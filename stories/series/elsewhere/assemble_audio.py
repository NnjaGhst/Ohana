#!/usr/bin/env python3
"""Assemble an Elsewhere episode from the audio manifest in Ohana's GitHub repo."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener, urlopen


REPO = "NnjaGhst/Ohana"
SERIES_PATH = "stories/series/elsewhere"
WADE = "a3a4aa242aaf431ea8ec2c4b3bb332fa"
HALLIE = "8f71d16d1fa84544a4db41a18264d613"
VOICES = dict(narrator=WADE, hex=WADE, ninja=HALLIE, other=WADE)
SAMPLE_RATE = 44100
FRAME_BYTES = 4  # 16-bit stereo PCM
MAX_CLIP_BYTES = 50 * 1024 * 1024
MAX_MASTER_BYTES = 25 * 1024 * 1024


def validate_url(url):
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.username or parts.password
            or parts.port is not None or parts.query or parts.fragment
            or not parts.hostname
            or not parts.hostname.endswith((".heygen.ai", ".heygen.com"))
            or not parts.path.lower().endswith(".mp3")):
        raise ValueError("Use a direct, unsigned HTTPS HeyGen MP3 URL.")


class HeyGenRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def validate_manifest(manifest, episode_id):
    if manifest.get("version") != 1 or manifest.get("episodeId") != episode_id:
        raise ValueError("Manifest version or episode ID does not match.")
    if manifest.get("script") != "script.md":
        raise ValueError("Manifest must reference its episode's script.md.")
    if manifest.get("status") != "ready":
        raise ValueError("Manifest is awaiting complete, verified audio sources.")
    segments = manifest.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError("Ready manifests require clips in playback order.")

    for index, clip in enumerate(segments, 1):
        speaker = clip.get("speaker")
        if speaker not in VOICES or clip.get("voiceId") != VOICES[speaker]:
            raise ValueError(f"Clip {index} has an incorrect or missing voice.")
        if not isinstance(clip.get("label"), str) or not clip["label"].strip():
            raise ValueError(f"Clip {index} needs a script cue or label.")
        if type(clip.get("gainApplied", False)) is not bool:
            raise ValueError(f"Clip {index} gainApplied must be a boolean.")
        url = clip.get("url")
        if not isinstance(url, str):
            raise ValueError(f"Clip {index} has no source URL.")
        validate_url(url)
        for path_segment in urlsplit(url).path.split("/"):
            if path_segment in (WADE, HALLIE) and path_segment != clip["voiceId"]:
                raise ValueError(f"Clip {index} URL names a different voice.")
    return segments


def download(url, target):
    validate_url(url)
    opener = build_opener(HeyGenRedirectHandler)
    with opener.open(url, timeout=30) as source, target.open("wb") as output:
        total = 0
        while chunk := source.read(256 * 1024):
            total += len(chunk)
            if total > MAX_CLIP_BYTES:
                raise ValueError("HeyGen source exceeds the clip size limit.")
            output.write(chunk)


def run(*command, **options):
    return subprocess.run(command, check=True, **options)


def build_episode(segments, output):
    if output.suffix.lower() != ".mp3" or output.exists():
        raise ValueError("Choose a new MP3 output path.")
    with tempfile.TemporaryDirectory(dir=output.parent) as directory:
        folder = Path(directory)
        pcm = folder / "episode.pcm"
        with pcm.open("wb") as joined:
            for index, clip in enumerate(segments, 1):
                source = folder / "source.mp3"
                try:
                    download(clip["url"], source)
                    codec = run(
                        "ffprobe", "-v", "error", "-select_streams", "a:0",
                        "-show_entries", "stream=codec_name",
                        "-of", "default=nw=1:nk=1", str(source),
                        capture_output=True, text=True,
                    ).stdout.strip()
                    if codec != "mp3":
                        raise ValueError("Source is not MP3 audio.")
                    gain = "0.9" if clip["speaker"] == "ninja" and not clip.get("gainApplied") else "1"
                    start = joined.tell()
                    run(
                        "ffmpeg", "-nostdin", "-v", "error", "-xerror",
                        "-i", str(source), "-map", "0:a:0", "-af", f"volume={gain}",
                        "-ar", str(SAMPLE_RATE), "-ac", "2", "-f", "s16le", "-",
                        stdout=joined,
                    )
                    if joined.tell() == start:
                        raise ValueError("Source decoded to empty audio.")
                except (OSError, ValueError, subprocess.CalledProcessError) as error:
                    raise ValueError(f"Clip {index} failed ({clip['label']}): {error}") from error
        master = folder / "master.mp3"
        run(
            "ffmpeg", "-nostdin", "-v", "error", "-xerror",
            "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "2", "-i", str(pcm),
            "-c:a", "libmp3lame", "-b:a", "96k", str(master),
        )
        run("ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i", str(master), "-f", "null", "-")
        if master.stat().st_size > MAX_MASTER_BYTES:
            raise ValueError("Master exceeds the Ohana upload limit of 25 MiB.")
        duration = pcm.stat().st_size / (FRAME_BYTES * SAMPLE_RATE)
        output.hardlink_to(master)
        return duration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("episode", type=int, choices=range(1, 1000), metavar="EPISODE")
    parser.add_argument("output", type=Path, help="New MP3 file")
    args = parser.parse_args()
    number = f"{args.episode:03d}"
    episode_id = f"elsewhere-{number}"
    try:
        with urlopen(f"https://api.github.com/repos/{REPO}/commits/main", timeout=20) as response:
            sha = json.load(response)["sha"]
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("GitHub returned an invalid commit SHA.")
        folder = f"https://raw.githubusercontent.com/{REPO}/{sha}/{SERIES_PATH}/episodes/episode-{number}"
        with urlopen(f"{folder}/audio-manifest.json", timeout=20) as response:
            segments = validate_manifest(json.load(response), episode_id)
        with urlopen(f"{folder}/script.md", timeout=20) as response:
            if not response.read().decode("utf-8").strip():
                raise ValueError("The episode script is empty.")
        duration = build_episode(segments, args.output)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"{episode_id}: {error}\n")
    print(f"{args.output} · {duration:.2f}s · manifest revision {sha}")


if __name__ == "__main__":
    main()
