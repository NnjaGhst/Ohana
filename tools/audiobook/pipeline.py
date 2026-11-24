"""Offline audiobook validation, rendering and QA."""
import argparse
import difflib
import hashlib
import json
import math
import re
import shlex
import subprocess
import tempfile
import unicodedata
from pathlib import Path

import numpy as np

RATE = 24000
WADE = "a3a4aa242aaf431ea8ec2c4b3bb332fa"
HALLIE = "8f71d16d1fa84544a4db41a18264d613"
DIGITS = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def identifier(value):
    return isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", value)


def asset(root, entry):
    name = entry["file"]
    path = (root / name).resolve()
    require(path.is_relative_to(root.resolve()) and not Path(name).is_absolute()
            and ":" not in name and "?" not in name, "Use a local asset under the manifest directory")
    require(path.is_file() and sha(path) == entry["sha256"], "Missing asset or checksum mismatch")
    return path


def load(manifest):
    data = read(manifest)
    root = manifest.parent
    script = read(asset(root, data["script"]))
    require(identifier(script["episode_id"]) and identifier(script["series_id"]), "Invalid episode identity")
    cues = script["cues"]
    ids = [cue["id"] for cue in cues]
    require(ids and all(map(identifier, ids)) and len(set(ids)) == len(ids), "Invalid or duplicate cue IDs")
    require([cue["order"] for cue in cues] == list(range(1, len(ids) + 1)), "Invalid cue order")
    approved = {}
    take_ids = set()
    for take in data["takes"]:
        key = take["id"]
        cue_id = take["cue_id"]
        require(identifier(key) and key not in take_ids and cue_id in ids, "Invalid or duplicate take")
        take_ids.add(key)
        require(take["status"] in {"approved", "rejected"}, "Invalid take status")
        if take["status"] == "approved":
            require(cue_id not in approved, "Multiple approved takes for " + cue_id)
            approved[cue_id] = take
    require(set(ids) == set(approved), "Missing approved take")
    seen_scenes = set()
    previous = None
    for cue in cues:
        key = cue["id"]
        scene, speaker, role = (cue[field] for field in ("scene", "speaker", "role"))
        require(identifier(scene) and role in {"narration", "dialogue", "thought"}
                and normalize(cue["text"]), "Invalid cue " + key)
        if scene != previous:
            require(scene not in seen_scenes, "Scene cues must be contiguous")
            seen_scenes.add(scene)
            previous = scene
        voice = script["cast"][speaker]
        require(identifier(voice) and voice == {"Ninja": HALLIE, "Hex": WADE}.get(speaker, voice)
                and approved[key]["voice_id"] == voice, "Voice mismatch for " + key)
        default = 0.80 if role == "thought" and speaker in {"Ninja", "Hex"} else (
            0.90 if voice == HALLIE and role == "dialogue" else 1.0)
        fixed_gain = (voice == HALLIE and role == "dialogue") or (
            role == "thought" and speaker in {"Hex", "Ninja"})
        gain = cue.get("gain", default)
        require(0 < gain <= 1 and (not fixed_gain or gain == default), "Invalid gain for " + key)
        cue["gain"] = gain
        require(approved[key].get("pitch", 1) == approved[key].get("speed", 1) == 1,
                "Pitch and speed must both be natural")
        require(0 < approved[key]["duration"] <= 480, "Invalid take duration")
        asset(root, approved[key])
    return script, approved


def normalize(value):
    value = unicodedata.normalize("NFKC", value).casefold().replace("’", "'")
    words = re.findall(r"[^\W_]+(?:'[^\W_]+)*", value)
    return [DIGITS[int(word)] if word.isascii() and word.isdecimal() and len(word) < 3
            and int(word) < len(DIGITS) and str(int(word)) == word else word for word in words]


def ffmpeg(*args):
    proc = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", *map(str, args)],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    require(proc.returncode == 0, "FFmpeg could not decode or write audio")
    return proc.stdout


def decode(path):
    return np.frombuffer(ffmpeg("-xerror", "-i", path, "-vn", "-ac", 1, "-ar", RATE,
                                "-f", "f32le", "pipe:1"), dtype="<f4")


def encode(path, samples):
    codec = ["-c:a", "libmp3lame", "-b:a", "128k"] if path.suffix == ".mp3" else ["-c:a", "pcm_f32le"]
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-n", "-f", "f32le", "-ar", str(RATE),
                    "-ac", "1", "-i", "pipe:0", "-map_metadata", "-1", *codec, str(path)],
                   input=np.asarray(samples, dtype="<f4").tobytes(), check=True)


def transcribe(command, cache, samples, temp):
    digest = hashlib.sha256(np.asarray(samples, dtype="<f4").tobytes()).hexdigest()
    argv = shlex.split(command)
    fingerprints = [sha(Path(arg)) for arg in argv if Path(arg).is_file()]
    key = hashlib.sha256(json.dumps([RATE, argv, fingerprints, digest]).encode()).hexdigest()
    cached = cache / (key + ".json")
    if cached.exists():
        result = read(cached)
        require(result["key"] == key, "ASR cache key mismatch")
        return result["asr"]
    wav = temp / "asr.wav"
    wav.unlink(missing_ok=True)
    encode(wav, samples)
    proc = subprocess.run([*argv, str(wav)], capture_output=True)
    require(proc.returncode == 0, "ASR adapter failed")
    result = json.loads(proc.stdout)
    require(isinstance(result["text"], str) and isinstance(result["words"], list)
            and 0 <= result["confidence"] <= 1, "Invalid ASR response")
    for word in result["words"]:
        require(isinstance(word["text"], str) and 0 <= word["start"] <= word["end"]
                <= len(samples) / RATE + 0.1, "Invalid ASR word timing")
    if result["text"].strip() and result["words"] and result["confidence"] >= 0.80:
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as stream:
            staged = Path(stream.name)
        try:
            write(staged, {"key": key, "asr": result})
            staged.replace(cached)
        finally:
            staged.unlink(missing_ok=True)
    return result


def content_flags(expected, result, *, short=False, proper_names=False):
    actual = normalize(result["text"])
    if not actual:
        return [("critical", "Blank ASR result")]
    if actual != normalize(expected):
        return [("critical", "Unexpected, repeated or missing words")]
    flags = []
    if result["confidence"] < 0.80 or not result["words"]:
        flags.append(("review", "Uncertain ASR"))
    if short or proper_names:
        flags.append(("review", "Short utterance or proper name"))
    return flags


def metrics(samples):
    require(len(samples) and np.isfinite(samples).all(), "Invalid PCM")
    peak = float(np.max(np.abs(samples)))
    rms = math.sqrt(float(np.mean(np.square(samples, dtype=np.float64))))
    return {"peak_dbfs": 20 * math.log10(max(peak, 1e-12)),
            "rms_dbfs": 20 * math.log10(max(rms, 1e-12)),
            "clipped_samples": int(np.count_nonzero(np.abs(samples) >= 0.9999))}


def loudness(path):
    proc = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(path), "-af",
                           "loudnorm=print_format=json", "-f", "null", "-"], capture_output=True, check=True)
    stats, _ = json.JSONDecoder().raw_decode(proc.stderr[proc.stderr.rindex(b"{"):].decode())
    return {key: float(stats[field]) if math.isfinite(float(stats[field])) else None
            for key, field in (("integrated_lufs", "input_i"), ("true_peak_dbtp", "input_tp"))}


def add_flags(report, scope, cue, seconds, expected, actual, flags):
    for severity, failure in flags:
        report["flags"].append(dict(scope=scope, cue_id=cue, seconds=seconds, expected=expected,
                                    actual=actual, severity=severity, failure=failure, disposition="open"))


def chapter_findings(report, scope, rows, result):
    start = rows[0]["start"]
    expected = " ".join(row["text"] for row in rows)
    flags = content_flags(expected, result)
    if not flags or flags[0][1] != "Unexpected, repeated or missing words":
        add_flags(report, scope, rows[0]["id"], start / RATE, expected, result["text"], flags)
        return
    aligned = [(word, row) for row in rows for word in normalize(row["text"])]
    observed = normalize(result["text"])
    timed = [(word, item["start"]) for item in result["words"] for word in normalize(item["text"])]
    for operation, left, right, actual_left, actual_right in difflib.SequenceMatcher(
            None, [word for word, _ in aligned], observed, autojunk=False).get_opcodes():
        if operation == "equal":
            continue
        cue = aligned[min(left, len(aligned) - 1)][1]
        time = cue["start"] / RATE
        if actual_left < len(timed) and [word for word, _ in timed] == observed:
            time = start / RATE + timed[actual_left][1]
        add_flags(report, scope, cue["id"], time,
                  " ".join(word for word, _ in aligned[left:right]),
                  " ".join(observed[actual_left:actual_right]), flags)


def soundscape(root, config, timeline, size):
    stems = {name: np.zeros(size, dtype=np.float32) for name in ("environment", "foley")}
    by_id = {cue["id"]: cue for cue in timeline}
    scene_ranges = {}
    for cue in timeline:
        scene_ranges.setdefault(cue["scene"], [cue["start"], cue["end"]])[1] = cue["end"]
    for event in config["events"]:
        stem = stems[event["stem"]]
        cue = by_id[event["cue_id"]]
        require(event["rights"] in {"original", "licensed"} and event["speech_free"] is True,
                "Effect needs approved rights and speech-free confirmation")
        group_start, group_end = scene_ranges[cue["scene"]]
        start = cue["start"] + round(RATE * event.get("offset_seconds", 0))
        count = round(RATE * event["duration"])
        gain = event["gain"]
        require(group_start <= start < start + count <= group_end and 0 <= gain <= 0.25,
                "Effect out of scene bounds or gain range")
        samples = decode(asset(root, event))
        require(len(samples), "Empty sound effect")
        if event["stem"] == "environment":
            samples = np.resize(samples, count)
        else:
            samples = np.pad(samples[:count], (0, max(0, count - len(samples))))
        # Fade only effects. Voice takes remain intact.
        fade = min(RATE // 50, count // 2)
        if fade:
            ramp = np.linspace(0, 1, fade, dtype=np.float32)
            samples[:fade] *= ramp
            samples[-fade:] *= ramp[::-1]
        stem[start:start + count] += samples * gain
    return stems


def summary(out, report):
    write(out / "qa.json", report)
    lines = [f"QA status: {report['status']}", "Human listening review still required."]
    lines += [f"{item['seconds']:.3f}s {item['cue_id']} {item['severity']}: {item['failure']}"
              for item in report["flags"]]
    (out / "qa.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(manifest, config_path, command, out, cache):
    script, takes = load(manifest)
    config = read(config_path)
    report = {"status": "blocked", "flags": [], "transcripts": [], "measurements": {}}
    out.mkdir(parents=True)
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        temp = Path(scratch)
        parts, timeline = [], []
        position = 0
        previous = None
        for cue in script["cues"]:
            key = cue["id"]
            take = takes[key]
            samples = decode(asset(manifest.parent, take))
            measure = metrics(samples)
            flags = []
            if abs(len(samples) / RATE - take["duration"]) > 0.05:
                flags.append(("critical", "Take duration mismatch"))
            if measure["peak_dbfs"] < -50 or measure["clipped_samples"]:
                flags.append(("critical", "Silent or clipped take"))
            edge = max(1, RATE // 100)
            if max(metrics(samples[:edge])["rms_dbfs"], metrics(samples[-edge:])["rms_dbfs"]) > -38:
                flags.append(("review", "Speech may start or end abruptly"))
            result = transcribe(command, cache, samples, temp)
            flags += content_flags(cue["text"], result, short=len(samples) < RATE or len(normalize(cue["text"])) <= 2,
                                   proper_names=cue.get("proper_names", False))
            gap = 0 if previous is None else (
                0.65 if cue["scene"] != previous["scene"] else
                0.35 if cue["speaker"] != previous["speaker"] else 0.18)
            count = round(gap * RATE)
            position += count
            row = {**cue, "take_id": take["id"], "source_sha256": take["sha256"],
                   "start": position, "end": position + len(samples)}
            add_flags(report, "take", key, position / RATE, cue["text"], result["text"], flags)
            report["transcripts"].append({"scope": "take", "cue_id": key, "asr": result})
            report["measurements"][key] = measure
            timeline.append(row)
            parts.extend([np.zeros(count, dtype=np.float32), samples * cue["gain"]])
            position = row["end"]
            previous = cue
        if any(flag["severity"] == "critical" for flag in report["flags"]):
            summary(out, report)
            return False
        voice = np.concatenate(parts)
        stems = soundscape(config_path.parent, config, timeline, len(voice))
        full = voice + stems["environment"] + stems["foley"]
        for name, samples in {"voice": voice, **stems, "mix": full}.items():
            encode(out / (name + ".wav"), samples)
            report["measurements"][name] = metrics(samples)
        master = out / "master.mp3"
        encode(master, full)
        final = decode(master)
        report["measurements"]["mp3"] = metrics(final)
        for name, path in (("voice", out / "voice.wav"), ("mix", out / "mix.wav"), ("mp3", master)):
            report["measurements"][name].update(loudness(path))
        if len(final) != len(full):
            add_flags(report, "mix", timeline[-1]["id"], len(final) / RATE, "", "",
                      [("critical", "Encoded master duration differs from timeline")])
        for name in ("voice", "mix", "mp3"):
            level = report["measurements"][name]
            if level["peak_dbfs"] > -1 or (level["true_peak_dbtp"] is not None
                                           and level["true_peak_dbtp"] > -1):
                add_flags(report, name, timeline[0]["id"], 0, "", "",
                          [("critical", "Insufficient peak headroom")])
        chapters = []
        for row in timeline:
            if not chapters or chapters[-1][-1]["scene"] != row["scene"] or (
                    row["end"] - chapters[-1][0]["start"] > 180 * RATE):
                chapters.append([])
            require(row["end"] - row["start"] <= 180 * RATE, "Take exceeds final ASR segment limit")
            chapters[-1].append(row)
        digest = sha(master)
        for number, rows in enumerate(chapters, 1):
            start, end = rows[0]["start"], rows[-1]["end"]
            for name, samples in (("voice", voice), ("mix", final)):
                result = transcribe(command, cache, samples[start:end], temp)
                report["transcripts"].append({"scope": name, "start": start, "end": end, "asr": result})
                chapter_findings(report, name, rows, result)
            encode(out / f"scene-{number:03d}-{digest[:16]}.mp3", final[start:min(end, start + 25 * RATE)])
        preview = out / f"preview-{digest}.mp3"
        master.rename(preview)
        write(out / "timeline.json", {"rate": RATE, "samples": len(full), "cues": timeline})
        report.update(status="blocked" if any(f["severity"] == "critical" for f in report["flags"])
                      else "listening_required", episode_id=script["episode_id"], series_id=script["series_id"],
                      master=preview.name, master_sha256=digest, manifest_sha256=sha(manifest),
                      script_sha256=read(manifest)["script"]["sha256"], soundscape_sha256=sha(config_path),
                      artifacts={file.name: sha(file) for file in sorted(out.iterdir())})
        summary(out, report)
        return report["status"] != "blocked"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="stage", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("manifest", type=Path)
    build = sub.add_parser("build")
    for field in ("manifest", "soundscape", "output", "cache"):
        build.add_argument(field, type=Path)
    build.add_argument("--asr", required=True, help="Versioned ASR adapter command")
    args = parser.parse_args()
    try:
        if args.stage == "validate":
            load(args.manifest)
            print("Manifest, cast and source hashes validated")
            return 0
        return 0 if run(args.manifest, args.soundscape, args.asr, args.output, args.cache) else 1
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Production blocked ({type(error).__name__}): {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
