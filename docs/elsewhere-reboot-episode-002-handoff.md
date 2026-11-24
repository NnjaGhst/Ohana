# Elsewhere reboot Episode 2 handoff

Episode ID `elsewhere-reboot-2026-002` · **The Man in the Corner**

Open the [production workspace](https://github.com/NnjaGhst/Ohana/tree/production/elsewhere-reboot-2026-002/production/elsewhere-reboot-2026/episode-002) and read its [checkpoint](https://github.com/NnjaGhst/Ohana/blob/production/elsewhere-reboot-2026-002/production/elsewhere-reboot-2026/episode-002/STATE.json) before working in either chat. That checkpoint is the record of the current phase, the next owner, the next action, and the exact revisions and files to use. The workspace begins with the pinned Draft 3 link, a dry-only soundscape, and a release block.

## Start or resume in a new chat

1. Tell the chat to resume Episode 2 from the checkpoint link above. Read the latest branch head and `STATE.json`. Resolve the pinned tooling commit, frozen manuscript and input hashes from that state. Reopen the private files through the references recorded there and verify their SHA-256 checksums before processing. If a referenced file is inaccessible or differs, record the blocker and ask the preceding owner for the exact version.
2. Work on the checkpoint's `next_action`. Keep local paths relative to the manifest directory. Preserve the previous source take and manifest revision when recording a pickup. Use the repository branch for the frozen cue script and non-sensitive metadata. Use private attachments or another owner-accessible, authenticated location for takes, previews, caches, listening records and approvals. Record a durable file ID or private reference, filename, role, SHA-256 and the related cue or preview version for each retained file. For Library transfers, record the exact file ID and version when available. A chat-only or temporary path is insufficient for a handoff.
3. Before ending the turn, commit the updated production metadata and `STATE.json` to the production branch. Record the phase, next owner, concrete next action, script/manifest/soundscape hashes, open cue IDs, and QA/master SHA-256s and private artifact references as they become available. Reset the review and approval status if the inputs, QA report or master change. Update from the branch head you read. If the head moved, reload it and reconcile the checkpoint before committing. Send the reviewer or next chat the checkpoint link and the commit SHA that contains your update.

## Ownership and checkpoints

| Phase | Owner | What to record before handing off |
| --- | --- | --- |
| `awaiting_manuscript_freeze` | Hex | Review the pinned draft. Freeze `script.json` with one record per spoken turn, stable cue IDs, order, scene, speaker, role and exact text. Keep editing markers out of spoken text. Record the approved cast and SHA-256. |
| `recording` | Hex | Record complete takes with natural pitch and speed. Use Wade for narration and Hex, and Hallie for Ninja. Use the manuscript's gain rules at assembly, applied once. Keep take IDs, duration, voice IDs, checksums and private source references. Fill the manifest from the [tooling examples](../tools/audiobook/examples/). Mark superseded takes rejected and record the selected ones. Hand off the frozen script, manifest, soundscape and take references to Dex. |
| `technical_qa` | Dex | Validate inputs, assemble dry and mixed audio, and record the preview version and artifact hashes. Log flagged cues with expected and observed words, timestamp and disposition. If a pickup is needed, assign Hex the named cue, exact line and defect. Preserve the other takes. |
| `listening_review` | Dex and Hex | Dex supplies the exact dry and mixed preview and QA report. Hex listens in context, checks flagged cues and representative joins or the full master, and records who listened, coverage and findings. Dex adds hash-bound cue reviews where warranted, reruns QA, and records the resulting QA and master hashes. Keep the episode blocked when a genuine fault or unresolved disagreement remains. |
| `awaiting_owner_approval` | Ninja | Approve the exact versioned MP3 and QA hashes explicitly and leave a durable reference to that decision. Technical signoff, chat handoff, and approving or merging the tooling PR do not confer release approval. |
| `approved_for_release` | Dex | Once Ninja separately authorizes publication, stage against current main and verify the exact candidate. Publish only Episode 2 through the approved release flow. Verify the catalog, bytes and live player, then checkpoint the resulting commit and playback reference. |

## Dex's render commands

From a checkout of the pinned audiobook tooling, use Python 3.12 and FFmpeg. Save the inputs together in a private local directory. Extract the sources there using the relative paths in `manifest.json`. Use the [tool runbook](../tools/audiobook/README.md) for the schemas, ASR configuration, review records and release commands.

```sh
python -m pip install -r tools/audiobook/requirements-asr.txt
python tools/audiobook/pipeline.py validate "$WORK/manifest.json"
python tools/audiobook/pipeline.py produce "$WORK/manifest.json" "$WORK/soundscape.json" \
  "$WORK/asr.json" "$WORK/previews" "$WORK/asr-cache"
```

After listening, repeat the produce command with `--reviews "$WORK/reviews.json"` for the real cue reviews. Save the versioned QA report and exact master together. If any bytes or the QA report change after approval, obtain approval for the new hashes. When a real release is authorized, follow the runbook's new-episode staging and readback instructions. Keep the release plan outside GitHub until its intended publication step.

Episode 1 diagnostic recordings remain private. The simulated Episode 1 gate records are test fixtures and must never be copied into the Episode 2 checkpoint, review records or approval file.
