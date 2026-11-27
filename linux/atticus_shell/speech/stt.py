"""Speech to text on this machine: sherpa-onnx streaming (Settings "Built-in", live words) or whisper.cpp ("Whisper").

Audio never leaves the computer. Engines take 16 kHz mono S16LE PCM (audio.Mic) and report live partials and one
final transcript; the final is what confirms a voice request before any AI tool runs (GUIDE, "How the shortcut…").
Whisper ports Atticus.swift's Whisper: whisper-server on 127.0.0.1, segments it rates as no-speech dropped, prompt echoes ignored.
"""
import array
import glob
import hashlib
import json
import os
import socket
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Callable, Protocol


# --- transcript helpers, ported 1:1 (Atticus.swift:884-899, 568-574) -------------------------------------------------
PUNCT = ' .,!?:;'


def transcript_key(s):
    return ' '.join(s.lower().split()).strip(PUNCT)


def _word_key(w):
    return w.lower().strip('!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~')


def stitch_transcript(prefix, suffix):
    a, b = prefix.split(), suffix.split()
    if not a:
        return suffix
    if not b:
        return prefix
    limit = min(24, len(a), len(b))
    overlap = next((n for n in range(limit, 0, -1) if all(_word_key(x) == _word_key(y) for x, y in zip(a[-n:], b[:n]))), 0)
    return ' '.join(a + b[overlap:])


def _letters(s):
    out, word = [], ''
    for ch in s.lower():
        if ch.isalnum():
            word += ch
        elif word:
            out.append(word)
            word = ''
    return out + ([word] if word else [])


def echo(text, prompt):
    """Whisper on short or quiet audio says its prompt back: every word it heard is a prompt word."""
    heard, known = _letters(text), set(_letters(prompt))
    return bool(heard) and all(w in known for w in heard)


def wav(pcm):
    """16 kHz mono 16-bit WAV around raw S16LE PCM."""
    import struct
    return (b'RIFF' + struct.pack('<I', 36 + len(pcm)) + b'WAVEfmt ' + struct.pack('<IHHIIHH', 16, 1, 1, 16000, 32000, 2, 16) +
            b'data' + struct.pack('<I', len(pcm)) + pcm)


def whisper_text(reply):
    segments = reply.get('segments') or []
    text = ''.join(s.get('text', '') for s in segments if (s.get('no_speech_prob') or 0) < 0.6) if segments else reply.get('text', '')
    return ' '.join(text.split())


def s16_to_float(pcm):
    samples = array.array('h')
    samples.frombytes(pcm[:len(pcm) - len(pcm) % 2])
    if sys.byteorder == 'big':
        samples.byteswap()
    return [v / 32768 for v in samples]


# --- engines ------------------------------------------------------------------------------------------------------------
class SttEngine(Protocol):
    def start(self, lang: str, on_partial: Callable[[str], None], on_final: Callable[[str], None]) -> None: ...
    def feed(self, pcm: bytes) -> None: ...
    def finish(self) -> None: ...
    def cancel(self) -> None: ...


class WhisperCpp:
    """Re-transcribes the growing recording for live words (pump(), from a worker), then once more on finish()."""

    def __init__(self, transcribe=None, partial_every=1.0, prompt=''):
        self.transcribe = transcribe or WhisperServer.shared().transcribe
        self.partial_every, self.prompt = partial_every, prompt
        self.lock = threading.Lock()
        self.audio, self.heard_len, self.last = bytearray(), 0, 0.0
        self.on_partial = self.on_final = None
        self.cancelled, self.lang = False, 'auto'

    def start(self, lang, on_partial, on_final):
        with self.lock:
            self.audio, self.heard_len, self.last, self.cancelled = bytearray(), 0, 0.0, False
        self.lang, self.on_partial, self.on_final = lang or 'auto', on_partial, on_final

    def feed(self, pcm):
        with self.lock:
            self.audio += pcm

    def pump(self):
        with self.lock:
            due = len(self.audio) > self.heard_len and time.monotonic() - self.last >= self.partial_every
            audio = bytes(self.audio) if due else b''
        if not audio or self.cancelled:
            return
        text = self.transcribe(audio, self.lang, self.prompt)
        with self.lock:
            self.heard_len, self.last = len(audio), time.monotonic()
        if text and not self.cancelled and not echo(text, self.prompt):
            self.on_partial(text)

    def finish(self):
        if self.cancelled or self.on_final is None:
            return
        with self.lock:
            audio = bytes(self.audio)
        text = self.transcribe(audio, self.lang, self.prompt) if audio else ''
        if not self.cancelled:
            self.on_final('' if echo(text, self.prompt) else text)

    def cancel(self):
        self.cancelled = True


class WhisperServer:
    """One whisper-server on a free 127.0.0.1 port, started on first use."""
    _shared = None
    NAMES = ('whisper-server', 'whisper-cpp-server')

    def __init__(self, model_path=''):
        self.model_path, self.proc, self.port = model_path, None, 0
        self.lock = threading.Lock()

    @classmethod
    def shared(cls):
        if cls._shared is None:
            cls._shared = cls()
        return cls._shared

    @classmethod
    def binary(cls):
        dirs = os.environ.get('PATH', '').split(':') + [os.path.expanduser('~/.nix-profile/bin'), '/run/current-system/sw/bin', '/usr/local/bin']
        for d in dirs:
            for n in cls.NAMES:
                p = os.path.join(d, n)
                if os.access(p, os.X_OK):
                    return p
        return ''

    def _up(self):
        if self.proc and self.proc.poll() is None:
            return
        binary = self.binary()
        if not binary:
            raise ModelError('whisper-server isn’t installed. Install whisper.cpp from your package manager.')
        if not os.path.exists(self.model_path):
            raise ModelError('Download a Whisper model in Settings → Voice & Sound first.')
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            self.port = s.getsockname()[1]
        self.proc = subprocess.Popen([binary, '-m', self.model_path, '--host', '127.0.0.1', '--port', str(self.port)],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        end = time.time() + 30
        while time.time() < end:
            try:
                urllib.request.urlopen(f'http://127.0.0.1:{self.port}/', timeout=1)
                return
            except OSError:
                time.sleep(0.1)
        self.stop()
        raise ModelError('whisper-server didn’t start.')

    def transcribe(self, pcm, language='auto', prompt=''):
        with self.lock:
            self._up()
            port = self.port
        boundary = uuid.uuid4().hex
        fields = {'response_format': 'verbose_json', 'temperature': '0', 'language': language}
        if prompt:
            fields['prompt'] = prompt
        body = b''.join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items())
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode()
                 + wav(pcm) + f'\r\n--{boundary}--\r\n'.encode())
        req = urllib.request.Request(f'http://127.0.0.1:{port}/inference', body, {'Content-Type': f'multipart/form-data; boundary={boundary}'})
        with urllib.request.urlopen(req, timeout=60) as r:
            return whisper_text(json.loads(r.read()))

    def stop(self):
        if self.proc:
            self.proc.terminate()
            self.proc = None


class SherpaStreaming:
    """sherpa-onnx streaming transducer: words appear while you talk; the final result comes from the same stream."""

    def __init__(self, model_dir='', recognizer=None):
        self.recognizer = recognizer or self._load(model_dir)
        self.stream = None
        self.last = ''
        self.cancelled = False

    @staticmethod
    def _load(model_dir):
        import sherpa_onnx

        def pick(prefix):
            files = sorted(glob.glob(os.path.join(model_dir, f'{prefix}*.int8.onnx'))) or sorted(glob.glob(os.path.join(model_dir, f'{prefix}*.onnx')))
            if not files:
                raise ModelError(f'The speech model in {model_dir} is incomplete. Download it again in Settings.')
            return files[0]
        return sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=os.path.join(model_dir, 'tokens.txt'), encoder=pick('encoder'), decoder=pick('decoder'), joiner=pick('joiner'),
            num_threads=2, sample_rate=16000, feature_dim=80, decoding_method='greedy_search')

    def start(self, lang, on_partial, on_final):
        self.stream, self.last, self.cancelled = self.recognizer.create_stream(), '', False
        self.on_partial, self.on_final = on_partial, on_final

    def _decode(self):
        while self.recognizer.is_ready(self.stream):
            self.recognizer.decode_stream(self.stream)
        text = self.recognizer.get_result(self.stream)
        return (text if isinstance(text, str) else getattr(text, 'text', '')).strip()

    def feed(self, pcm):
        if self.stream is None or self.cancelled:
            return
        if pcm:
            self.stream.accept_waveform(16000, s16_to_float(pcm))
        text = self._decode()
        if text and text != self.last:
            self.last = text
            self.on_partial(text)

    def finish(self):
        if self.stream is None or self.cancelled:
            return
        self.stream.input_finished()
        text = self._decode()
        self.stream = None
        self.on_final(text)

    def cancel(self):
        self.cancelled = True
        self.stream = None


# --- models ------------------------------------------------------------------------------------------------------------
class ModelError(Exception):
    pass


@dataclass(frozen=True)
class Model:
    id: str
    name: str
    url: str
    file: str
    sha256: str
    bytes: int


WHISPER_BASE = 'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/'
WHISPER_MODELS = {m.id: m for m in (   # same files and checksums as Atticus.swift's Whisper.models
    Model('turbo', 'Turbo', WHISPER_BASE + 'ggml-large-v3-turbo-q5_0.bin', 'ggml-large-v3-turbo-q5_0.bin',
          '394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2', 574041195),
    Model('large', 'Large v3', WHISPER_BASE + 'ggml-large-v3-q5_0.bin', 'ggml-large-v3-q5_0.bin',
          'd75795ecff3f83b5faa89d1900604ad8c780abd5739fae406de19f23ecd98ad1', 1081140203),
    Model('small', 'Small', WHISPER_BASE + 'ggml-small-q5_1.bin', 'ggml-small-q5_1.bin',
          'ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb', 190085487))}
SHERPA_BASE = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/'
SHERPA_MODELS = {m.id: m for m in (
    Model('builtin', 'Built-in (English)', SHERPA_BASE + 'asr-models/sherpa-onnx-streaming-zipformer-en-2023-06-26.tar.bz2',
          'sherpa-onnx-streaming-zipformer-en-2023-06-26.tar.bz2', '639e25b578e9e997131402199419c13a941f8e4e198e2da1ce57dbf5cf401282', 310414022),
    Model('wake', 'Wake word', SHERPA_BASE + 'kws-models/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.tar.bz2',
          'sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.tar.bz2', 'f170013b4716e41b62b9bfd809687c207cef798ef9bc6534d524e17af9b6561a', 17626723))}


class ModelStore:
    """Downloads to <file>.part (resuming), checks sha256, then renames into place: a half or wrong file never looks installed.
    .tar.bz2 archives are unpacked into a folder of the same name."""

    def __init__(self, root, models):
        self.root, self.models = root, models

    def _final(self, m):
        name = m.file[:-len('.tar.bz2')] if m.file.endswith('.tar.bz2') else m.file
        return os.path.join(self.root, name)

    def installed(self, model_id):
        return os.path.exists(self._final(self.models[model_id]))

    def ensure(self, model_id, progress=None):
        m = self.models[model_id]
        final = self._final(m)
        if os.path.exists(final):
            return final
        os.makedirs(self.root, mode=0o700, exist_ok=True)
        import fcntl
        lock_path = os.path.join(self.root, m.file + '.lock')
        lock = open(lock_path, 'w')   # two downloads appending to one .part would corrupt it
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock.close()
            raise ModelError('This model is already downloading. Wait for it to finish.')
        try:
            return self._fetch(m, final, progress)
        finally:
            try:
                os.unlink(lock_path)   # while still held, so nothing is left behind
            except OSError:
                pass
            lock.close()

    def _fetch(self, m, final, progress):
        if os.path.exists(final):   # another download finished while this one waited for the lock
            return final
        part = os.path.join(self.root, m.file + '.part')
        have = os.path.getsize(part) if os.path.exists(part) else 0
        req = urllib.request.Request(m.url, headers={'Range': f'bytes={have}-'} if have else {})
        try:
            with urllib.request.urlopen(req, timeout=30) as r, open(part, 'ab' if have and r.status == 206 else 'wb') as f:
                done = have if r.status == 206 else 0
                while chunk := r.read(1 << 16):
                    f.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done / m.bytes if m.bytes else 0)
        except OSError:
            raise ModelError('Download failed. Check your connection and try again.')
        digest = hashlib.sha256()
        with open(part, 'rb') as f:
            while chunk := f.read(1 << 20):
                digest.update(chunk)
        if digest.hexdigest() != m.sha256:
            os.unlink(part)
            raise ModelError('The download didn’t match. Try again.')
        if m.file.endswith('.tar.bz2'):
            with tarfile.open(part) as t:
                t.extractall(self.root, filter='data')
            os.unlink(part)
            for f in glob.glob(os.path.join(final, '*.onnx')):   # keep the int8 models only: a third of the disk
                if not f.endswith('.int8.onnx') and os.path.exists(f[:-len('.onnx')] + '.int8.onnx'):
                    os.unlink(f)
        else:
            os.replace(part, final)
        return final


def selftest(_args=()):
    """atticus-shell --selftest: transcript matching without starting the app (like Atticus --selftest)."""
    cases = [(('We should ship the release today', 'the release today before lunch'), 'We should ship the release today before lunch'),
             (('Keep punctuation, please.', 'Punctuation please, and spacing.'), 'Keep punctuation, please. and spacing.'),
             (('No shared ending', 'Start fresh'), 'No shared ending Start fresh'), (('', 'First words'), 'First words')]
    bad = [a for a, want in cases if stitch_transcript(*a) != want]
    if bad or transcript_key(' Next. ') != 'next':
        print(f'selftest FAIL: {bad}', file=sys.stderr)
        return 1
    print('selftest ok')
    return 0
