"""Offline audiobook production. Run with Python 3.11+, NumPy and FFmpeg."""
import argparse
import hashlib
import json
import re
import subprocess
import tempfile
import unicodedata
import wave
from pathlib import Path

import numpy as np

RATE = 48000
WADE = 'a3a4aa242aaf431ea8ec2c4b3bb332fa'
HALLIE = '8f71d16d1fa84544a4db41a18264d613'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def local(root, name):
    path = (root / name).resolve()
    require(not Path(name).is_absolute() and path.is_relative_to(root.resolve()),
            'Asset locator must stay inside the input directory')
    return path


def asset(root, item):
    path = local(root, item['asset'])
    require(path.is_file() and digest(path) == item['sha256'], 'Missing asset or checksum mismatch')
    return path


def validate(manifest_path):
    root = manifest_path.parent
    manifest = read(manifest_path)
    require(manifest['version'] == 1, 'Unsupported manifest version')
    episode = manifest['episode']
    require(type(episode['number']) is int and episode['number'] > 0
            and episode['series_id'] == 'elsewhere-reboot-2026'
            and episode['id'] == f"elsewhere-reboot-2026-{episode['number']:03d}", 'Wrong episode identity')
    script = read(asset(root, manifest['script']))
    cues = manifest['cues']
    require(cues and len(cues) == len(script), 'Missing or extra cues')
    seen = set()
    take_ids = set()
    for order, (cue, line) in enumerate(zip(cues, script), 1):
        fields = ('id', 'scene', 'order', 'speaker', 'role', 'text')
        require(all(cue[key] == line[key] for key in fields) and cue['order'] == order,
                f'Manuscript mismatch at cue {order}')
        require(cue['id'] not in seen and all(re.fullmatch(r'[a-zA-Z0-9_-]+', cue[key])
                for key in ('id', 'scene')), 'Duplicate or invalid cue ID or scene')
        seen.add(cue['id'])
        require(words(cue['text']) and cue['role'] in ('narration', 'dialogue', 'thought'),
                f"Invalid text or role in {cue['id']}")
        speaker = cue['speaker'].casefold()
        default_voice = {'ninja': HALLIE, 'hex': WADE, 'narrator': WADE}.get(speaker)
        voice = manifest['cast'][cue['speaker']]
        expected_gain = 0.8 if speaker in ('ninja', 'hex') and cue['role'] == 'thought' else (
            0.9 if voice == HALLIE and cue['role'] == 'dialogue' else 1.0)
        require(cue['voice_id'] == voice and (default_voice is None or voice == default_voice)
                and cue['gain'] == expected_gain, f"Wrong voice or gain in {cue['id']}")
        approved = []
        for take in cue['takes']:
            require(take['id'] not in take_ids and take['status'] in ('approved', 'rejected'),
                    'Duplicate take ID or invalid status')
            take_ids.add(take['id'])
            if take['status'] == 'approved':
                approved.append(take)
        require(len(approved) == 1, f"Expected one approved take in {cue['id']}")
        take = approved[0]
        require(take['voice_id'] == voice and take['speed'] == take['pitch'] == 1
                and 0 < take['duration_seconds'] <= 480, f"Invalid take settings in {cue['id']}")
        asset(root, take)
        if 'gap_ms' in cue:
            require(type(cue['gap_ms']) is int and 0 <= cue['gap_ms'] <= 3000, 'Invalid gap')
    return manifest


NUMBERS = dict(zip('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty'.split(), map(str, range(21))))


def words(text):
    text = unicodedata.normalize('NFKC', text).casefold().replace('’', "'")
    return [NUMBERS.get(word, word) for word in re.findall(r"[^\W_]+(?:'[^\W_]+)*", text)]


def content_failure(expected, actual):
    wanted, heard = words(expected), words(actual)
    if not heard:
        return 'blank ASR'
    if wanted == heard:
        return None
    for start in range(len(heard)):
        for length in range(1, (len(heard) - start) // 2 + 1):
            end = start + length
            repeat_end = end
            while heard[start:end] == heard[repeat_end:repeat_end + length]:
                repeat_end += length
                if heard[:end] + heard[repeat_end:] == wanted:
                    return 'unexpected repetition'
    return 'missing, extra, reordered or incorrect words'


def ffmpeg(*args):
    result = subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', *map(str, args)],
                            capture_output=True, check=False)
    require(result.returncode == 0, 'FFmpeg failed to decode, measure or encode audio')
    return result


def decode(path, channels=1):
    output = ffmpeg('-v', 'error', '-xerror', '-i', path, '-map', '0:a:0', '-ac', channels,
                    '-ar', RATE, '-f', 'f32le', '-').stdout
    audio = np.frombuffer(output, dtype='<f4').reshape(-1, channels)
    require(len(audio) > 0 and np.isfinite(audio).all(), 'Empty or invalid decoded audio')
    return audio


def wav(path, audio):
    require(float(np.max(np.abs(audio))) < 1, 'Audio exceeds lossless PCM headroom')
    with wave.open(str(path), 'wb') as output:
        output.setparams((audio.shape[1], 4, RATE, 0, 'NONE', 'not compressed'))
        output.writeframes(np.rint(audio.astype(np.float64) * (2**31 - 1)).astype('<i4').tobytes())


def transcribe(path, adapter, cache):
    checksum = digest(path)
    key = hashlib.sha256(json.dumps([checksum, adapter['id']]).encode()).hexdigest()
    cached = cache / (key + '.json')
    if cached.is_file():
        envelope = read(cached)
        require(envelope['audio_sha256'] == checksum and envelope['adapter_id'] == adapter['id'],
                'Stale ASR cache')
        result = envelope['result']
    else:
        process = subprocess.run([*adapter['command'], str(path)], capture_output=True, timeout=900)
        require(process.returncode == 0, 'ASR adapter failed')
        result = json.loads(process.stdout)
    require(isinstance(result['text'], str) and 0 <= result['confidence'] <= 1
            and isinstance(result['segments'], list), 'Invalid ASR adapter response')
    require(not result['text'].strip() or result['segments'], 'Missing ASR timings')
    last_end = 0
    for segment in result['segments']:
        require(last_end <= segment['start'] < segment['end'] and isinstance(segment['text'], str),
                'Invalid ASR timings')
        last_end = segment['end']
    with wave.open(str(path), 'rb') as audio:
        duration = audio.getnframes() / audio.getframerate()
    require(last_end <= duration + 0.1, 'ASR timing exceeds audio duration')
    require(words(' '.join(segment['text'] for segment in result['segments'])) == words(result['text']),
            'ASR segment text disagrees with transcript')
    if not cached.exists():
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as temporary:
            name = Path(temporary.name)
        try:
            write(name, {'audio_sha256': checksum, 'adapter_id': adapter['id'], 'result': result})
            name.replace(cached)
        finally:
            name.unlink(missing_ok=True)
    return result


def check_content(report, stage, entries, actual, audio_hash, reviews):
    expected = ' '.join(entry['text'] for entry in entries)
    failure = content_failure(expected, actual['text'])
    reasons = []
    if len(words(expected)) <= 3:
        reasons.append('short utterance')
    if actual['confidence'] < 0.85:
        reasons.append('low ASR confidence')
    if any(entry.get('proper_names') for entry in entries):
        reasons.append('proper names')
    if failure:
        reasons.append(failure)
    first = entries[0]
    location = first
    heard_index = 0
    if failure:
        wanted, heard = words(expected), words(actual['text'])
        heard_index = next((index for index, (left, right) in enumerate(zip(wanted, heard))
                            if left != right), min(len(wanted), len(heard)))
        remaining = heard_index
        for entry in entries:
            location = entry
            remaining -= len(words(entry['text']))
            if remaining < 0:
                break
    sample = location['start']
    for segment in actual['segments'] if failure else []:
        heard_index -= len(words(segment['text']))
        if heard_index < 0:
            sample = min(entries[-1].get('end', sample),
                         first['start'] + round(segment['start'] * RATE))
            break
    review = next((item for item in reviews if item.get('stage') == stage
                   and item.get('cue_id') == first['id'] and item.get('audio_sha256') == audio_hash
                   and item.get('expected') == expected and item.get('transcript') == expected
                   and item.get('listened_in_context') is True and item.get('reviewer')
                   and item.get('notes')), None)
    # A blank transcript or an unexpected repeat requires a fresh take or ASR result.
    cleared = bool(review and failure not in ('blank ASR', 'unexpected repetition'))
    report.append(dict(stage=stage, cue_id=first['id'], cue_ids=[entry['id'] for entry in entries],
                       affected_cue_id=location['id'], sample=sample, seconds=sample / RATE, expected=expected,
                       actual=actual, failure=', '.join(reasons) or None,
                       severity='critical' if reasons else 'none', review=review,
                       disposition='reviewed' if cleared and reasons else ('blocked' if reasons else 'pass'),
                       audio_sha256=audio_hash))


def signal_flags(audio, take=False):
    peak = float(np.max(np.abs(audio)))
    if peak >= 0.999:
        return ['clipping']
    if peak < 0.001:
        return ['silence']
    if take and max(float(np.max(np.abs(audio[:240]))), float(np.max(np.abs(audio[-240:])))) > 0.04:
        return ['suspicious take boundary']
    return []


def write_report(out, report):
    write(out / 'qa.json', report)
    lines = [f"Status: {report['status']}", report['listening'], '']
    lines += [f"{row['seconds']:.3f}s {row['cue_id']} [{row['stage']}] {row['disposition']}: {row['failure']}"
              for row in report['checks'] if row['failure']]
    (out / 'qa.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def make_timeline(cues):
    offset = 0
    for index, cue in enumerate(cues):
        cue['start'] = offset
        cue['end'] = offset + len(cue['audio'])
        if index == len(cues) - 1:
            return cue['end']
        next_cue = cues[index + 1]
        default_gap = 650 if cue['scene'] != next_cue['scene'] else (
            425 if cue['speaker'] != next_cue['speaker'] else 240)
        offset = cue['end'] + cue.get('gap_ms', default_gap) * (RATE // 1000)


def sound_stems(root, config, cues, length):
    stems = {kind: np.zeros((length, 2), np.float32) for kind in ('environment', 'foley')}
    anchors = {cue['id']: cue for cue in cues}
    for item in config['sounds']:
        cue = anchors[item['cue_id']]
        kind = item['stem']
        require(item['cleared'] is True and item['no_intelligible_voices'] is True
                and 0 <= item['gain'] <= 1 and type(item['offset_ms']) is int
                and kind in stems
                and type(item['duration_ms']) is int and item['duration_ms'] > 0,
                'Invalid or uncleared soundscape event')
        start = cue['start'] + item['offset_ms'] * (RATE // 1000)
        count = item['duration_ms'] * (RATE // 1000)
        require(0 <= start < length and start + count <= length, 'Soundscape event exceeds timeline')
        source = decode(asset(root, item), 2)
        require(len(source) >= count or item.get('loop') is True, 'Sound shorter than configured slot')
        clip = np.tile(source, (int(np.ceil(count / len(source))), 1))[:count] if len(source) < count else source[:count].copy()
        fade = min(RATE // 100, count // 2)
        ramp = np.linspace(0, 1, fade, dtype=np.float32)[:, None]
        clip[:fade] *= ramp
        clip[-fade:] *= ramp[::-1]
        stems[kind][start:start + count] += clip * item['gain']
    return stems


def chapters(cues):
    group = []
    for cue in cues:
        require(cue['end'] - cue['start'] <= 120 * RATE, 'Take exceeds final ASR limit; split at a complete turn')
        if group and (cue['scene'] != group[0]['scene'] or cue['end'] - group[0]['start'] > 120 * RATE):
            yield group
            group = []
        group.append(cue)
    yield group


def run(manifest_path, soundscape_path, adapter, reviews, directory, cache):
    manifest = validate(manifest_path)
    root = manifest_path.parent
    soundscape = read(soundscape_path)
    directory.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=directory) as tmp:
        work = Path(tmp)
        report = {'status': 'blocked', 'checks': [], 'listening': 'Human listening not yet recorded',
                  'episode': manifest['episode'], 'manifest_sha256': digest(manifest_path),
                  'script_sha256': manifest['script']['sha256'], 'soundscape_sha256': digest(soundscape_path),
                  'adapter_id': adapter['id'], 'engine_sha256': digest(__file__),
                  'ffmpeg_version': ffmpeg('-version').stdout.decode().splitlines()[0]}
        cues = manifest['cues']
        for cue in cues:
            take = next(take for take in cue['takes'] if take['status'] == 'approved')
            cue['audio'] = decode(asset(root, take))
        length = make_timeline(cues)
        for cue in cues:
            take = next(take for take in cue['takes'] if take['status'] == 'approved')
            audio = cue['audio']
            flags = signal_flags(audio, take=True)
            if abs(len(audio) / RATE - take['duration_seconds']) > 0.06:
                flags.append('duration mismatch')
            if flags:
                report['checks'].append(dict(stage='raw_signal', cue_id=cue['id'], sample=cue['start'],
                                             seconds=cue['start'] / RATE, expected=cue['text'], actual=None,
                                             failure=', '.join(flags), severity='critical', disposition='blocked'))
            else:
                path = work / 'take.wav'
                wav(path, audio)
                check_content(report['checks'], 'raw', [cue], transcribe(path, adapter, cache),
                              digest(path), reviews)
        if any(row['disposition'] == 'blocked' for row in report['checks']):
            write_report(directory, report)
            return None
        voice = np.zeros((length, 2), np.float32)
        for cue in cues:
            voice[cue['start']:cue['end']] = cue['audio'] * cue['gain']
        stems = sound_stems(root, soundscape, cues, length)
        final = voice + stems['environment'] + stems['foley']
        for name, audio in {'dry': voice, **stems, 'mix': final}.items():
            require(float(np.max(np.abs(audio))) <= 0.98, f'{name} exceeds headroom; adjust input gains')
            wav(work / f'{name}.wav', audio)
        mp3 = work / 'preview.mp3'
        ffmpeg('-v', 'error', '-i', work / 'mix.wav', '-c:a', 'libmp3lame', '-b:a', '192k', mp3)
        encoded = decode(mp3, 2)
        require(len(encoded) == length, 'MP3 decoded sample count changed')
        metrics = ffmpeg('-i', mp3, '-af', 'loudnorm=print_format=json', '-f', 'null', '-').stderr.decode()
        report['loudness'] = json.JSONDecoder().raw_decode(metrics[metrics.rindex('{'):])[0]
        report['peak_dbfs'] = float(20 * np.log10(np.max(np.abs(encoded))))
        require(not signal_flags(encoded) and float(report['loudness']['input_tp']) <= -0.1,
                'Encoded preview fails clipping or true peak headroom check')
        for index, group in enumerate(chapters(cues), 1):
            start, end = group[0]['start'], group[-1]['end']
            for stage, audio in (('dry', voice), ('final', encoded)):
                path = work / 'chapter.wav'
                wav(path, audio[start:end])
                check_content(report['checks'], stage, group, transcribe(path, adapter, cache),
                              digest(path), reviews)
            preview = work / f'scene-{index:03d}.wav'
            wav(preview, encoded[start:min(end, start + 25 * RATE)])
        for name in ('take.wav', 'chapter.wav'):
            (work / name).unlink(missing_ok=True)
        write(work / 'timeline.json', [dict(id=cue['id'], scene=cue['scene'], speaker=cue['speaker'],
                                          start=cue['start'], end=cue['end'],
                                          source_sha256=next(t['sha256'] for t in cue['takes'] if t['status'] == 'approved'))
                                        for cue in cues])
        report['samples'] = length
        report['outputs'] = {file.name: digest(file) for file in sorted(work.iterdir())}
        report['status'] = ('blocked' if any(row['disposition'] == 'blocked' for row in report['checks'])
                            else 'awaiting_listening_and_owner_approval')
        version = hashlib.sha256(json.dumps(report, sort_keys=True).encode()).hexdigest()
        report['version'] = version
        write_report(work, report)
        destination = directory / version
        try:
            work.rename(destination)
        except OSError:
            require(destination.is_dir() and read(destination / 'qa.json') == report
                    and all(digest(destination / name) == checksum for name, checksum in report['outputs'].items()),
                    'Existing preview version failed verification')
        return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('validate').add_argument('manifest', type=Path)
    production = commands.add_parser('produce')
    for argument in ('manifest', 'soundscape', 'adapter', 'output', 'cache'):
        production.add_argument(argument, type=Path)
    production.add_argument('--reviews', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'validate':
            validate(args.manifest)
            return 0
        preview = run(args.manifest, args.soundscape, read(args.adapter),
                      read(args.reviews) if args.reviews else [], args.output, args.cache)
        print(preview if preview else args.output / 'qa.json')
        return 2 if not preview or read(preview / 'qa.json')['status'] == 'blocked' else 0
    except (ValueError, KeyError, TypeError, OSError, subprocess.TimeoutExpired) as error:
        # External adapter stderr and credentials must stay out of reports.
        parser.exit(2, f'Production blocked: {error}\n')


if __name__ == '__main__':
    raise SystemExit(main())
