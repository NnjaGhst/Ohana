# Audiobook production and QA

Use this workflow from a feature checkout. Install Python 3.11+, NumPy 2.3.5, FFmpeg with libmp3lame, and Git. The suite uses synthetic audio and a temporary Git remote.

```sh
python -m pip install 'numpy==2.3.5'
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tools/audiobook -v
```

## Freeze, record and assemble

Hex prepares the manuscript, cast, takes and creative assets. Copy the three JSON templates from `examples/` into a production workspace outside tracked files. `script.json` is the frozen spoken manuscript, one record per complete turn, in order. Fill the manifest with the same cue fields, the manuscript hash, source hashes and durations. Use distinct take IDs and retain superseded source files marked `rejected`. Store raw recordings outside the repo or in access-controlled storage, with only local relative asset paths in the input. Use `sha256sum FILE` to populate hashes. Use `proper_names: true` on cues that contain names requiring confirmation.

Cue gains are linear and apply once. Hallie dialogue is 0.90, Ninja and Hex thoughts are 0.80, and default gain is 1.00. Use original pitch and speed. The gap override is the pause **after** that cue, in milliseconds. Defaults are 240 ms between same-speaker turns, 425 ms for a voice change and 650 ms for a scene change. Submit complete takes. The pipeline has no trim mode. Request a clean pickup for a defective take.

Each soundscape event uses a cue anchor, a relative offset and a fixed duration in milliseconds. Mark each effect cleared and free of intelligible voices after listening. Use `{"sounds":[]}` for a dry-only draft. For long room tone, use a clean loopable source. Stems and previews use 48 kHz PCM. Retain the inputs and configuration with the versioned QA and output hashes. Keep the verified originals for future pickups. Preview bundles include full-length lossless WAVs, so allow disk space for roughly 92 MB per minute of output. Use the same FFmpeg build for reproducible output hashes.

```sh
python -m pip install faster-whisper
python tools/audiobook/pipeline.py validate work/manifest.json
python tools/audiobook/pipeline.py produce work/manifest.json work/soundscape.json \
  work/asr.json work/previews work/asr-cache
```

Set `work/asr.json` to an executable argument array with a cache namespace. For example, `{"id":"local-small-en-v1","command":["python","tools/audiobook/whisper_adapter.py","--model","small.en"]}`. Pin the actual package/model versions for production and change the adapter ID with any model, configuration or program change. An adapter receives a lossless WAV path as its last argument. It emits JSON with `text`, `confidence` in [0,1], and ordered `segments` containing `start` and `end` in seconds and raw `text`. Supply credentials through your environment if needed. The local cache keeps checksum-verified source takes and reuses ASR results only for identical audio, adapter ID and command. Keep this cache private.

Read `qa.txt` for the flagged cue and timestamp, and `qa.json` for all expected words, raw ASR/timings, disposition and hashes. Chapter ASR runs on both voice-only audio and the decoded MP3. Listen to flagged passages in context and to representative joins at minimum. Preview WAVs contain up to 25 seconds from each chapter's beginning. The full MP3 is in the same version directory. Automated checks cover cue ordering, timing, silence, peaks, headroom and transcript agreement. The listening review covers intelligibility, natural boundaries, transitions and background suitability. No listening review is inferred from ASR.

For a short utterance, proper name, uncertain output or suspected ASR disagreement, supply `--reviews work/reviews.json` with records keyed to the QA row. Confirm with a second adapter when useful and record its result in the notes. Example review record:

```json
{
  "stage": "raw",
  "cue_id": "s01-c01",
  "audio_sha256": "<row audio_sha256>",
  "expected": "Why?",
  "transcript": "Why?",
  "listened_in_context": true,
  "reviewer": "Dex",
  "notes": "One complete utterance heard, including the final sound."
}
```

Use a JSON array of these records. A blank ASR result needs another transcription run. Unexpected repeats need a pickup. Change the take ID and checksum for a replacement. Run `produce` again to recalculate the timeline, stems, and final QA. Exit 2 means stop and inspect the error or report. A versioned draft with unresolved flags remains blocked from release.

## Owner approval and release

Episode production starts after the tooling PR review and acceptance checks. Ninja approves the audio and scene treatment using the versioned preview. Record the exact SHA-256 of `preview.mp3`, the SHA-256 of `qa.json`, and Ninja's approval reference. A release approval file has these keys:

```json
{
  "owner": "NnjaGhst",
  "owner_approval_reference": "<link or reference to explicit approval of this SHA>",
  "episode_id": "elsewhere-reboot-2026-002",
  "qa_sha256": "<qa.json SHA-256>",
  "master_sha256": "<preview.mp3 SHA-256>",
  "listening": {
    "reviewer": "Dex",
    "scope": "full_master",
    "notes": "<actual listening coverage and findings>",
    "passed": true
  }
}
```

Use `flagged_and_representative_joins` if that is the actual listening coverage and record its limitations. If listening is unavailable, report that and stop before release.

```sh
python tools/audiobook/release.py stage "$PREVIEW" work/approval.json work/release-plan.json --master "$SHA"
```

For a new episode with creative assets already in main, pass `--new-episode work/new-episode.json` containing the intended `entry` and `metadata` objects in existing Ohana format with audio unset. Stage creates a release branch with a single commit off the current remote main, recording the prior catalog audio in the rollback plan. Inspect the branch before publishing. Save the approval and plan. They are local operator records.

Configure `work/readback.json` as the executable argument array for your Ohana readback adapter. The command receives the catalog episode ID and returns the actually loaded player's episode entry as `{"id":"...","seriesId":"...","audioPath":"...","audioUrl":"..."}`. The HTTPS URL must be the one returned by the live player. Use an authenticated integration if required. Keep command credentials in the environment. The tool blocks verification when no live readback is available.

```sh
python tools/audiobook/release.py publish work/release-plan.json --preview "$PREVIEW" \
  --approval work/approval.json --master "$SHA" --public-url "$URL" --readback work/readback.json
```

On failure, inspect main first. If the push succeeded but the player is stale, inspect cache/version and retry the read-only `verify` command with the plan, public URL and readback. Report release success only once all checks pass. If the staged commit is still main's head, `rollback work/release-plan.json --master "$SHA"` restores the prior catalog and metadata by a new commit while retaining both audio assets. Recheck the live player after rollback. If main advanced, inspect the differences and prepare a targeted revert that preserves subsequent changes.
