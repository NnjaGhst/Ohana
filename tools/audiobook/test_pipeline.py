import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pipeline as audio
import release

ROOT = Path(__file__).resolve().parent
PHRASES = {440: 'I heard the last word.', 550: 'You heard it too.', 660: 'I heard the last word.'}


def tone(freq, count=24000):
    samples = np.zeros((count, 1), np.float32)
    offset = np.arange(1000, count - 1000)
    samples[offset, 0] = 0.15 * np.sin(2 * np.pi * freq * offset / audio.RATE)
    return samples


def synthetic_asr(path):
    import wave
    with wave.open(path, 'rb') as source:
        data = np.frombuffer(source.readframes(source.getnframes()), dtype='<i4')
        data = data.reshape(-1, source.getnchannels())[:, 0] / 2**31
    indices = np.flatnonzero(np.abs(data) > 0.08)
    runs = np.split(indices, np.flatnonzero(np.diff(indices) > 1000) + 1)
    segments = []
    for run in runs:
        if not len(run):
            continue
        start, end = int(run[0]), int(run[-1] + 1)
        clip = data[start:end]
        freq = np.argmax(np.abs(np.fft.rfft(clip))) * audio.RATE / len(clip)
        text = PHRASES[min(PHRASES, key=lambda candidate: abs(candidate - freq))]
        segments.append(dict(start=start / audio.RATE, end=end / audio.RATE, text=text))
    print(json.dumps(dict(text=' '.join(seg['text'] for seg in segments), segments=segments, confidence=0.99)))


class Acceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.episode = dict(id='elsewhere-reboot-2026-002', series_id=release.SERIES, number=2)
        lines = [
            dict(id='c1', scene='s1', order=1, speaker='Hex', role='narration', text=PHRASES[440]),
            dict(id='c2', scene='s1', order=2, speaker='Ninja', role='dialogue', text=PHRASES[550]),
        ]
        audio.write(self.root / 'script.json', lines)
        for name, freq in [('one.wav', 440), ('two.wav', 550), ('pickup.wav', 660), ('rain.wav', 100)]:
            audio.wav(self.root / name, tone(freq))
        self.manifest = dict(version=1, episode=self.episode, script=self.ref('script.json'),
                             cast={'Hex': audio.WADE, 'Ninja': audio.HALLIE}, cues=[])
        for line, name, voice, gain in zip(lines, ('one.wav', 'two.wav'), (audio.WADE, audio.HALLIE), (1, 0.9)):
            self.manifest['cues'].append(dict(**line, voice_id=voice, gain=gain,
                                              takes=[self.take(name, voice)]))
        self.manifest_path = self.root / 'manifest.json'
        self.sound_path = self.root / 'sounds.json'
        self.sounds = [dict(**self.ref('rain.wav'), cue_id='c2', stem='environment', gain=0.03,
                            duration_ms=200, offset_ms=0, cleared=True, no_intelligible_voices=True)]
        self.adapter = dict(id='synthetic-frequency-v1',
                            command=[sys.executable, str(ROOT / 'test_pipeline.py'), 'asr'])
        self.save()

    def ref(self, name):
        return dict(asset=name, sha256=audio.digest(self.root / name))

    def take(self, name, voice):
        return dict(**self.ref(name), id=name, voice_id=voice, speed=1, pitch=1,
                    duration_seconds=0.5, status='approved')

    def save(self):
        audio.write(self.manifest_path, self.manifest)
        audio.write(self.sound_path, dict(sounds=self.sounds))

    def produce(self):
        return audio.run(self.manifest_path, self.sound_path, self.adapter, [], self.root / 'out',
                         self.root / 'cache')

    def test_episode_1_regression_and_intentional_repetition(self):
        fixture = audio.read(ROOT / 'episode-1-regression.json')
        for cue in fixture['cues']:
            self.assertEqual(audio.content_failure(cue['expected'], cue['rejected']), 'unexpected repetition')
            self.assertIsNone(audio.content_failure(cue['expected'], cue['corrected']))
        self.assertIsNone(audio.content_failure('Why? Why?', 'Why why'))
        self.assertIsNone(audio.content_failure('There are TWO doors.', 'there are 2 doors'))

    def test_short_ambiguous_blank_and_complete_sentences(self):
        for expected, heard, confidence, reason in [
            ('Why?', 'Why?', 0.99, 'short utterance'),
            ('Speak a complete sentence.', 'Speak a complete sentence.', 0.73, 'low ASR confidence'),
            ('Speak a complete sentence.', '', 0, 'blank ASR'),
            ('Speak a complete sentence.', 'Speak a complete', 0.99, 'missing'),
        ]:
            row = []
            actual = dict(text=heard, confidence=confidence, segments=[])
            audio.check_content(row, 'raw', [dict(text=expected, id='c1', start=48000)], actual, 'a', [])
            self.assertEqual(row[0]['disposition'], 'blocked')
            self.assertIn(reason, row[0]['failure'])
        row = []
        review = dict(stage='raw', cue_id='c1', audio_sha256='a', expected='Why?', transcript='Why?',
                      listened_in_context=True, reviewer='Dex', notes='Verified complete utterance')
        audio.check_content(row, 'raw', [dict(text='Why?', id='c1', start=0)],
                            dict(text='Why?', confidence=0.99, segments=[]), 'a', [review])
        self.assertEqual(row[0]['disposition'], 'reviewed')

    def test_manifest_rejects_missing_duplicate_and_wrong_episode(self):
        self.manifest['cues'].pop()
        self.save()
        with self.assertRaisesRegex(ValueError, 'Missing or extra'):
            audio.validate(self.manifest_path)
        line = dict(self.manifest['cues'][0], order=2)
        self.manifest['cues'].append(line)
        script = audio.read(self.root / 'script.json')
        script[1] = dict(script[0], order=2)
        audio.write(self.root / 'script.json', script)
        self.manifest['script'] = self.ref('script.json')
        self.save()
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            audio.validate(self.manifest_path)
        self.episode['number'] = 3
        self.save()
        with self.assertRaisesRegex(ValueError, 'identity'):
            audio.validate(self.manifest_path)

    def test_determinism_pickup_and_fixed_duration_stems(self):
        first = self.produce()
        self.assertEqual(self.produce(), first)
        report = audio.read(first / 'qa.json')
        self.assertEqual(report['status'], 'awaiting_listening_and_owner_approval')
        self.assertEqual({row['stage'] for row in report['checks']}, {'raw', 'dry', 'final'})
        self.manifest['cues'][0]['takes'][0]['status'] = 'rejected'
        self.manifest['cues'][0]['takes'].append(self.take('pickup.wav', audio.WADE))
        self.save()
        second = self.produce()
        timeline = audio.read(first / 'timeline.json')
        self.assertEqual(timeline[1], audio.read(second / 'timeline.json')[1])
        offset = timeline[1]['start']
        np.testing.assert_array_equal(audio.decode(first / 'dry.wav', 2)[offset:],
                                      audio.decode(second / 'dry.wav', 2)[offset:])
        np.testing.assert_allclose(audio.decode(second / 'dry.wav', 2)[offset:, 0],
                                   audio.decode(self.root / 'two.wav')[:, 0] * 0.9, atol=1e-7)
        self.assertEqual(audio.digest(first / 'environment.wav'), audio.digest(second / 'environment.wav'))
        self.sounds[0].update(self.ref('one.wav'))
        self.save()
        third = self.produce()
        self.assertNotEqual(audio.digest(second / 'environment.wav'), audio.digest(third / 'environment.wav'))
        self.assertEqual(audio.read(second / 'qa.json')['samples'], audio.read(third / 'qa.json')['samples'])
        self.assertEqual(audio.digest(second / 'dry.wav'), audio.digest(third / 'dry.wav'))

    def test_release_gates_and_rollback(self):
        preview = self.produce()
        master = audio.digest(preview / 'preview.mp3')
        approval = dict(owner='NnjaGhst', owner_approval_reference='test fixture only',
                        qa_sha256=audio.digest(preview / 'qa.json'), episode_id=self.episode['id'],
                        master_sha256=master, listening=dict(reviewer='fixture', passed=True,
                                                            scope='full_master', notes='synthetic'))
        with self.assertRaisesRegex(ValueError, 'checksum|master'):
            release.validate_approval(preview, approval, '0' * 64)
        with self.assertRaisesRegex(ValueError, 'Listening'):
            release.validate_approval(preview, dict(approval, listening={}), master)
        repo = self.root / 'repo'
        repo.mkdir()
        working = Path.cwd()
        os.chdir(repo)
        self.addCleanup(os.chdir, working)
        release.git('init', '-q', '-b', 'main')
        release.git('config', 'user.name', 'Fixture')
        release.git('config', 'user.email', 'fixture@example.invalid')
        remote = self.root / 'origin.git'
        release.git('init', '--bare', '-q', '-b', 'main', str(remote))
        release.git('remote', 'add', 'origin', str(remote))
        prior = 'series/elsewhere/reboot-2026/episodes/episode-002/audio/old.mp3'
        meta = str(Path(prior).parent.parent / 'ohana-metadata.json')
        other = dict(id='elsewhere-002', seriesId='elsewhere', number=2, audio='other.mp3')
        entry = dict(id=self.episode['id'], seriesId=release.SERIES, number=2, title='Fixture',
                     metadata=meta, audio=prior, images=[{'path': 'image.jpg'}], text='story.txt')
        Path('stories', prior).parent.mkdir(parents=True)
        Path('stories', prior).write_bytes(b'prior audio')
        metadata = dict(episode=2, series='Elsewhere (2026 Reboot)', title='Fixture', audio_file=prior,
                        illustrations=['image.jpg'])
        audio.write(Path('stories', meta), metadata)
        audio.write(Path(release.CATALOG), dict(episodes=[other, entry]))
        release.git('add', 'stories')
        release.git('commit', '-qm', 'Fixture')
        release.git('push', 'origin', 'main')
        base = release.remote_main()
        plan = release.stage(preview, approval, master, self.root / 'plan.json')
        self.assertEqual(release.remote_main(), base)
        with self.assertRaisesRegex(ValueError, 'Catalog'):
            release.verify(plan, base, 'https://example.invalid/audio.mp3', ['unused'])
        release.publish(plan, master, preview, approval)
        with self.assertRaisesRegex(ValueError, 'Main changed'):
            release.publish(plan, master, preview, approval)
        live = release.remote_main()
        response = io.BytesIO(b'wrong public version')
        response.url = 'https://example.invalid/audio.mp3'
        with patch.object(release, 'urlopen', return_value=response):
            with self.assertRaisesRegex(ValueError, 'Public audio differs'):
                release.verify(plan, live, response.url, ['unused'])
        response = io.BytesIO((preview / 'preview.mp3').read_bytes())
        response.url = 'https://example.invalid/audio.mp3'
        live_entry = dict(id=self.episode['id'], seriesId=release.SERIES, audioPath=prior, audioUrl=response.url)
        with patch.object(release, 'urlopen', return_value=response):
            with self.assertRaisesRegex(ValueError, 'stale'):
                release.verify(plan, live, response.url, [
                    sys.executable, '-c', f'print({json.dumps(live_entry)!r})'])
        response = io.BytesIO((preview / 'preview.mp3').read_bytes())
        response.url = 'https://example.invalid/audio.mp3'
        live_entry['audioPath'] = plan['audio']
        with patch.object(release, 'urlopen', return_value=response):
            release.verify(plan, live, response.url, [
                sys.executable, '-c', f'print({json.dumps(live_entry)!r})'])
        rollback = release.rollback(plan)
        self.assertEqual(release.blob(rollback, release.CATALOG), release.blob(base, release.CATALOG))
        self.assertEqual(release.blob(rollback, f'stories/{prior}'), b'prior audio')
        self.assertEqual(release.blob(rollback, 'stories/' + plan['audio']), (preview / 'preview.mp3').read_bytes())


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'asr':
        synthetic_asr(sys.argv[2])
    else:
        unittest.main()
