"""Microphones, 16 kHz capture, and quieting other audio while Atticus listens (PipeWire through its PulseAudio API).

Ports chooseMic (Atticus.swift:286) and AudioQuiet (Atticus.swift:630): a picked device wins; a Bluetooth default
falls back to the built-in mic (Bluetooth headsets drop to call quality while their mic is open); ducking snapshots
one sink's volume, ramps in six 25 ms steps and puts that same sink back, retrying a failed restore a second later.
"""
import json
import subprocess
import threading
from dataclasses import dataclass

CAPS = 'audio/x-raw,format=S16LE,rate=16000,channels=1,layout=interleaved'


def _run(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, timeout=3, **kw)


@dataclass(frozen=True)
class MicrophoneDevice:
    uid: str
    name: str
    bus: str = ''
    builtin: bool = False


def microphones(run=_run):
    try:
        rows = json.loads(run(['pactl', '-f', 'json', 'list', 'sources']).stdout or '[]')
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    mics = []
    for r in rows:
        props = r.get('properties') or {}
        if r.get('monitor_of_sink') not in (None, '', 'n/a') or props.get('device.class') == 'monitor' or r['name'].endswith('.monitor'):
            continue
        bus = props.get('device.bus', '')
        mics.append(MicrophoneDevice(r['name'], r.get('description') or r['name'], bus,
                                     props.get('device.form_factor') == 'internal' or bus == 'pci'))
    return sorted(mics, key=lambda m: m.name.casefold())


def choose_mic(preferred, devices, default_uid):
    if preferred != 'automatic':
        picked = next((d for d in devices if d.uid == preferred), None)
        if picked:
            return picked
    standard = next((d for d in devices if d.uid == default_uid), None)
    if standard is None:
        return devices[0] if devices else None
    if standard.bus == 'bluetooth':
        return next((d for d in devices if d.builtin), standard)
    return standard


def default_source(run=_run):
    try:
        return (run(['pactl', 'get-default-source']).stdout or '').strip()
    except (OSError, subprocess.SubprocessError):
        return ''


def listen_mic(preferred, run=_run):
    return choose_mic(preferred, microphones(run), default_source(run))


def microphone_choices(run=_run):
    return [{'uid': m.uid, 'name': m.name} for m in microphones(run)]


@dataclass(frozen=True)
class VolumeSnapshot:
    sink: str
    volume: float       # 0..1 (1 = 100%)


class PactlVolume:
    """The default sink's volume through pactl. Fixed-volume sinks (no volume field) can't be quieted."""

    def __init__(self, run=_run):
        self.run = run

    def snapshot(self):
        try:
            name = (self.run(['pactl', 'get-default-sink']).stdout or '').strip()
            sinks = json.loads(self.run(['pactl', '-f', 'json', 'list', 'sinks']).stdout or '[]')
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
        sink = next((s for s in sinks if s.get('name') == name), None)
        channels = (sink or {}).get('volume') or {}
        values = [int(str(c.get('value_percent', '')).rstrip('%')) for c in channels.values() if str(c.get('value_percent', '')).rstrip('%').isdigit()]
        return VolumeSnapshot(name, max(values) / 100) if values else None

    def set(self, snap, volume):
        try:
            return self.run(['pactl', 'set-sink-volume', snap.sink, f'{round(volume * 100)}%']).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False


def _later(delay, fn):
    from gi.repository import GLib
    GLib.timeout_add(int(delay * 1000), lambda: (fn(), False)[1])


class Ducker:
    STEPS, STEP = 6, 0.025

    def __init__(self, layer=None, later=_later):
        self.layer, self.later = layer or PactlVolume(), later
        self.original, self.scale, self.generation, self.active = None, 1.0, 0, False

    def begin(self, enabled, level=0.18):
        if not enabled:
            return
        if self.original is None:
            snap = self.layer.snapshot()
            if snap is None:
                return
            self.original, self.scale = snap, 1.0
        self.active = True
        self._ramp(level)

    def end(self, immediate=False):
        if self.original is None or not (self.active or immediate):
            return
        self.active = False
        if immediate:
            self.generation += 1
            self.scale = 1.0
            if self.layer.set(self.original, self.original.volume):
                self.original = None
            else:
                self.later(1, lambda: self.end(immediate=True))
            return
        self._ramp(1.0)

    def _ramp(self, target):
        if self.original is None:
            return
        self.generation += 1
        mine, start = self.generation, self.scale

        def step(i):
            if self.generation != mine or self.original is None:
                return
            self.scale = start + (target - start) * i / self.STEPS
            restored = self.layer.set(self.original, self.original.volume * self.scale)
            if i == self.STEPS and target == 1.0:
                if restored:
                    self.original = None
                else:
                    self.later(1, lambda: self.end(immediate=True))
        for i in range(1, self.STEPS + 1):
            self.later(i * self.STEP, lambda i=i: step(i))


def default_sink_monitor(run=_run):
    """The monitor source of the default output: what the other side of a call sounds like (Meeting Copilot)."""
    try:
        name = (run(['pactl', 'get-default-sink']).stdout or '').strip()
    except (OSError, subprocess.SubprocessError):
        return ''
    return f'{name}.monitor' if name else ''


class Mic:
    """16 kHz mono S16LE PCM from a PulseAudio/PipeWire source into on_pcm(bytes), on GStreamer's streaming thread."""

    def __init__(self, source=None):
        self.source = source
        self.pipeline = None
        self.lock = threading.Lock()

    def start(self, uid, on_pcm):
        import gi
        gi.require_version('Gst', '1.0')
        from gi.repository import Gst
        Gst.init(None)
        src = self.source or (f'pulsesrc device="{uid}"' if uid else 'pulsesrc')
        self.pipeline = Gst.parse_launch(f'{src} ! audioconvert ! audioresample ! {CAPS} ! appsink name=sink emit-signals=true sync=false')
        sink = self.pipeline.get_by_name('sink')

        def sample(appsink):
            buf = appsink.emit('pull-sample').get_buffer()
            ok, info = buf.map(Gst.MapFlags.READ)
            if ok:
                try:
                    on_pcm(bytes(info.data))
                finally:
                    buf.unmap(info)
            return Gst.FlowReturn.OK
        sink.connect('new-sample', sample)
        self.pipeline.set_state(Gst.State.PLAYING)

    def caps_string(self):
        pad = self.pipeline.get_by_name('sink').get_static_pad('sink')
        caps = pad.get_current_caps()
        return caps.to_string() if caps else ''

    def stop(self):
        with self.lock:
            if self.pipeline is not None:
                from gi.repository import Gst
                self.pipeline.set_state(Gst.State.NULL)
                self.pipeline = None
