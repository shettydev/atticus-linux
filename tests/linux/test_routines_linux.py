import configparser
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import linux


def unit(path):
    c = configparser.ConfigParser(interpolation=None, strict=False)
    c.optionxform = str
    c.read(path)
    return c


class RoutineUnitTests(unittest.TestCase):
    def setUp(self):
        self.a = linux.linux_brain(self)
        self.dir = tempfile.mkdtemp()
        for p in (mock.patch.object(self.a, 'DIR', self.dir), mock.patch.object(self.a, 'pref', side_effect=lambda k, d: d)):
            p.start()
            self.addCleanup(p.stop)

    def test_weekdays_oncalendar(self):
        row = self.a.routine_add('time', '08:00', 'summarize my calendar', days='weekdays')
        timer = unit(self.a.routine_job_path(row['id']))
        self.assertEqual(timer['Timer']['OnCalendar'], 'Mon..Fri *-*-* 08:00:00')
        self.assertEqual(timer['Timer']['Persistent'], 'false')
        service = unit(self.a.routine_job_path(row['id'])[:-len('.timer')] + '.service')
        self.assertIn(f'--routine-fire {row["id"]}', service['Service']['ExecStart'])

    def test_daily(self):
        row = self.a.routine_add('time', '21:30', 'water the plants')
        self.assertEqual(unit(self.a.routine_job_path(row['id']))['Timer']['OnCalendar'], '*-*-* 21:30:00')

    def test_interval_units(self):
        row = self.a.routine_add('interval', 1800, 'check my inbox')
        t = unit(self.a.routine_job_path(row['id']))['Timer']
        self.assertEqual((t['OnActiveSec'], t['OnUnitActiveSec']), ('1800s', '1800s'))

    def test_remove_disables_and_deletes(self):
        row = self.a.routine_add('time', '08:00', 'x')
        timer = self.a.routine_job_path(row['id'])
        ran = []
        with mock.patch.dict(os.environ, {'ATTICUS_DIR': ''}), mock.patch('subprocess.run', side_effect=lambda a, **k: ran.append(a) or subprocess.CompletedProcess(a, 0)):
            os.environ.pop('ATTICUS_DIR')
            with mock.patch.object(self.a, 'routine_job_path', return_value=timer):
                self.assertTrue(self.a.routine_remove(row['id']))
        self.assertIn(['systemctl', '--user', 'disable', '--now', os.path.basename(timer)], ran)
        self.assertFalse(os.path.exists(timer))
        self.assertFalse(os.path.exists(timer[:-len('.timer')] + '.service'))
        self.assertEqual(self.a.routines(), [])

    def test_install_enables_the_timer(self):
        ran = []
        with mock.patch.dict(os.environ, {}, clear=False), mock.patch('subprocess.run', side_effect=lambda a, **k: ran.append(a) or subprocess.CompletedProcess(a, 0)):
            os.environ.pop('ATTICUS_DIR', None)
            with mock.patch.object(self.a, 'routine_job_path', return_value=os.path.join(self.dir, 'atticus-routine-abcdef123456.timer')):
                self.a.routine_install({'id': 'abcdef123456', 'kind': 'time', 'at': '07:00', 'days': 'daily', 'ask': 'x', 'mode': 'cheapest', 'created': 1})
        self.assertEqual(ran[-2:], [['systemctl', '--user', 'daemon-reload'], ['systemctl', '--user', 'enable', '--now', 'atticus-routine-abcdef123456.timer']])

    def test_paused_skips_fire(self):
        row = self.a.routine_add('interval', 60, 'x', now=0)
        with mock.patch.object(self.a, 'pref', side_effect=lambda k, d: True if k == 'paused' else d):
            self.assertFalse(self.a.routine_fire(row['id'], now=120))

    def test_fire_queues_and_wakes_the_app(self):
        row = self.a.routine_add('interval', 60, 'x', now=0)
        ran = []
        with mock.patch.dict(os.environ, {}, clear=False), mock.patch('subprocess.run', side_effect=lambda a, **k: ran.append(a) or subprocess.CompletedProcess(a, 0)):
            os.environ.pop('ATTICUS_DIR', None)
            self.assertTrue(self.a.routine_fire(row['id'], now=125))
            self.assertFalse(self.a.routine_fire(row['id'], now=126))   # the same slot never runs twice
        self.assertIn(['systemctl', '--user', 'start', 'atticus.service'], ran)
        self.assertEqual(Path(self.dir, 'routine-queue.jsonl').read_text().strip(), json.dumps({'id': row['id']}))

    def test_repair_reinstalls_missing_timers(self):
        row = self.a.routine_add('time', '08:00', 'x')
        os.remove(self.a.routine_job_path(row['id']))
        self.a.routine_repair()
        self.assertTrue(os.path.exists(self.a.routine_job_path(row['id'])))


class WakeTests(unittest.TestCase):
    def test_queue_ids_reach_the_supervisor(self):
        from atticus_shell import routines as R
        sent = []
        R.drain(lambda: ['a1', 'b2'], sent.append)
        self.assertEqual(sent, [{'action': 'routine', 'id': 'a1'}, {'action': 'routine', 'id': 'b2'}])


if __name__ == '__main__':
    unittest.main()
