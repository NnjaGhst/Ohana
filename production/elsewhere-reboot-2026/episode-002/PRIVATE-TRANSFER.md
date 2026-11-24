# Private HeyGen WAV transfer

This workflow covers Episode 2, `elsewhere-reboot-2026-002`. A verified transfer confirms the bytes are readable and recoverable. The cast audition, context listening and episode QA still apply.

## Hex records and hands off

1. Finish the pending performance audition. Record a small batch from the frozen script and selected cast. Keep one stable cue ID and a unique take ID for each recording. A replacement gets a new take ID.
2. Save a new private JSON index per batch. Include the episode ID and a `takes` array with `cue_id`, `take_id`, `url` and `duration_seconds` for each take. Retain voice, speed and other production metadata alongside these fields. Use the original HeyGen TTS WAV URL. Include `sha256` when a digest from a prior verified retrieval exists. For a new take without a provider digest, use null.
3. Retain the index privately with its SHA-256 and exact backing file reference. Hand Dex that version and the cue range in the checkpoint. Keep the index, HeyGen URLs, and WAV files out of GitHub.

## Dex verifies and hands back

From this production checkout, with Python 3.11+ and FFmpeg installed:

```sh
umask 077
python production/elsewhere-reboot-2026/episode-002/ingest_heygen.py \
  "$PRIVATE/index.json" "$PRIVATE/takes" > "$PRIVATE/transfer-receipt.jsonl"
```

- Reopen the pinned private index and compare its checksum before running. The helper handles multiple takes in order. It restricts retrieval to the verified HeyGen TTS host, checks the WAV header, decodes the full file, checks duration and verifies any supplied SHA-256. It logs safe per-take receipts without source URLs. Exit 1 means one or more takes were blocked. Keep successful takes and retry just the blocked ones.
- For new takes, pin the first retrieved SHA-256 and run a second retrieval with that value in the private index. This establishes a reproducible digest when HeyGen supplies none. Retain the receipts privately.
- Retain the unmodified WAVs in the project's private files. Retrieve the exact stored versions again and check their SHA-256 and byte counts. Save the filename, cue ID, take ID, durable private file ID, backing file ID, version, bytes, duration and SHA-256 in the checkpoint. Save or retrieve files in groups of at most 20 per transfer. Never record a take as transferred until its private round trip has passed.
- If a URL has expired or comes from another host, ask Hex to re-export the same take or supply the original private WAV. Compare it with the pinned digest if one exists. For different bytes, stop and resolve the identity before replacing anything. Do not automatically regenerate a take.
- Update `STATE.json` against the latest branch head, and hand Hex the new checkpoint commit. Resume recording after confirming the first small batch. Transfer verification does not close a cue for QA or grant release approval.

Pilot: `s01-c001-t01` matched a fresh source retrieval and a version-pinned private retrieval. A second row with an intentionally invalid digest was rejected during the batch test. The pilot tested one real recording. The second test row was only a checksum failure check.
