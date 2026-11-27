"""Writes Atticus for Linux's cue sounds (ui/sounds/*.oga): short synthesized tones, original to this project (CC0).
Run inside the test image: python3 linux/packaging/make-sounds.py && the .oga files land in ui/sounds/."""
import math
import os
import struct
import subprocess
import tempfile
import wave

RATE = 44100
CUES = {   # name: [(frequency Hz, start s, length s)], a soft attack and an exponential decay each
    'start': [(880, 0.0, 0.09), (1320, 0.05, 0.12)],
    'release': [(660, 0.0, 0.10), (520, 0.06, 0.12)],
    'done': [(1046.5, 0.0, 0.35), (1568, 0.07, 0.40)],
    'stop': [(1760, 0.0, 0.08)],
}


def render(notes):
    total = max(s + l for _, s, l in notes) + 0.05
    out = [0.0] * int(total * RATE)
    for f, start, length in notes:
        for i in range(int(length * RATE)):
            t = i / RATE
            env = min(1, t / 0.004) * math.exp(-t * 9 / length)
            out[int(start * RATE) + i] += 0.35 * env * math.sin(2 * math.pi * f * t)
    return b''.join(struct.pack('<h', int(max(-1, min(1, v)) * 32767)) for v in out)


here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
dest = os.path.join(here, 'ui', 'sounds')
os.makedirs(dest, exist_ok=True)
for name, notes in CUES.items():
    wav = os.path.join(tempfile.mkdtemp(), name + '.wav')
    with wave.open(wav, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(render(notes))
    subprocess.run(['gst-launch-1.0', '-q', 'filesrc', f'location={wav}', '!', 'wavparse', '!', 'audioconvert', '!', 'vorbisenc', 'quality=0.4',
                    '!', 'oggmux', '!', 'filesink', f'location={os.path.join(dest, name + ".oga")}'], check=True)
    print(name, os.path.getsize(os.path.join(dest, name + '.oga')))
