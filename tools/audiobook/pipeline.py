"""Offline audiobook production. Run with Python 3.11+, NumPy and FFmpeg."""
import argparse
import hashlib
import json
import re
import shutil
import struct
from contextlib import ExitStack
import subprocess
import tempfile
import unicodedata
import wave
from pathlib import Path

import numpy as np

RATE = 48000
BLOCK = RATE * 2
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
    adapter_hash = hashlib.sha256(json.dumps(adapter['command']).encode()).hexdigest()
    key = hashlib.sha256(json.dumps([checksum, adapter['id'], adapter_hash]).encode()).hexdigest()
    cached = cache / (key + '.json')
    if cached.is_file():
        envelope = read(cached)
        require(envelope['audio_sha256'] == checksum and envelope['adapter_id'] == adapter['id']
                and envelope['adapter_command_sha256'] == adapter_hash,
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
            write(name, {'audio_sha256': checksum, 'adapter_id': adapter['id'],
                         'adapter_command_sha256': adapter_hash, 'result': result})
            name.replace(cached)
        finally:
            name.unlink(missing_ok=True)
    return result


def cache_source(cache, source, checksum):
    destination = cache / 'sources' / checksum
    destination.parent.mkdir(exist_ok=True)
    if not destination.exists():
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temporary:
            name = Path(temporary.name)
        try:
            shutil.copyfile(source, name)
            require(digest(name) == checksum, 'Source changed during ingestion')
            name.replace(destination)
        finally:
            name.unlink(missing_ok=True)
    require(digest(destination) == checksum, 'Cached source checksum mismatch')
    return destination


def check_content(report, stage, entries, actual, audio_hash, reviews, context=None):
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
    contextual = context and context['verified']
    # Context confirms a clean isolated transcript. A conflicting isolated
    # transcript still requires listening, even if context supplies the word.
    context_pass = contextual and not failure and not first.get('proper_names')
    reviewable = (not failure or contextual) and not cleared and not context_pass
    report.append(dict(stage=stage, cue_id=first['id'], cue_ids=[entry['id'] for entry in entries],
                       affected_cue_id=location['id'], sample=sample, seconds=sample / RATE, expected=expected,
                       actual=actual, failure=', '.join(reasons) or None,
                       severity='critical' if reasons else 'none', review=review,
                       context=context, render_for_review=bool(reviewable),
                       disposition=('reviewed' if cleared and reasons else
                                    'context_verified' if context_pass else 'blocked' if reasons else 'pass'),
                       audio_sha256=audio_hash))


def contextual_check(cues, index, isolated, work, adapter, cache):
    cue = cues[index]
    expected = words(cue['text'])
    heard = words(isolated['text'])
    if len(expected) > 3 or not heard:
        return None
    # Only a strict omission is eligible for a review render. Extra words,
    # repeats and substitutions cannot be cleared by contextual ASR.
    remaining = iter(expected)
    if not all(any(token == want for want in remaining) for token in heard):
        return None
    neighbors = [item for item in cues[max(0, index - 1):index + 2]
                 if item['scene'] == cue['scene'] and item['samples'] <= RATE * 12]
    if len(neighbors) < 2:
        return None
    origin = neighbors[0]['start']
    length = neighbors[-1]['end'] - origin
    if length > RATE * 28:
        return None
    left, right = cue['start'] - origin, cue['end'] - origin
    audio = np.zeros((length, 1), np.float32)
    for item in neighbors:
        audio[item['start'] - origin:item['end'] - origin] = frames(item['raw'], 0, item['samples'], 1)
    path = work / 'context.wav'
    wav(path, audio)
    checksum = digest(path)
    with_cue = transcribe(path, adapter, cache)
    timestamps = [word for segment in with_cue['segments'] for word in segment.get('words', [])]
    if words(' '.join(item['text'] for item in timestamps)) != words(with_cue['text']):
        return dict(verified=False, audio_sha256=checksum, actual=with_cue)
    target = []
    for item in timestamps:
        start, end = item['start'], item['end']
        if not 0 <= start <= end <= length / RATE + 0.1:
            return dict(verified=False, audio_sha256=checksum, actual=with_cue)
        if left <= (start + end) * RATE / 2 < right:
            target.extend(words(item['text']))
    audio[left:right] = 0
    wav(path, audio)
    control_sha = digest(path)
    without_cue = transcribe(path, adapter, cache)
    verified = (target == expected
                and content_failure(' '.join(item['text'] for item in neighbors), with_cue['text']) is None
                and content_failure(' '.join(item['text'] for item in neighbors if item is not cue),
                                    without_cue['text']) is None)
    return dict(verified=verified, audio_sha256=checksum, actual=with_cue, cue_words=target,
                control_sha256=control_sha, control=without_cue)


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
    lines += [f"{row['seconds']:.3f}s {row.get('affected_cue_id', row['cue_id'])} [{row['stage']}] {row['disposition']}: {row['failure']}"
              for row in report['checks'] if row['failure']]
    (out / 'qa.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def make_timeline(cues):
    offset = 0
    for index, cue in enumerate(cues):
        cue['start'] = offset
        cue['end'] = offset + cue['samples']
        if index == len(cues) - 1:
            return cue['end']
        next_cue = cues[index + 1]
        default_gap = 650 if cue['scene'] != next_cue['scene'] else (
            425 if cue['speaker'] != next_cue['speaker'] else 240)
        offset = cue['end'] + cue.get('gap_ms', default_gap) * (RATE // 1000)


def raw_file(source, destination, channels):
    if not destination.exists():
        ffmpeg('-v', 'error', '-xerror', '-i', source, '-map', '0:a:0', '-ac', channels,
               '-ar', RATE, '-f', 'f32le', destination)
    size = destination.stat().st_size
    require(size > 0 and size % (channels * 4) == 0, 'Invalid decoded PCM')
    return size // (channels * 4)


def frames(path, start, end, channels=2, offset=0, dtype='<f4'):
    with path.open('rb') as source:
        source.seek(offset + start * channels * 4)
        data = source.read((end - start) * channels * 4)
    require(len(data) == (end - start) * channels * 4, 'Truncated PCM')
    return np.frombuffer(data, dtype=dtype).reshape(-1, channels)


def pcm(audio):
    return np.rint(audio.astype(np.float64) * (2**31 - 1)).astype('<i4')


def wave_header(length):
    size = length * 8
    fmt = struct.pack('<4sIHHIIHH', b'fmt ', 16, 1, 2, RATE, RATE * 8, 8, 32)
    if size + 36 <= 0xffffffff:
        return struct.pack('<4sI4s', b'RIFF', size + 36, b'WAVE') + fmt + struct.pack('<4sI', b'data', size)
    return (struct.pack('<4sI4s4sIQQQI', b'RF64', 0xffffffff, b'WAVE', b'ds64', 28,
                        size + 72, size, length, 0)
            + fmt + struct.pack('<4sI', b'data', 0xffffffff))


def render_stems(root, config, cues, length, work):
    anchors = {cue['id']: cue for cue in cues}
    layers = ('dry', 'environment', 'foley')
    events = {layer: [] for layer in layers}
    for cue in cues:
        events['dry'].append((cue['start'], cue['end'], cue['raw'], cue['samples'], cue['gain'], 0, 1))
    for item in config['sounds']:
        cue = anchors[item['cue_id']]
        kind = item['stem']
        require(item['cleared'] is True and item['no_intelligible_voices'] is True
                and 0 <= item['gain'] <= 1 and type(item['offset_ms']) is int
                and kind in ('environment', 'foley')
                and type(item['duration_ms']) is int and item['duration_ms'] > 0,
                'Invalid or uncleared soundscape event')
        start = cue['start'] + item['offset_ms'] * (RATE // 1000)
        count = item['duration_ms'] * (RATE // 1000)
        require(0 <= start < length and start + count <= length, 'Soundscape event exceeds timeline')
        source = work / f"effect-{item['sha256']}.raw"
        samples = raw_file(asset(root, item), source, 2)
        require(samples >= count or item.get('loop') is True, 'Sound shorter than configured slot')
        events[kind].append((start, start + count, source, samples, item['gain'], min(480, count // 2), 2))
    pending = {kind: iter(sorted(enumerate(events[kind]), key=lambda e: e[1][0])) for kind in layers}
    upcoming = {kind: next(pending[kind], None) for kind in layers}
    active = {kind: {} for kind in layers}
    header = wave_header(length)
    with ExitStack() as stack:
        outputs = {name: stack.enter_context((work / f'{name}.wav').open('wb'))
                   for name in (*layers, 'mix')}
        for output in outputs.values():
            output.write(header)
        for start in range(0, length, BLOCK):
            end = min(start + BLOCK, length)
            blocks = {kind: np.zeros((end - start, 2), np.float32) for kind in layers}
            for kind in layers:
                while upcoming[kind] and upcoming[kind][1][0] < end:
                    index, event = upcoming[kind]
                    active[kind][index] = event
                    upcoming[kind] = next(pending[kind], None)
                for index in sorted(active[kind]):
                    begin, finish, source, period, gain, fade, channels = active[kind][index]
                    first, last = max(start, begin), min(end, finish)
                    cursor = first
                    while cursor < last:
                        pos = (cursor - begin) % period
                        count = min(last - cursor, period - pos)
                        audio = frames(source, pos, pos + count, channels).copy()
                        require(np.isfinite(audio).all(), 'Non-finite render PCM')
                        if fade:
                            index_in_event = np.arange(cursor - begin, cursor - begin + count)
                            factor = np.minimum(index_in_event, finish - begin - 1 - index_in_event)
                            audio *= np.minimum(1, factor / max(1, fade - 1)).astype(np.float32)[:, None]
                        blocks[kind][cursor - start:cursor - start + count] += audio * gain
                        cursor += count
                    if finish <= end:
                        del active[kind][index]
            blocks['mix'] = blocks['dry'] + blocks['environment'] + blocks['foley']
            for name, audio in blocks.items():
                require(float(np.max(np.abs(audio))) <= 0.98, f'{name} exceeds headroom; adjust input gains')
                if np.any(audio):
                    outputs[name].write(pcm(audio).tobytes())
                else:
                    outputs[name].seek(audio.size * 4, 1)
        for output in outputs.values():
            output.truncate(len(header) + length * 8)
    return len(header)


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
            source = cache_source(cache, asset(root, take), take['sha256'])
            cue['raw'] = work / f"voice-{take['sha256']}.raw"
            cue['samples'] = raw_file(source, cue['raw'], 1)
        length = make_timeline(cues)
        for index, cue in enumerate(cues):
            take = next(take for take in cue['takes'] if take['status'] == 'approved')
            audio = frames(cue['raw'], 0, cue['samples'], 1)
            require(np.isfinite(audio).all(), 'Non-finite take PCM')
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
                actual = transcribe(path, adapter, cache)
                context = contextual_check(cues, index, actual, work, adapter, cache)
                check_content(report['checks'], 'raw', [cue], actual, digest(path), reviews, context)
            del audio
        if any(row['disposition'] == 'blocked' and not row.get('render_for_review')
               for row in report['checks']):
            write_report(directory, report)
            return None
        dry_offset = render_stems(root, soundscape, cues, length, work)
        mp3 = work / 'preview.mp3'
        ffmpeg('-v', 'error', '-i', work / 'mix.wav', '-c:a', 'libmp3lame', '-b:a', '192k', mp3)
        encoded = work / 'decoded-preview.raw'
        require(raw_file(mp3, encoded, 2) == length, 'MP3 decoded sample count changed')
        metrics = ffmpeg('-nostats', '-i', mp3, '-af', 'loudnorm=print_format=json', '-f', 'null', '-').stderr.decode()
        report['loudness'] = json.JSONDecoder().raw_decode(metrics[metrics.rindex('{'):])[0]
        peak = max(float(np.max(np.abs(frames(encoded, start, min(start + BLOCK, length)))))
                   for start in range(0, length, BLOCK))
        require(0.001 <= peak < 0.999 and float(report['loudness']['input_tp']) <= -0.1,
                'Encoded preview fails silence, clipping or true peak headroom check')
        report['peak_dbfs'] = float(20 * np.log10(peak))
        for index, group in enumerate(chapters(cues), 1):
            start, end = group[0]['start'], group[-1]['end']
            for stage in ('dry', 'final'):
                if stage == 'dry':
                    audio = frames(work / 'dry.wav', start, end, offset=dry_offset,
                                   dtype='<i4').astype(np.float32) / np.float32(2**31)
                else:
                    audio = frames(encoded, start, end)
                path = work / 'chapter.wav'
                wav(path, audio)
                check_content(report['checks'], stage, group, transcribe(path, adapter, cache),
                              digest(path), reviews)
                del audio
            preview = work / f'scene-{index:03d}.wav'
            wav(preview, frames(encoded, start, min(end, start + 25 * RATE)))
        for path in [*work.glob('*.raw'), *(work / name for name in ('take.wav', 'chapter.wav', 'context.wav'))]:
            path.unlink(missing_ok=True)
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
