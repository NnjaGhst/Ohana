import json
from pathlib import Path
import tempfile
import unittest

import recording_queue as q


class QueueTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.d = Path(temp.name)
        self.ledger = self.d / 'queue.json'
        self.script = self.d / 'script.json'
        self.cast = self.d / 'cast.json'
        q.save(self.script, [dict(id=f'c{i}', speaker='speaker', order=i, text='Please now.')
                             for i in range(1, 4)])
        q.save(self.cast, dict(speaker=dict(voice_id='voice')))
        self.wav = self.d / 'verified.wav'
        self.wav.write_bytes(b'fixture PCM')
        self.receipt = dict(sha256=q.digest(self.wav), bytes=self.wav.stat().st_size,
                            duration_seconds=1, file_id='file-x', library_file_id='libfile-x',
                            roundtrip_verified=True, version=0)
        self.settings = dict(account='demo', authorization_reference='fixture', pilot_verified=True,
                             max_total_calls=3, max_total_characters=40, max_calls_per_utc_day=3,
                             min_interval_seconds=1, provider_command=[], retain_command=[])
        self.calls = []
        self.t = 100

    def clock(self):
        self.t += 2
        return self.t

    def provider(self, command, request, timeout):
        self.calls.append(request['cue_id'])
        durable = q.read(self.ledger)['cues'][request['cue_id']]
        self.assertEqual(durable['state'], 'submitted')
        return dict(status='ok', url='https://fixture.invalid/private')

    def retain(self, command, data, timeout):
        return dict(self.receipt, verified_wav=str(self.wav), transcript='Please now.')

    def init(self, existing=()):
        return q.init(self.script, self.cast, 'episode', existing)

    def run_queue(self, s, provider=None, retain=None):
        return q.run(self.ledger, s, self.settings, provider or self.provider,
                     retain or self.retain, self.clock)

    def test_existing_order_and_restart(self):
        s = self.init([dict(cue_id='c2', take_id='old', disposition='candidate', receipt=self.receipt)])
        self.assertEqual(q.plan(s)['missing'], 2)
        self.assertEqual(self.run_queue(s), 'queue_drained')
        self.assertEqual(self.calls, ['c1', 'c3'])
        self.run_queue(q.read(self.ledger))
        self.assertEqual(self.calls, ['c1', 'c3'])
        self.assertEqual(s['cues']['c2']['attempts'][0]['take_id'], 'old')

    def test_crash_after_submission_never_reissues(self):
        class Crash(BaseException):
            pass
        def crash(command, request, timeout):
            self.provider(command, request, timeout)
            raise Crash
        with self.assertRaises(Crash):
            self.run_queue(self.init(), provider=crash)
        s = q.read(self.ledger)
        self.assertEqual(self.run_queue(s), 'queue_drained')
        self.assertEqual(self.calls, ['c1', 'c2', 'c3'])
        self.assertEqual(s['cues']['c1']['attempts'][0]['reason'], 'ambiguous_submission')

    def test_expired_url_and_bad_checksum_hold_but_other_cues_continue(self):
        def fail_two(command, data, timeout):
            if data['request']['cue_id'] == 'c1':
                raise TimeoutError('expired private URL')
            result = self.retain(command, data, timeout)
            if data['request']['cue_id'] == 'c2':
                result['sha256'] = '0' * 64
            return result
        s = self.init()
        self.run_queue(s, retain=fail_two)
        self.assertEqual([s['cues'][c]['state'] for c in ('c1', 'c2', 'c3')],
                         ['received', 'received', 'candidate'])
        self.assertNotIn('fixture.invalid', self.ledger.read_text())
        self.run_queue(q.read(self.ledger))
        self.assertEqual(self.calls, ['c1', 'c2', 'c3'])

    def test_provider_rate_limit_stops_and_does_not_retry(self):
        def rate(command, request, timeout):
            self.provider(command, request, timeout)
            return dict(status='rate_limit')
        s = self.init()
        self.assertEqual(self.run_queue(s, provider=rate), 'provider_blocked')
        self.assertEqual(self.calls, ['c1'])
        self.assertEqual(s['cues']['c1']['state'], 'hold')
        self.assertEqual(s['cues']['c2']['state'], 'missing')
        self.assertEqual(self.run_queue(q.read(self.ledger)), 'provider_blocked_pending_authorization')
        self.assertEqual(self.calls, ['c1'])

    def test_budget_exhaustion_and_daily_limit(self):
        s = self.init()
        self.settings['max_total_calls'] = 1
        self.assertEqual(self.run_queue(s), 'budget_exhausted')
        self.settings['max_total_calls'] = 3
        self.settings['max_calls_per_utc_day'] = 1
        self.assertEqual(self.run_queue(s), 'daily_limit')
        self.assertEqual(self.calls, ['c1'])

    def test_repeat_and_missing_are_held(self):
        def mismatch(command, data, timeout):
            r = self.retain(command, data, timeout)
            r['transcript'] = {'c1': 'Please please now.', 'c2': 'Please', 'c3': 'Please now'}[data['request']['cue_id']]
            return r
        s = self.init()
        self.run_queue(s, retain=mismatch)
        self.assertEqual([s['cues'][c]['state'] for c in ('c1', 'c2', 'c3')],
                         ['hold', 'hold', 'candidate'])

    def test_held_existing_take_is_not_scheduled(self):
        s = self.init([dict(cue_id='c2', take_id='old', disposition='review_pending',
                            receipt=self.receipt)])
        self.assertEqual(self.run_queue(s), 'queue_drained')
        self.assertEqual(self.calls, ['c1', 'c3'])
        self.assertEqual(s['cues']['c2']['state'], 'hold')

    def test_explicit_pilot_is_one_request(self):
        self.settings.update(pilot_verified=False, pilot_only=True, max_total_calls=1)
        self.assertEqual(self.run_queue(self.init()), 'budget_exhausted')
        self.assertEqual(self.run_queue(q.read(self.ledger)), 'budget_exhausted')
        self.assertEqual(self.calls, ['c1'])

    def test_reconciliation_keeps_rejected_original(self):
        s = self.init([dict(cue_id='c1', take_id='old', disposition='rejected', receipt=self.receipt)])
        d = dict(cue_id='c1', take_id='old', receipt=self.receipt, evidence_reference='private review')
        with self.assertRaises(ValueError):
            q.resolve(s, d)
        q.resolve(s, dict(d, take_id='reviewed-pickup'))
        self.assertEqual([a['state'] for a in s['cues']['c1']['attempts']], ['rejected', 'candidate'])
        self.run_queue(s)
        self.assertEqual(self.calls, ['c2', 'c3'])

    def test_live_pilot_and_budget_required(self):
        self.settings['pilot_verified'] = False
        with self.assertRaises(ValueError):
            self.run_queue(self.init())
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
