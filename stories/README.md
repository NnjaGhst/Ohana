# Ohana story library

This directory stores story-series metadata, episode scripts, audio recordings, and illustrations for the Ohana app.

Each series gets its own folder under `stories/series/<series-slug>/`.

Episode naming: `episodes/episode-001/`, `episode-002/`, etc.

Suggested episode contents:
- `script.md` — complete episode text
- `metadata.json` — title, number, narrator, duration, audio and illustration paths
- `audio/` — final MP3 recording and optional segments
- `illustrations/` — episode illustrations

**Important:** GitHub storage is not automatically connected to the live Ohana website. A separate app integration is needed before content appears in Ohana. Do not store credentials or private API keys in this repository.
