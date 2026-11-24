import copy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

import pipeline as p
import release


def asr(text, confidence=0.98):
    return {"text": text, "confidence": confidence,
            "words": [{"text": word, "start": 0.1, "end": 0.2} for word in text.split()]}


class ProductionTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.script = self.root / "script.json"
        self.manifest = self.root / "manifest.json"
        self.sound = self.root / "sound.json"
        self.cache = self.root / "cache"
        self.lines = ["Listen to the quiet rain.", "Stay here with me."]
        data = {"episode_id": "elsewhere-reboot-2026-002", "series_id": release.SERIES,
                "cast": {"Hex": p.WADE, "Ninja": p.HALLIE},
                "cues": [{"id": "line-1", "order": 1, "scene": "outside", "speaker": "Hex",
                          "role": "narration", "text": self.lines[0]},
                         {"id": "line-2", "order": 2, "scene": "outside", "speaker": "Ninja",
                          "role": "dialogue", "text": self.lines[1]}]}
        p.write(self.script, data)
        self.audio(self.root / "wade.wav", 200)
        self.audio(self.root / "hallie.wav", 300)
        self.audio(self.root / "rain.wav", 500)
        self.takes = {"script": self.ref(self.script), "takes": [
            {"id": f"take-{i}", "cue_id": f"line-{i}", "voice_id": voice,
             "status": "approved", "duration": 1, **self.ref(self.root / file)}
            for i, (voice, file) in enumerate([(p.WADE, "wade.wav"), (p.HALLIE, "hallie.wav")], 1)]}
        p.write(self.manifest, self.takes)
        p.write(self.sound, {"events": [
            {"cue_id": "line-2", "stem": "environment", "duration": 0.7, "gain": 0.015,
             "rights": "original", "speech_free": True, **self.ref(self.root / "rain.wav")}]})
        adapter = self.root / "adapter.py"
        adapter.write_text(
            "import json, sys\n"
            "from pathlib import Path\n"
            "duration = Path(sys.argv[-1]).stat().st_size\n"
            f"lines = {self.lines!r}\n"
            "import struct\n"
            "wav = Path(sys.argv[-1]).read_bytes()\n"
            "offset = wav.index(b'data') + 8\n"
            "samples = struct.unpack('<' + str((len(wav)-offset)//4) + 'f', wav[offset:])\n"
            "text = ' '.join(lines) if duration > 120000 else lines[0 if samples[2500] > 0 else 1]\n"
            "print(json.dumps(dict(text=text, confidence=0.98, words=[dict(text=w,start=.1,end=.2) "
            "for w in text.split()])))\n"
        )
        self.command = f"{sys.executable} {adapter}"

    def ref(self, path):
        return {"file": path.name, "sha256": p.sha(path)}

    def audio(self, path, frequency):
        t = np.arange(p.RATE) / p.RATE
        signal = (0.15 * np.sin(2 * np.pi * frequency * (t - 2500 / p.RATE) + np.pi / 2))
        if frequency == 300:
            signal *= -1
        signal[:2000] = signal[-2000:] = 0
        p.encode(path, signal)

    def build(self, name):
        dest = self.root / name
        self.assertTrue(p.run(self.manifest, self.sound, self.command, dest, self.cache))
        return dest

    def test_historical_repetition_and_clean_takes(self):
        fixtures = p.read(Path(__file__).with_name("episode1-regression.json"))
        for case in fixtures["cases"]:
            with self.subTest(cue=case["cue"]):
                self.assertEqual(p.content_flags(case["expected"], asr(case["rejected"]))[0][0], "critical")
                self.assertFalse(p.content_flags(case["expected"], asr(case["corrected"])))
        self.assertFalse(p.content_flags("Why, why?", asr("WHY WHY")))

    def test_blank_uncertain_short_and_final_word(self):
        self.assertEqual(p.content_flags("Why?", asr(""))[0][0], "critical")
        self.assertEqual(p.content_flags("Why?", asr("Why?", 0.40))[0][0], "review")
        self.assertEqual(p.content_flags("Why?", asr("Why?"), short=True)[0][0], "review")
        self.assertEqual(p.content_flags("The complete sentence ends here.", asr("The complete sentence ends"))[0][0],
                         "critical")
        self.assertFalse(p.content_flags("Take 2 books.", asr("Take two books")))

    def test_build_blocks_repeated_raw_take(self):
        adapter = self.root / "adapter.py"
        adapter.write_text(adapter.read_text().replace("text=text,", "text=text + ' rain rain',"))
        out = self.root / "bad-raw"
        self.assertFalse(p.run(self.manifest, self.sound, self.command, out, self.cache))
        report = p.read(out / "qa.json")
        self.assertEqual(report["status"], "blocked")
        self.assertTrue(all(flag["scope"] == "take" for flag in report["flags"]))
        self.assertFalse(list(out.glob("preview-*.mp3")))

    def test_final_mix_missing_word_blocks_release(self):
        adapter = self.root / "adapter.py"
        adapter.write_text(adapter.read_text().replace("' '.join(lines) if duration",
                                                       "' '.join(lines)[:-3] if duration"))
        out = self.root / "bad-mix"
        self.assertFalse(p.run(self.manifest, self.sound, self.command, out, self.cache))
        flags = p.read(out / "qa.json")["flags"]
        self.assertTrue(any(flag["scope"] == "mix" and flag["cue_id"] == "line-2"
                            and flag["expected"] == "me" and flag["severity"] == "critical"
                            for flag in flags))

    def test_missing_and_duplicate_cues_and_takes(self):
        amended = copy.deepcopy(self.takes)
        amended["takes"][1]["status"] = "rejected"
        p.write(self.manifest, amended)
        with self.assertRaisesRegex(ValueError, "Missing approved take"):
            p.load(self.manifest)
        amended = copy.deepcopy(self.takes)
        amended["takes"].append({**amended["takes"][0], "id": "duplicate"})
        p.write(self.manifest, amended)
        with self.assertRaisesRegex(ValueError, "Multiple approved takes"):
            p.load(self.manifest)
        script = p.read(self.script)
        script["cues"][1]["id"] = script["cues"][0]["id"]
        p.write(self.script, script)
        self.takes["script"] = self.ref(self.script)
        p.write(self.manifest, self.takes)
        with self.assertRaisesRegex(ValueError, "duplicate cue"):
            p.load(self.manifest)

    def test_render_is_repeatable_and_pickup_preserves_unrelated_voice(self):
        original = self.build("original")
        repeat = self.build("repeat")
        self.assertEqual(p.read(original / "qa.json")["artifacts"], p.read(repeat / "qa.json")["artifacts"])
        timeline = p.read(original / "timeline.json")
        first, second = timeline["cues"]
        dry = p.decode(original / "voice.wav")
        np.testing.assert_allclose(dry[second["start"]:second["end"]], p.decode(self.root / "hallie.wav") * 0.9)
        self.audio(self.root / "pickup.wav", 220)
        self.takes["takes"][0]["status"] = "rejected"
        self.takes["takes"].append({**self.takes["takes"][0], "id": "pickup", "status": "approved",
                                   **self.ref(self.root / "pickup.wav")})
        p.write(self.manifest, self.takes)
        pickup = self.build("pickup")
        new = p.decode(pickup / "voice.wav")
        self.assertEqual(len(new), len(dry))
        self.assertFalse(np.array_equal(new[:first["end"]], dry[:first["end"]]))
        np.testing.assert_array_equal(new[second["start"]:], dry[second["start"]:])
        self.audio(self.root / "rain2.wav", 520)
        config = p.read(self.sound)
        config["events"][0].update(self.ref(self.root / "rain2.wav"))
        p.write(self.sound, config)
        replacement = self.build("replacement")
        self.assertEqual(p.read(replacement / "timeline.json"), p.read(pickup / "timeline.json"))
        self.assertEqual(len(p.decode(replacement / "environment.wav")), len(dry))

    def test_release_approval_verification_and_rollback(self):
        out = self.build("release")
        repo = self.root / "site"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        release.git(repo, "config", "user.name", "QA")
        release.git(repo, "config", "user.email", "qa@example.invalid")
        report = p.read(out / "qa.json")
        episode, digest = report["episode_id"], report["master_sha256"]
        base, number = release.targets(episode)
        metadata = base / "ohana-metadata.json"
        old = base / "audio" / "old.mp3"
        (repo / "stories" / old).parent.mkdir(parents=True)
        (repo / "stories" / old).write_bytes(b"old")
        p.write(repo / "stories" / metadata, {"episode": number, "series": "Reboot", "audio_file": old.as_posix()})
        row = {"id": episode, "seriesId": release.SERIES, "number": number, "seriesName": "Reboot",
               "metadata": metadata.as_posix(), "audio": old.as_posix()}
        other = {"id": "elsewhere-002", "audio": "different.mp3"}
        p.write(repo / release.CATALOG, {"episodes": [row, other]})
        release.git(repo, "add", ".")
        release.git(repo, "commit", "-m", "Base catalog")
        release.git(repo, "switch", "-c", "release-test")
        approval, receipt_path = self.root / "approval.json", self.root / "receipt.json"
        record = dict(episode_id=episode, master_sha256=digest, qa_sha256=p.sha(out / "qa.json"),
                      owner="Ninja", approved=False, approval_reference="test",
                      reviewer="QA", listened_to_flagged_passages=True, listened_to_representative_joins=True,
                      review_notes="Synthetic test only", resolved_review_flags=list(range(len(report["flags"]))))
        p.write(approval, record)
        with self.assertRaisesRegex(ValueError, "Explicit owner approval"):
            release.stage(repo, out, approval, receipt_path)
        p.write(approval, {**record, "approved": True})
        release.stage(repo, out, approval, receipt_path)
        receipt = p.read(receipt_path)
        public = self.root / "public.mp3"
        ohana = self.root / "ohana.mp3"
        stale = self.root / "stale.mp3"
        for path in (public, ohana):
            path.write_bytes((out / report["master"]).read_bytes())
        stale.write_bytes(b"stale")
        observed = dict(episode_id=episode, series_id=release.SERIES, catalog_audio=receipt["audio"],
                        public_download=str(public), ohana_download=str(ohana),
                        public_url="https://example.invalid/audio", ohana_url="https://example.invalid/app")
        with self.assertRaisesRegex(ValueError, "Stale catalog"):
            release.verify(repo, receipt, observed, ref="main")
        with self.assertRaisesRegex(ValueError, "readback"):
            release.verify(repo, receipt, {**observed, "ohana_download": str(stale)}, ref="HEAD")
        with self.assertRaisesRegex(ValueError, "readback"):
            release.verify(repo, receipt, {**observed, "episode_id": "elsewhere-002"}, ref="HEAD")
        release.verify(repo, receipt, observed, ref="HEAD")
        release.rollback(repo, receipt)
        self.assertEqual(p.read(repo / release.CATALOG)["episodes"], [row, other])
        self.assertTrue((repo / "stories" / receipt["audio"]).exists())


if __name__ == "__main__":
    unittest.main()
