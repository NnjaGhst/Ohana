# Elsewhere Episode 2 — Audio Editing Contract for Dex

## Source of truth
- Canonical script: `stories/series/elsewhere/episodes/episode-002/script.md`.
- Existing audio references: `audio-production-manifest.json`.
- Pickup references: `hallie-pickups.json` (currently partial/unverified; **null URLs are not recordings**).
- The user-provided pickup list references script Git blob `0ac4fa0022b40fad6b841ee2b0d9f825ffe75163`. Before editing, verify the current script matches that blob. If not, produce a text diff and resolve it before mixing.
- Never overwrite the canonical script as part of an audio-editing task.

## Voices and levels
- Wade voice ID `a3a4aa242aaf431ea8ec2c4b3bb332fa`: narration, Hex, all non-Ninja speakers.
- Hallie voice ID `8f71d16d1fa84544a4db41a18264d613`: **Ninja's quoted spoken words only**.
- Record both at speed 1.0 with original pitch/timbre. In the mix, apply **0.90 linear amplitude** to Hallie exactly once; Wade stays at 1.0. Avoid effects or normalization that defeats the intended relative gain.

## Precise splice algorithm
1. Parse canonical script in narrative order into alternating atomic segments: Wade narration/dialogue (including dialogue tags) and Hallie Ninja spoken words. A quotation split by a tag must be split into multiple Hallie clips separated by Wade's tag.
2. Build a cue sheet: sequence index, script chapter/line, speaker, exact text, source filename/URL, source in/out samples or timestamps, target in/out timestamps, status. Do not use an unverified timestamp.
3. Download original MP3 sources and inspect speech against exact script. Parts 1–2 are rough Wade takes with Ninja's words included. Part 3 is older and may differ. Retain only audio whose words exactly match the script.
4. For each Ninja line in a rough Wade take: identify the *exact* Wade dialogue start/end by listening and waveform/speech alignment. Cut only those spoken words; retain adjacent narration and dialogue tags. Insert the corresponding Hallie clip at that position. **Never overlay Hallie on top of Wade's Ninja line.** If Wade's speech overlaps a boundary, rerecord the smallest clean Wade sentence/tag needed.
5. For lines with interleaved tags, use `Hallie quote A -> Wade tag -> Hallie quote B` in exact canonical order; do not combine across a tag.
6. Join with natural short pauses at sentence boundaries; use short equal-power fades only if necessary to remove clicks, never cut phonemes. Preserve continuous ambient silence naturally; avoid audible abrupt changes.
7. Verify every scripted word once and only once, no doubled voices, clipped syllables, incorrect speaker, wrong line order, or unnatural pauses. Validate all 28 missing Chapter 2/4/5/7 Hallie turns plus 8 Chapter 1 sample turns.
8. Produce one MP3 master; check total duration, playback, and beginning/end. Make a **private preview** for Ninja. **Do not publish** until she approves.

## Existing source caveats
- Chapter 1 Hallie sample is one MP3 containing eight noncontiguous lines. Use the exact ranges in Dex's own recording list, checking each cut against waveform; don't treat as continuous dialogue.
- Part 3 Chapter 6 line 558: verify whether Wade says 'lamps and puddles' instead of canonical 'lamps in puddles'; rerecord exact sentence if wrong.
- Part 3 Chapter 6 line 560: existing 'It might have been a cruel joke…' paragraph reportedly present; reuse if verified.
- Part 3 Chapter 7 lines 586, 590, 678: replace Wade's Ninja readings with full Hallie pickups.
- Current Hallie pickup file is not complete and contains no verified URLs. Do not claim recordings exist merely because a generation call was attempted.
- If any cue is unavailable or cannot be cut cleanly, mark `needs_recording` with exact chapter/line/text/speaker. Do not invent or silently skip audio.

## GitHub handoff and publication
- Store an updated audio manifest containing every verified source URL or stable file path, exact cue sequence, checksum if available, durations, and missing-cue statuses.
- Keep all intermediate and final audio files out of the Git repository unless the app's established storage workflow explicitly requires otherwise.
- After Ninja approves the private MP3, upload through the existing Ohana workflow, update episode metadata, and verify playback/scene cues. Preserve all unrelated app features, music library and database.
