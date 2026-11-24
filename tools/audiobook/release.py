"""Stage, publish, verify or roll back one approved Elsewhere reboot master."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlsplit
from urllib.request import urlopen
import hashlib

from pipeline import digest, read, require, write

CATALOG = 'stories/index.json'
SERIES = 'elsewhere-reboot-2026'


def git(*args, data=None, env=None):
    result = subprocess.run(['git', *args], input=data, capture_output=True, env=env, check=False)
    require(result.returncode == 0, f'Git {args[0]} failed')
    return result.stdout


def blob(commit, path):
    return git('show', f'{commit}:{path}')


def target(catalog, number):
    episode_id = f'{SERIES}-{number:03d}'
    matches = [entry for entry in catalog['episodes'] if entry['id'] == episode_id]
    require(len(matches) <= 1, 'Duplicate catalog ID')
    if not matches:
        return None
    entry = matches[0]
    require(entry['seriesId'] == SERIES and entry['number'] == number, 'Wrong catalog episode identity')
    return entry


def remote_main():
    commit = git('ls-remote', '--exit-code', 'origin', 'refs/heads/main').decode().split()[0]
    git('fetch', '--no-tags', 'origin', commit)
    return commit


def build_commit(parent, files, message):
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, 'GIT_INDEX_FILE': str(Path(tmp) / 'index')}
        git('read-tree', parent, env=env)
        for path, content in files.items():
            sha = git('hash-object', '-w', '--stdin', data=content).decode().strip()
            git('update-index', '--add', '--cacheinfo', f'100644,{sha},{path}', env=env)
        tree = git('write-tree', env=env).decode().strip()
        return git('commit-tree', tree, '-p', parent, '-m', message).decode().strip()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()


def validate_approval(preview, approval, master):
    report_file = preview / 'qa.json'
    report = read(report_file)
    require(report['status'] == 'awaiting_listening_and_owner_approval'
            and report['checks'] and all(row['disposition'] in ('pass', 'reviewed') for row in report['checks']),
            'QA still blocked')
    require(report['version'] == preview.name and approval['qa_sha256'] == digest(report_file),
            'Approval must identify this QA report')
    require({'preview.mp3', 'dry.wav', 'environment.wav', 'foley.wav', 'mix.wav', 'timeline.json'}
            <= report['outputs'].keys(), 'Missing required preview artifacts')
    for name, checksum in report['outputs'].items():
        require(re.fullmatch(r'[\w.-]+', name) and (preview / name).is_file()
                and digest(preview / name) == checksum,
                'Preview is missing a verified artifact')
    require(re.fullmatch('[0-9a-f]{64}', master) and
            report['outputs']['preview.mp3'] == approval['master_sha256'] == master
            and approval['owner'] == 'NnjaGhst' and approval['owner_approval_reference']
            and approval['episode_id'] == report['episode']['id'], 'Exact master or owner approval missing')
    listening = approval['listening']
    require(listening.get('reviewer') and listening.get('scope') in ('full_master', 'flagged_and_representative_joins')
            and listening.get('notes') and listening.get('passed') is True,
            'Listening review missing or failed')
    return report


def stage(preview, approval, master, plan_path, new_episode=None):
    report = validate_approval(preview, approval, master)
    episode = report['episode']
    number = episode['number']
    require(type(number) is int and number > 0 and episode['series_id'] == SERIES
            and episode['id'] == f'{SERIES}-{number:03d}', 'Wrong release identity')
    base = remote_main()
    catalog = json.loads(blob(base, CATALOG))
    catalog_before = deepcopy(catalog)
    entry = target(catalog, number)
    folder = f'series/elsewhere/reboot-2026/episodes/episode-{number:03d}'
    metadata_path = f'{folder}/ohana-metadata.json'
    if entry:
        require(new_episode is None and entry['metadata'] == metadata_path, 'Wrong metadata path')
        metadata = json.loads(blob(base, 'stories/' + metadata_path))
        require(entry.get('audio') == metadata.get('audio_file'), 'Stale catalog audio')
        if entry.get('audio'):
            require(entry['audio'].startswith(folder + '/audio/'), 'Wrong prior episode audio path')
            blob(base, 'stories/' + entry['audio'])
    else:
        require(new_episode is not None, 'Missing catalog entry; provide approved new episode metadata')
        entry = new_episode['entry']
        require(entry['id'] == episode['id'] and entry['seriesId'] == SERIES and entry['number'] == number
                and entry.get('metadata') == metadata_path and entry.get('audio') is None,
                'Wrong new episode catalog entry')
        metadata = new_episode['metadata']
        require(metadata.get('audio_file') is None, 'Unexpected existing audio in new metadata')
        catalog['episodes'].append(entry)
        # Creative assets must already exist on main and are preserved as-is.
        for path in [entry['text'], *(image['path'] for image in entry['images']),
                     metadata['script_file'], *metadata['illustrations']]:
            require(not path.startswith('/') and '..' not in Path(path).parts, 'Invalid catalog asset path')
            blob(base, 'stories/' + path)
    require(metadata['episode'] == number and metadata['series'] == 'Elsewhere (2026 Reboot)'
            and metadata['title'] == entry['title'], 'Wrong episode metadata')
    previous = entry.get('audio')
    audio_path = f'{folder}/audio/episode-{number:03d}-{master}.mp3'
    require(previous != audio_path, 'This exact master is already in the catalog')
    entry.update(audio=audio_path, status='ready')
    metadata.update(audio_file=audio_path, actual_duration_seconds=report['samples'] / 48000,
                    audio_edit_version=report['version'], audio_preview_approved=True,
                    status='ready', published_to_ohana=True)
    require([x for x in catalog_before['episodes'] if x['id'] != episode['id']] ==
            [x for x in catalog['episodes'] if x['id'] != episode['id']], 'Unrelated catalog changes')
    files = {'stories/' + audio_path: (preview / 'preview.mp3').read_bytes(),
             'stories/' + metadata_path: json_bytes(metadata), CATALOG: json_bytes(catalog)}
    commit = build_commit(base, files, f'Release {episode["id"]} audiobook')
    branch = f'release/{episode["id"]}-{commit[:12]}'
    git('branch', branch, commit)
    plan = dict(base=base, commit=commit, branch=branch, master_sha256=master, episode=episode,
                audio=audio_path, metadata=metadata_path, previous_audio=previous,
                qa_sha256=approval['qa_sha256'], owner_approval_reference=approval['owner_approval_reference'])
    write(plan_path, plan)
    return plan


def check_commit(plan, commit):
    episode = plan['episode']
    number = episode['number']
    entry = target(json.loads(blob(commit, CATALOG)), number)
    require(entry and entry['id'] == episode['id'] and entry['seriesId'] == episode['series_id']
            and entry['audio'] == plan['audio'] and entry['metadata'] == plan['metadata'],
            'Catalog verification failed')
    metadata = json.loads(blob(commit, 'stories/' + plan['metadata']))
    require(metadata['episode'] == number and metadata['audio_file'] == plan['audio']
            and metadata['series'] == 'Elsewhere (2026 Reboot)', 'Metadata verification failed')
    require(hashlib.sha256(blob(commit, 'stories/' + plan['audio'])).hexdigest()
            == plan['master_sha256'], 'Committed audio differs from approved master')


def verify(plan, main, url, readback_command):
    check_commit(plan, main)
    parsed = urlsplit(url)
    require(parsed.scheme == 'https' and parsed.hostname and not parsed.username
            and not parsed.password and not parsed.query and not parsed.fragment, 'Invalid public audio URL')
    with urlopen(url, timeout=60) as response:
        require(urlsplit(response.url).scheme == 'https', 'Public audio redirected away from HTTPS')
        checksum = hashlib.file_digest(response, 'sha256').hexdigest()
    require(checksum == plan['master_sha256'], 'Public audio differs from approved master')
    child = subprocess.run([*readback_command, plan['episode']['id']], capture_output=True, timeout=120)
    require(child.returncode == 0, 'Ohana readback failed')
    live = json.loads(child.stdout)
    require(live['id'] == plan['episode']['id'] and live['seriesId'] == plan['episode']['series_id']
            and live['audioPath'] == plan['audio'] and live['audioUrl'] == url,
            'Ohana readback is stale or points to a different episode/master')


def publish(plan, master, preview, approval):
    report = validate_approval(preview, approval, master)
    require(master == plan['master_sha256'] and approval['qa_sha256'] == plan['qa_sha256']
            and report['episode'] == plan['episode']
            and approval['owner_approval_reference'] == plan['owner_approval_reference'],
            'Explicit approval of staged release required')
    require(remote_main() == plan['base'], 'Main changed after staging; restage and review')
    commit = plan['commit']
    require(git('rev-parse', commit + '^').decode().strip() == plan['base'], 'Invalid release ancestry')
    paths = git('diff-tree', '--no-commit-id', '--name-only', '-r', commit).decode().splitlines()
    require(set(paths) == {CATALOG, 'stories/' + plan['audio'], 'stories/' + plan['metadata']},
            'Unexpected files in release')
    before = json.loads(blob(plan['base'], CATALOG))
    after = json.loads(blob(commit, CATALOG))
    for catalog in (before, after):
        catalog['episodes'] = [row for row in catalog['episodes'] if row['id'] != plan['episode']['id']]
    require(before == after, 'Unrelated catalog changes')
    check_commit(plan, commit)
    git('push', 'origin', f'{commit}:refs/heads/main')


def rollback(plan):
    head = remote_main()
    require(head == plan['commit'], 'Main changed; inspect before reverting the release')
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, 'GIT_INDEX_FILE': str(Path(tmp) / 'index')}
        git('read-tree', plan['base'], env=env)
        audio_path = 'stories/' + plan['audio']
        require(audio_path not in git('ls-tree', '-r', '--name-only', plan['base'], audio_path).decode().splitlines(),
                'Versioned audio path existed before release')
        audio_sha = git('rev-parse', f'{head}:{audio_path}').decode().strip()
        git('update-index', '--add', '--cacheinfo', f'100644,{audio_sha},{audio_path}', env=env)
        tree = git('write-tree', env=env).decode().strip()
    commit = git('commit-tree', tree, '-p', head, '-m', 'Roll back audiobook release').decode().strip()
    git('push', 'origin', f'{commit}:refs/heads/main')
    restored = remote_main()
    require(restored == commit and git('rev-parse', restored + '^{tree}').decode().strip() == tree,
            'Rollback verification failed')
    return commit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    prepare = sub.add_parser('stage')
    for name in ('preview', 'approval', 'plan'):
        prepare.add_argument(name, type=Path)
    prepare.add_argument('--new-episode', type=Path)
    prepare.add_argument('--master', required=True)
    for action in ('publish', 'verify', 'rollback'):
        command = sub.add_parser(action)
        command.add_argument('plan', type=Path)
        if action == 'publish':
            command.add_argument('--preview', required=True, type=Path)
            command.add_argument('--approval', required=True, type=Path)
        if action != 'verify':
            command.add_argument('--master', required=True)
        if action != 'rollback':
            command.add_argument('--public-url', required=True)
            command.add_argument('--readback', required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.action == 'stage':
            plan = stage(args.preview, read(args.approval), args.master, args.plan,
                         read(args.new_episode) if args.new_episode else None)
            print(f"Staged {plan['branch']} at {plan['commit']}.")
            return
        plan = read(args.plan)
        if args.action == 'rollback':
            require(args.master == plan['master_sha256'], 'Identify the exact release to roll back')
            print(f'Rollback verified on main at {rollback(plan)}; check Ohana readback after refresh.')
            return
        readback_command = read(args.readback)
        require(isinstance(readback_command, list) and readback_command and
                all(isinstance(part, str) and part for part in readback_command), 'Invalid readback command')
        if args.action == 'publish':
            publish(plan, args.master, args.preview, read(args.approval))
        verify(plan, remote_main(), args.public_url, readback_command)
        print('Release verified on main, at the public URL and in Ohana.')
    except (ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        parser.exit(2, f'Release blocked or unverified: {error}. Check main before retrying.\n')


if __name__ == '__main__':
    main()
