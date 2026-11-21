# Elsewhere audio production

## Hex in regular ChatGPT

In `NnjaGhst/Ohana`, save the approved script as `stories/series/elsewhere/episodes/episode-NNN/script.md`. Save `audio-manifest.json` next to it. Reference the script by filename and list every clip in final playback order. Use the existing HeyGen MP3 links and label each clip with its script cue so Dex can check coverage. Include narration and dialogue through the end of the episode. Keep `status` at `awaiting-sources` while any clip or speaker is unverified.

This repository is public. Add only scripts and audio links approved for public sharing. Use direct, unsigned HTTPS HeyGen MP3 URLs. Keep access tokens, expiring signed links, and private audio out of commits.

Example structure. Replace the illustrative URLs with the exact links from the HeyGen results.

```json
{
  "version": 1,
  "episodeId": "elsewhere-NNN",
  "script": "script.md",
  "status": "ready",
  "segments": [
    {
      "label": "Chapter 1, opening narration",
      "speaker": "narrator",
      "voiceId": "a3a4aa242aaf431ea8ec2c4b3bb332fa",
      "url": "https://resource2.heygen.ai/example-narration.mp3"
    },
    {
      "label": "Chapter 1, Ninja's first line",
      "speaker": "ninja",
      "voiceId": "8f71d16d1fa84544a4db41a18264d613",
      "url": "https://resource2.heygen.ai/example-dialogue.mp3"
    }
  ]
}
```

Use Wade for `narrator`, `hex`, and any supporting character recorded with him (`other`). Use Hallie for `ninja`. Each clip must contain a single speaker role. Hallie clips default to a 0.9 volume multiplier. Set `"gainApplied": true` only when Hallie's clip is confirmed to already have that reduction. If a recording combines multiple speakers, provide the separate originals. If a URL fails, recover the original clip from HeyGen or the recording chat before considering a new recording.

## Standard editing and approval

Follow the [Episode 2 audio editing guide](episodes/episode-002/audio-editing-guide.md) for every Elsewhere episode. Substitute the current episode's script, commit revision, and source recordings for the Episode 2 examples. Pin the script blob before editing and resolve any script changes before splicing.

Keep an ordered cue sheet in the episode manifest. For each cue, record its script location, exact text, speaker, source recording, verified source bounds, final time range, and status. Leave unverified bounds empty. Reuse exact, clean recordings and mark unavailable or unusable cues `needs_recording` with the exact script text. Cut Wade's Ninja dialogue before inserting Hallie, preserve Wade's dialogue tags in place, and apply Hallie's 0.90 linear gain exactly once. Use the assembler below only with verified, isolated clips in final order.

Check the complete mix against the script for missing or repeated words, wrong speakers, clipped syllables, awkward pauses, and playback start and end. Share one private MP3 preview for approval. Upload or publish only after Ninja approves that complete preview, then verify playback and illustration cues against the final audio.

## Dex in Work

With Python 3, FFmpeg, and FFprobe available, run:

```sh
python3 stories/series/elsewhere/assemble_audio.py 2 Elsewhere_Episode_02_The_Crooked_Lantern.mp3
```

The script reads the manifest and script from the same GitHub commit, validates the voice IDs and uses the recording order supplied in the manifest, downloads the clips, and creates an MP3 with no extra gaps, pitch changes, or effects. Check the cues against the script for complete coverage and listen across speaker transitions before uploading.

Use Ohana Stories to upload the MP3 to the episode. Confirm the returned repository path before updating the existing episode's `audio` field with that stories-relative path. If the episode already links to that exact asset, the update can be skipped. Keep the existing title, script, illustrations, and catalog status unless the user requests a change. Read back the episode, verify that the linked audio matches the master, and check playback in Ohana when the episode is visible. No site deployment is needed for the GitHub catalog update.
