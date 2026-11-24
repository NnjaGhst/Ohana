# Recording queue

The queue orchestrates recording on a single private Linux runner with persistent storage. It is a foreground command. The HeyGen ChatGPT connector is callable in the chat host. It is not an executable or an Actions credential. For unattended operation outside that host, deploy a runner explicitly and provision its credentials and storage.

## Prerequisites and pilot

- Freeze the ordered script and cast. Seed the ledger with every existing or awaiting-transfer take. A known but unverified take uses `transfer_pending`; a rejected one uses `rejected`. Neither is scheduled automatically. Retain rejected originals and their provenance.
- Use a private durable work directory for the ledger, request receipts, ASR configuration and source files. Use a separate private persistent archive for WAVs. Back up both. The lock only coordinates on one shared filesystem. Do not start a second machine or restore a stale ledger while an active or newer ledger exists.
- The REST adapter uses HeyGen's [Text to Speech API](https://developers.heygen.com/docs/voices/heygen-voice-speech). Configure `HEYGEN_API_KEY` in the private runner's secret environment, plus `HEYGEN_AUDIOBOOK_ACCOUNT` and the authorized `HEYGEN_AUDIOBOOK_MODEL`. Never put keys, source URLs or WAVs in GitHub. Run `python tools/audiobook/heygen_recording.py probe` for an authenticated, read-only check.
- Obtain explicit authorization for that account, model, and a one-call pilot drawn from missing cues. Use `pilot_only: true`, `pilot_verified: false`, and `max_total_calls: 1` for it. Verify the WAV, independent archive readback, source transcript and versioned receipt. Only after that pilot and a larger budget are approved, update the cumulative ceilings and set `pilot_verified: true`. The adapter has no paid fallback.
- Preflight the durable volumes, API access and quota, account-specific call/day limits, FFmpeg, and the pinned ASR dependencies. The retention adapter accepts the Episode 2 source host supported by its existing ingest helper. If the API returns a different host, confirm the exact official download origin before extending the allowlist. Download/QA failures must never trigger new synthesis.

## Start and resume

Seed once with an array of `{cue_id,take_id,disposition,receipt}` rows. A verified receipt contains SHA-256, bytes, duration, and either the pinned private file IDs and version or an archive key. It also records `roundtrip_verified: true`. A pending transfer has no receipt. A verified take with unsettled QA uses `review_pending`.

```sh
Q=tools/audiobook/recording_queue.py
python "$Q" "$WORK/ledger.json" init "$WORK/script.json" "$WORK/cast.json" \
  "$WORK/existing.json" elsewhere-reboot-2026-002
python "$Q" "$WORK/ledger.json" plan
python "$Q" "$WORK/ledger.json" run "$WORK/queue-settings.json"
```

Settings contain `durable_workspace` (the private work path), `account`, `authorization_reference`, `pilot_verified`, `max_total_calls`, `max_total_characters`, `max_calls_per_utc_day`, and `min_interval_seconds`. These are cumulative ceilings and an account-specific rate, not estimates. Set `provider_command` to `["python","tools/audiobook/heygen_recording.py"]` and `retain_command` to `["python","tools/audiobook/heygen_retention.py"]`. Set `HEYGEN_PRIVATE_WORK`, `HEYGEN_PRIVATE_ARCHIVE` and `HEYGEN_INGEST_HELPER` in that environment. The latter points to the pinned Episode 2 ingest script. Put the existing pipeline's adapter config at `$WORK/asr.json`.

The process durably reserves a charge before submission. After a response it separately persists private retrieval information. Restart with the same ledger. A killed or ambiguous in-flight request is held and never resubmitted. Inspect provider request history before recording another take for that cue. After a provider error the queue stops. Reconcile the failure, obtain renewed authorization when the integration requires it, and supply a new `authorization_reference` with `resume_after_provider_block` set to the stopped authorization reference. This permits new missing cues, never automatically retries the failed one. A retained response can be downloaded again or recovered from its archive receipt without synthesis. Keep source URLs in the private spool. To record a verified reconciliation or a separately authorized pickup, call `python "$Q" "$WORK/ledger.json" resolve "$WORK/decision.json"`. A decision contains `cue_id`, `take_id`, a verified `receipt`, and a private `evidence_reference`. Resolve selects a candidate for the unchanged production QA gates. It does not clear listening or publication and cannot select a previously rejected or changed source under the same take ID.

For a new frozen revision, stop the previous queue and reconcile any submitted or received requests against provider history. Seed the new revision only with exact unchanged cues and keep retired audio in the private archive. Recheck the live episode checkpoint before each start or resume. On Draft 4, extract `performance_directions` from `revision-004.json` to a standalone JSON object and supply it with `init --performance "$WORK/performance.json"`. The plain-text REST adapter leaves those cues on hold until a tested delivery path and verified takes are available.

## QA and assembly

The recording queue separates candidates, source/word defects, and unresolved submissions. An exact transcript is only a candidate. Review real pickup needs in one consolidated pass. The existing [production pipeline](README.md) owns contextual ASR, muted controls, clipping/headroom, independent cross-checks, review evidence and release gates. Do not convert ASR agreement into listening signoff. Reopen Library takes to `$WORK/takes/<take-id>.wav` and compare their hashes. Include the approved r01 repair with its own retained identity, separate from the two rejected originals.

```sh
python "$Q" "$WORK/ledger.json" manifest "$WORK/script.json" "$WORK/cast.json" "$WORK/manifest.json"
python tools/audiobook/pipeline.py produce "$WORK/manifest.json" "$WORK/soundscape.json" \
  "$WORK/asr.json" "$WORK/previews" "$WORK/asr-cache"
```

Keep the dry, environment, foley and mixed previews and QA report in a private versioned directory. Release remains a separate, owner-approved operation using the existing exact QA and master hash validation.

## Acceptance

Run `python -m unittest discover -s tools/audiobook -v`. Mock tests cover deterministic order, retention of existing takes, restarting after a submitted request, expired source, checksum mismatch, provider limit, budget and daily exhaustion, missing/repeated text, and production pilot/authorization gating. This tests the orchestration, not live access to the user's API account. The current project runner did not have a HeyGen API key. The connected account could read Wade's active voice, but quota and authorized account limits were not established for a new pilot. Live synthesis remains gated.
