# Audiobook production

Run from the repository root. Requires Python 3.12+, NumPy and FFmpeg with libmp3lame. CI exercises the pipeline offline with synthetic audio and Episode 1's historical transcript fixtures.

```sh
python -m pip install 'numpy>=1.26,<3'
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tools/audiobook -v
```

## Freeze and render

Hex freezes the script as ordered spoken cues in `script.json` and checks them against the manuscript. Use one cue per complete speaking turn and one approved, unprocessed take per cue. The tools compare against the complete structured script in cue order. Keep rejected entries and audio for provenance. For pickups, preserve the cue ID and add a new take ID and hash. Use local assets and put the actual durations and SHA-256 digests in the manifest. The examples contain placeholder paths and hashes. Set `proper_names: true` on cues with names that require listening review.

```sh
python -m pip install faster-whisper
python tools/audiobook/pipeline.py validate /work/manifest.json
python tools/audiobook/pipeline.py build /work/manifest.json /work/soundscape.json /work/render-v1 /work/cache \
  --asr 'python tools/audiobook/whisper_adapter.py --model /models/immutable-whisper-snapshot'
```

A custom ASR command receives one WAV path as its last argument and must emit JSON with literal `text`, `confidence` in [0, 1], and `words` containing `text`, `start` and `end` in seconds. Keep secrets in the environment and pin the tool, model and adapter version for reproducible QA. Use a new cache namespace after changing model files in place. The cache reuses eligible responses only for the same decoded audio and adapter identity. Use a fresh cache for an independent ASR run. Use the same FFmpeg and encoder versions to reproduce output bytes.

Dex uses the report to request only necessary pickups. Critical findings block release. Review flags include short takes, marked proper names, questionable boundaries and uncertain ASR. Listen in context and use an independent ASR when helpful. Preserve intentional repeats. The renderer uses complete takes and avoids automated trims or reinterpretation of script text. A take over 180 seconds must be split at a complete cue boundary before build so the full-mix ASR segments can include every word. There is no paid generation or automatic provider retry.

The build writes 24 kHz mono lossless voice, environment, foley and mix WAVs, a sample-aligned cue timeline, a hash-named full MP3 preview and chapter excerpts. It checks dry audio and decoded MP3 separately, reports peak, clipping, RMS and integrated loudness, and writes raw ASR, timings and flags in `qa.json` plus a summary in `qa.txt`. Check the timeline for cue boundaries and review intelligibility with sound effects on. A blocked or interrupted output directory is diagnostic only. Resolve the cause and use a fresh output directory on retry.

## Review and release

Dex listens to flagged passages and representative joins, ideally the full master, and records what was heard and how each review flag was resolved. If listening is unavailable, record that limitation and leave release blocked. Ninja must explicitly approve the exact preview SHA-256. Complete the approval record with the reference to that approval and the SHA-256 of the unchanged QA report. Review flag indexes are zero-based in the report. Keep the record and receipt somewhere private and durable.

From a clean, separate release branch based on current main, the intended reboot episode must have its own catalog row and Ohana metadata. Review any required new episode setup separately from the audio release. Pass a full checkout and keep the run artifacts and receipt outside it.

```sh
python tools/audiobook/release.py stage /work/release-checkout /work/render-v1 /work/approval.json /work/receipt.json
```

Review the release commit before moving it into main. Its changes are limited to one versioned audio asset, one catalog row and the matching audio metadata. If the branch has fallen behind, stage again from current main and recheck that only the intended paths changed. Publish the three changes in one commit or PR merge. The tool stages locally and does not push or publish.

Fetch main after deployment. Open the correct episode in Ohana and capture the resolved audio from the player as well as an independent download of the public asset. Keep an operator readback JSON with `episode_id`, `series_id`, `catalog_audio`, `public_url`, `ohana_url`, `public_download` and `ohana_download`. The last two values are paths to the downloaded bytes. Avoid committing signed URLs or local readback records.

```sh
git -C /work/release-checkout fetch origin main
python tools/audiobook/release.py verify /work/release-checkout /work/receipt.json /work/readback.json
```

Treat verification failure as an incomplete release. Check the app deployment and catalog cache before retrying with fresh downloads. To roll back, use a clean working branch based on current main and run the following. Review and publish that rollback commit atomically and recheck the restored episode in Ohana. Prior assets are retained. Delete an old asset only after checking current catalog references and getting owner approval.

```sh
python tools/audiobook/release.py rollback /work/release-checkout /work/receipt.json
```
