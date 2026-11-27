import json
import subprocess
import threading
import unittest

from tests import linux
from atticus_shell import audio as A

SOURCES = [
    {'name': 'alsa_output.pci-0000_00_1f.3.analog-stereo.monitor', 'description': 'Monitor of Built-in Audio',
     'monitor_of_sink': 'alsa_output.pci-0000_00_1f.3.analog-stereo', 'properties': {'device.class': 'monitor', 'device.bus': 'pci'}},
    {'name': 'alsa_input.pci-0000_00_1f.3.analog-stereo', 'description': 'Built-in Audio Analog Stereo',
     'monitor_of_sink': 'n/a', 'properties': {'device.bus': 'pci', 'device.form_factor': 'internal'}},
    {'name': 'bluez_input.AA_BB', 'description': 'AirPods', 'monitor_of_sink': None, 'properties': {'device.bus': 'bluetooth'}},
    {'name': 'alsa_input.usb-Blue_Yeti', 'description': 'Yeti', 'monitor_of_sink': 'n/a', 'properties': {'device.bus': 'usb'}},
]


def run_for(default_source='alsa_input.pci-0000_00_1f.3.analog-stereo'):
    def run(args, **kw):
        if args[:4] == ['pactl', '-f', 'json', 'list'] and args[4] == 'sources':
            return subprocess.CompletedProcess(args, 0, json.dumps(SOURCES), '')
        if args == ['pactl', 'get-default-source']:
            return subprocess.CompletedProcess(args, 0, default_source + '\n', '')
        raise AssertionError(args)
    return run


class MicTests(unittest.TestCase):
    def test_monitors_not_listed_and_sorted_by_name(self):
        mics = A.microphones(run_for())
        self.assertEqual([m.name for m in mics], ['AirPods', 'Built-in Audio Analog Stereo', 'Yeti'])

    def test_preferred_wins(self):
        mics = A.microphones(run_for())
        self.assertEqual(A.choose_mic('alsa_input.usb-Blue_Yeti', mics, 'bluez_input.AA_BB').name, 'Yeti')

    def test_bluetooth_default_prefers_builtin(self):
        mics = A.microphones(run_for())
        self.assertEqual(A.choose_mic('automatic', mics, 'bluez_input.AA_BB').name, 'Built-in Audio Analog Stereo')

    def test_choose_mic_falls_back_to_default(self):
        mics = A.microphones(run_for())
        self.assertEqual(A.choose_mic('gone-device', mics, 'alsa_input.usb-Blue_Yeti').name, 'Yeti')

    def test_unknown_default_takes_first(self):
        mics = A.microphones(run_for())
        self.assertEqual(A.choose_mic('automatic', mics, 'nothing').name, 'AirPods')
        self.assertIsNone(A.choose_mic('automatic', [], 'nothing'))

    def test_listen_mic_reads_default_source(self):
        self.assertEqual(A.listen_mic('automatic', run_for('alsa_input.usb-Blue_Yeti')).uid, 'alsa_input.usb-Blue_Yeti')


class FakeLayer:
    def __init__(self, volume=0.5, sink='speakers', fail_sets=0):
        self.vol = {sink: volume}
        self.default = sink
        self.fail_sets = fail_sets
        self.sets = []

    def snapshot(self):
        return A.VolumeSnapshot(self.default, self.vol[self.default])

    def set(self, snap, volume):
        if self.fail_sets:
            self.fail_sets -= 1
            return False
        self.vol[snap.sink] = volume
        self.sets.append((snap.sink, round(volume, 4)))
        return True


class Later:
    """Collects scheduled work; run() fires it in time order."""

    def __init__(self):
        self.queue = []

    def __call__(self, delay, fn):
        self.queue.append((delay, len(self.queue), fn))

    def run(self):
        while self.queue:
            self.queue.sort()
            _, _, fn = self.queue.pop(0)
            fn()


class DuckerTests(unittest.TestCase):
    def test_ramps_down_in_six_steps(self):
        layer, later = FakeLayer(0.5), Later()
        d = A.Ducker(layer, later)
        d.begin(True, level=0.18)
        later.run()
        self.assertEqual(len(layer.sets), 6)
        self.assertEqual(layer.sets[-1], ('speakers', 0.09))

    def test_disabled_does_nothing(self):
        layer = FakeLayer()
        A.Ducker(layer, Later()).begin(False)
        self.assertEqual(layer.sets, [])

    def test_end_restores_original_exactly(self):
        layer, later = FakeLayer(0.5), Later()
        d = A.Ducker(layer, later)
        d.begin(True)
        later.run()
        d.end()
        later.run()
        self.assertEqual(layer.vol['speakers'], 0.5)
        self.assertIsNone(d.original)

    def test_ducker_restores_snapshot_even_if_user_changed_other_sink(self):
        layer, later = FakeLayer(0.5, 'speakers'), Later()
        d = A.Ducker(layer, later)
        d.begin(True)
        later.run()
        layer.default = 'headphones'          # the user switched output mid-listen
        layer.vol['headphones'] = 0.3
        d.end(immediate=True)
        self.assertEqual(layer.vol, {'speakers': 0.5, 'headphones': 0.3})

    def test_failed_restore_retries(self):
        layer, later = FakeLayer(0.5), Later()
        d = A.Ducker(layer, later)
        d.begin(True)
        later.run()
        layer.fail_sets = 1
        d.end(immediate=True)
        self.assertIsNotNone(d.original)
        later.run()                           # retried a second later
        self.assertEqual(layer.vol['speakers'], 0.5)
        self.assertIsNone(d.original)

    def test_new_begin_cancels_old_ramp(self):
        layer, later = FakeLayer(0.5), Later()
        d = A.Ducker(layer, later)
        d.begin(True)
        d.end()
        later.run()
        self.assertEqual(layer.vol['speakers'], 0.5)


class PactlVolumeTests(unittest.TestCase):
    def test_snapshot_and_set(self):
        calls = []
        sinks = [{'name': 'speakers', 'volume': {'front-left': {'value_percent': '40%'}, 'front-right': {'value_percent': '40%'}}}]

        def run(args, **kw):
            calls.append(args)
            if args == ['pactl', 'get-default-sink']:
                return subprocess.CompletedProcess(args, 0, 'speakers\n', '')
            if args[:5] == ['pactl', '-f', 'json', 'list', 'sinks']:
                return subprocess.CompletedProcess(args, 0, json.dumps(sinks), '')
            return subprocess.CompletedProcess(args, 0, '', '')
        layer = A.PactlVolume(run)
        snap = layer.snapshot()
        self.assertEqual(snap, A.VolumeSnapshot('speakers', 0.4))
        self.assertTrue(layer.set(snap, 0.072))
        self.assertEqual(calls[-1], ['pactl', 'set-sink-volume', 'speakers', '7%'])


@linux.needs_gi
class GstMicTests(unittest.TestCase):
    def test_pcm_is_16k_mono_s16(self):
        got, done = [], threading.Event()

        def on_pcm(data):
            got.append(data)
            if sum(map(len, got)) >= 16000 * 2 // 4:
                done.set()
        mic = A.Mic(source='audiotestsrc wave=sine num-buffers=40')
        mic.start('', on_pcm)
        self.addCleanup(mic.stop)
        self.assertTrue(done.wait(5))
        self.assertTrue(all(len(b) % 2 == 0 for b in got))
        fields = dict(f.strip().split('=', 1) for f in mic.caps_string().split(',')[1:])
        self.assertEqual(fields, {'format': '(string)S16LE', 'rate': '(int)16000', 'channels': '(int)1', 'layout': '(string)interleaved'})


if __name__ == '__main__':
    unittest.main()
