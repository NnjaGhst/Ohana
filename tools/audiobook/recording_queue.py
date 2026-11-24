"""Resumable, single-worker recording queue. Provider and retention adapters use JSON on stdin/out."""
import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

import pipeline

SAFE_ID = re.compile(r'[A-Za-z0-9_-]{1,100}')
SHA = re.compile(r'[0-9a-f]{64}')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def save(path, value):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, mode='w', encoding='utf-8') as out:
        temp = Path(out.name)
        try:
            json.dump(value, out, indent=2)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
            temp.replace(path)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temp.unlink(missing_ok=True)


def init(script, cast, episode, existing):
    require(SAFE_ID.fullmatch(episode), 'Invalid episode ID')
    cues = read(script)
    cast_rows = read(cast)
    cast_rows = cast_rows.get('confirmed', cast_rows)
    require(cues and [cue['order'] for cue in cues] == list(range(1, len(cues) + 1)),
            'Frozen cue order is invalid')
    require(len({cue['id'] for cue in cues}) == len(cues), 'Duplicate cue ID')
    state = dict(version=1, episode_id=episode, script_sha256=digest(script), cast_sha256=digest(cast),
                 account=None, calls_reserved=0, characters_reserved=0, reservations=[], cues={})
    for cue in cues:
        identifier = cue['id']
        require(SAFE_ID.fullmatch(identifier), 'Invalid cue ID')
        voice = cast_rows[cue['speaker']]['voice_id']
        require(SAFE_ID.fullmatch(voice) and 0 < len(cue['text']) <= 10000, 'Invalid voice or turn length')
        state['cues'][identifier] = dict(text=cue['text'], voice_id=voice, speed=1, pitch=1,
                                         state='missing', attempts=[])
    for take in existing:
        cue = state['cues'][take['cue_id']]
        identifier = take['take_id']
        require(SAFE_ID.fullmatch(identifier) and not any(x['take_id'] == identifier for x in cue['attempts']),
                'Duplicate or invalid existing take ID')
        disposition = take['disposition']
        require(disposition in ('candidate', 'rejected', 'transfer_pending', 'review_pending'), 'Invalid existing take state')
        attempt = dict(take_id=identifier, origin='existing', state=disposition)
        if disposition != 'transfer_pending':
            attempt['receipt'] = receipt_fields(take['receipt'])
        cue['attempts'].append(attempt)
        if cue['state'] != 'candidate':
            cue['state'] = 'candidate' if disposition == 'candidate' else 'hold'
    return state


def plan(state):
    missing = [cue for cue in state['cues'].values() if cue['state'] == 'missing']
    words = sum(len(cue['text'].split()) for cue in missing)
    return dict(cues=len(state['cues']), missing=len(missing),
                candidates=sum(c['state'] == 'candidate' for c in state['cues'].values()),
                unreconciled=sum(c['state'] not in ('missing', 'candidate') for c in state['cues'].values()),
                characters=sum(len(cue['text']) for cue in missing), words=words,
                estimated_minutes_135_to_165_wpm=[round(words / 165, 2), round(words / 135, 2)],
                calls_reserved=state['calls_reserved'], characters_reserved=state['characters_reserved'])


def receipt_fields(item):
    sha = item['sha256']
    duration, size = item['duration_seconds'], item['bytes']
    require(SHA.fullmatch(sha) and math.isfinite(duration) and 0 < duration <= 480 and
            type(size) is int and size > 0 and item['roundtrip_verified'] is True,
            'Missing a verified private retention receipt')
    storage = {}
    if 'archive_key' in item:
        key = item['archive_key']
        require(key == sha + '.wav', 'Invalid private archive key')
        storage['archive_key'] = key
    else:
        file_id, library_id = item['file_id'], item['library_file_id']
        require(SAFE_ID.fullmatch(file_id) and SAFE_ID.fullmatch(library_id) and
                type(item['version']) is int and item['version'] >= 0, 'Invalid private file identity')
        storage.update(file_id=file_id, library_file_id=library_id, version=item['version'])
    return dict(sha256=sha, duration_seconds=duration, bytes=size, roundtrip_verified=True, **storage)


def adapter(command, data, timeout):
    result = subprocess.run(command, input=json.dumps(data), text=True, capture_output=True, timeout=timeout)
    require(result.returncode == 0, 'Adapter failed')
    return json.loads(result.stdout)


def run(path, state, settings, provider=adapter, retain=adapter, clock=time.time):
    account = settings['account']
    require(SAFE_ID.fullmatch(account) and state['account'] in (None, account), 'Queue account mismatch')
    pilot = settings.get('pilot_only') is True and settings['max_total_calls'] == 1
    require(settings['authorization_reference'] and (settings['pilot_verified'] is True or pilot),
            'Explicit authorization and a one-call pilot or verified live adapter are required')
    ceilings = ('max_total_calls', 'max_total_characters', 'max_calls_per_utc_day', 'min_interval_seconds')
    require(all(type(settings[key]) is int and settings[key] > 0 for key in ceilings),
            'Positive budget and rate limits required')
    require(settings['max_total_calls'] >= state['calls_reserved'] and
            settings['max_total_characters'] >= state['characters_reserved'], 'Budget is below prior reservations')
    if state.get('provider_block'):
        old = state['provider_block']['authorization_reference']
        if settings.get('resume_after_provider_block') != old or settings['authorization_reference'] == old:
            return 'provider_blocked_pending_authorization'
        del state['provider_block']
    state['account'] = account

    def block_provider(reason):
        state['provider_block'] = dict(reason=reason,
                                       authorization_reference=settings['authorization_reference'])
        save(path, state)
    # Recovering a saved response allows another download, never another paid request.
    for cue in state['cues'].values():
        if cue['state'] == 'submitted':
            cue['state'] = 'hold'
            cue['attempts'][-1].update(state='hold', reason='ambiguous_submission')
    save(path, state)

    def retain_record(cue, attempt):
        spool = path.parent / (attempt['request_id'] + '.private.json')
        if not spool.is_file():
            return
        try:
            result = retain(settings['retain_command'], read(spool), 900)
            receipt = receipt_fields(result)
            require(result['sha256'] == digest(Path(result['verified_wav'])),
                    'Checksum mismatch on retained WAV')
            require(receipt['bytes'] == Path(result['verified_wav']).stat().st_size,
                    'Byte count mismatch on retained WAV')
            failure = ('signal_gate' if result.get('signal_flags') else
                       pipeline.content_failure(cue['text'], result['transcript']))
            disposition = 'hold' if failure else 'candidate'
            attempt.update(receipt=receipt, state=disposition, reason=failure)
            cue['state'] = disposition
            save(path, state)
            spool.unlink()
        except Exception:
            attempt['reason'] = 'private_ingest_or_qa_incomplete'
            save(path, state)

    for cue in state['cues'].values():
        if cue['state'] in ('received', 'hold') and cue['attempts'] and 'request_id' in cue['attempts'][-1]:
            retain_record(cue, cue['attempts'][-1])
    for cue_id, cue in state['cues'].items():
        if cue['state'] != 'missing':
            continue
        now = int(clock())
        reservations = state['reservations']
        if (state['calls_reserved'] + 1 > settings['max_total_calls'] or
                state['characters_reserved'] + len(cue['text']) > settings['max_total_characters']):
            return 'budget_exhausted'
        if sum(t // 86400 == now // 86400 for t in reservations) >= settings['max_calls_per_utc_day']:
            return 'daily_limit'
        if reservations and now - reservations[-1] < settings['min_interval_seconds']:
            # The caller can continue the same job after the configured interval.
            return 'rate_wait'
        take_id = f'{cue_id}-t01'
        request_id = hashlib.sha256(json.dumps([state['episode_id'], state['script_sha256'], account,
                                               cue_id, cue['voice_id'], cue['speed'], cue['pitch'],
                                               take_id]).encode()).hexdigest()
        attempt = dict(take_id=take_id, request_id=request_id, state='submitted')
        cue['attempts'].append(attempt)
        cue['state'] = 'submitted'
        state['calls_reserved'] += 1
        state['characters_reserved'] += len(cue['text'])
        reservations.append(now)
        save(path, state)
        try:
            request = dict(account=account, cue_id=cue_id, take_id=take_id, request_id=request_id,
                           text=cue['text'], voice_id=cue['voice_id'], speed=cue['speed'], pitch=cue['pitch'])
            response = provider(settings['provider_command'], request, 300)
            if response['status'] != 'ok':
                code = response['status']
                cue['state'] = 'hold'
                attempt.update(state='hold', reason=code if code in ('rate_limit', 'quota', 'authorization') else 'provider_error')
                block_provider(attempt['reason'])
                return 'provider_blocked'
            save(path.parent / (request_id + '.private.json'), dict(request=request, response=response))
            cue['state'] = 'received'
            attempt['state'] = 'received'
            save(path, state)
        except Exception:
            cue['state'] = 'hold'
            attempt.update(state='hold', reason='ambiguous_submission')
            block_provider(attempt['reason'])
            return 'reconcile_submission'
        retain_record(cue, attempt)
    return 'queue_drained'


def manifest(state, script, cast, output):
    require(digest(script) == state['script_sha256'] and digest(cast) == state['cast_sha256'],
            'Frozen input changed')
    require(all(cue['state'] == 'candidate' for cue in state['cues'].values()),
            'Resolve missing or held cues before full assembly')
    episode = state['episode_id']
    require(re.fullmatch(r'elsewhere-reboot-2026-\d{3}', episode), 'Unexpected episode')
    cast_rows = read(cast)
    voices = {speaker: value['voice_id'] for speaker, value in cast_rows.get('confirmed', cast_rows).items()}
    root = output.parent.resolve()
    cues = []
    for line in read(script):
        cue = state['cues'][line['id']]
        chosen, = [take for take in cue['attempts'] if take['state'] == 'candidate']
        receipt = chosen['receipt']
        asset = 'takes/' + chosen['take_id'] + '.wav'
        pipeline.asset(root, dict(asset=asset, sha256=receipt['sha256']))
        speaker, role = line['speaker'].casefold(), line['role']
        voice = cue['voice_id']
        gain = .8 if speaker in ('ninja', 'hex') and role == 'thought' else (
            .9 if voice == pipeline.HALLIE and role == 'dialogue' else 1.)
        # "approved" selects the source for pipeline validation, not listening or release.
        take = dict(id=chosen['take_id'], status='approved', asset=asset, sha256=receipt['sha256'],
                    duration_seconds=receipt['duration_seconds'], voice_id=voice, speed=1, pitch=1)
        cues.append(dict(line, voice_id=voice, gain=gain, takes=[take]))
    save(output, dict(version=1, episode=dict(id=episode, series_id='elsewhere-reboot-2026',
                        number=int(episode.rsplit('-', 1)[-1])), cast=voices,
                      script=dict(asset=str(script.resolve().relative_to(root)),
                                  sha256=state['script_sha256']), cues=cues))
    pipeline.validate(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ledger', type=Path)
    sub = parser.add_subparsers(dest='action', required=True)
    create = sub.add_parser('init')
    for arg in ('script', 'cast', 'existing'):
        create.add_argument(arg, type=Path)
    create.add_argument('episode')
    sub.add_parser('plan')
    export = sub.add_parser('manifest')
    for arg in ('script', 'cast', 'output'):
        export.add_argument(arg, type=Path)
    start = sub.add_parser('run')
    start.add_argument('settings', type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    args.ledger.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        with args.ledger.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.action == 'init':
                require(not args.ledger.exists(), 'Ledger already exists')
                save(args.ledger, init(args.script, args.cast, args.episode, read(args.existing)))
            else:
                state = read(args.ledger)
                if args.action == 'manifest':
                    manifest(state, args.script, args.cast, args.output)
                elif args.action == 'run':
                    settings = read(args.settings)
                    require(args.ledger.parent.resolve() == Path(settings['durable_workspace']).resolve(),
                            'Run only on the configured persistent private workspace')
                    while (status := run(args.ledger, state, settings)) == 'rate_wait':
                        time.sleep(min(settings['min_interval_seconds'], 60))
                    print(json.dumps(dict(status=status, **plan(state))))
                else:
                    print(json.dumps(plan(state)))
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(2, f'Queue blocked ({type(error).__name__}). Inspect private configuration and lock.\n')


if __name__ == '__main__':
    main()
