import hashlib
import http.server
import os
import struct
import tempfile
import threading
import unittest
from pathlib import Path

from tests import linux  # noqa: F401
from atticus_shell import cli
from atticus_shell.speech import stt as S


class StitchTests(unittest.TestCase):
    """Cases from tests/test_dictation.py, which pins Swift's stitchTranscript."""

    def test_stitch_transcript_matches_swift(self):
        self.assertEqual(S.stitch_transcript('We should ship the release today', 'the release today before lunch'),
                         'We should ship the release today before lunch')
        self.assertEqual(S.stitch_transcript('Keep punctuation, please.', 'Punctuation please, and spacing.'),
                         'Keep punctuation, please. and spacing.')
        self.assertEqual(S.stitch_transcript('No shared ending', 'Start fresh'), 'No shared ending Start fresh')
        self.assertEqual(S.stitch_transcript('', 'First words'), 'First words')
        self.assertEqual(S.stitch_transcript('Only this', ''), 'Only this')

    def test_transcript_key(self):
        self.assertEqual(S.transcript_key('  Next.  '), 'next')
        self.assertEqual(S.transcript_key('Open   Spotify!'), 'open spotify')

    def test_echo(self):
        self.assertTrue(S.echo('App, open.', 'Open App'))
        self.assertFalse(S.echo('Open the app now', 'Open App'))
        self.assertFalse(S.echo('', 'Open App'))

    def test_selftest_flag_runs_stitch_cases(self):
        self.assertEqual(cli.main(['--selftest']), 0)


def pcm(seconds):
    return b'\x01\x00' * int(16000 * seconds)


class WhisperEngineTests(unittest.TestCase):
    def make(self, replies):
        self.calls = []

        def transcribe(audio, language, prompt=''):
            self.calls.append((len(audio), language))
            return replies.pop(0) if replies else ''
        return S.WhisperCpp(transcribe=transcribe, partial_every=0)   # 0: a partial on every feed (tests)

    def test_final_confirms_before_tools(self):
        partials, finals = [], []
        e = self.make(['hello', 'hello world'])
        e.start('auto', partials.append, finals.append)
        e.feed(pcm(1))
        e.pump()
        self.assertEqual((partials, finals), (['hello'], []))
        e.finish()
        self.assertEqual(finals, ['hello world'])
        self.assertEqual(self.calls[-1][0], 32000)      # the final pass hears all audio

    def test_cancel_never_finalizes(self):
        finals = []
        e = self.make(['x'])
        e.start('en', lambda t: None, finals.append)
        e.feed(pcm(1))
        e.cancel()
        e.finish()
        self.assertEqual(finals, [])

    def test_wav_header(self):
        wav = S.wav(b'\x00\x00' * 10)
        self.assertEqual(wav[:4], b'RIFF')
        self.assertEqual(struct.unpack('<I', wav[24:28])[0], 16000)
        self.assertEqual(len(wav), 44 + 20)

    def test_segments_filter_no_speech(self):
        reply = {'segments': [{'text': ' Hi', 'no_speech_prob': 0.1}, {'text': ' Thanks for watching!', 'no_speech_prob': 0.9}]}
        self.assertEqual(S.whisper_text(reply), 'Hi')
        self.assertEqual(S.whisper_text({'text': ' plain  text '}), 'plain text')


class FakeStream:
    def __init__(self):
        self.samples, self.finished = 0, False

    def accept_waveform(self, rate, samples):
        assert rate == 16000
        self.samples += len(samples)

    def input_finished(self):
        self.finished = True


class FakeRecognizer:
    def __init__(self):
        self.stream = FakeStream()

    def create_stream(self):
        return self.stream

    def is_ready(self, stream):
        return False

    def decode_stream(self, stream):
        pass

    def get_result(self, stream):
        return 'final words' if stream.finished else f'{stream.samples} samples'


class SherpaEngineTests(unittest.TestCase):
    def test_partials_then_final(self):
        partials, finals = [], []
        e = S.SherpaStreaming(recognizer=FakeRecognizer())
        e.start('en', partials.append, finals.append)
        e.feed(pcm(0.5))
        self.assertEqual(partials, ['8000 samples'])
        e.feed(b'')
        self.assertEqual(partials, ['8000 samples'])     # unchanged text is not re-sent
        e.finish()
        self.assertEqual(finals, ['final words'])

    def test_s16_to_float(self):
        self.assertEqual(S.s16_to_float(struct.pack('<hh', 16384, -32768)), [0.5, -1.0])


class Server(http.server.BaseHTTPRequestHandler):
    payload = b''

    def do_GET(self):
        start = 0
        rng = self.headers.get('Range')
        if rng:
            start = int(rng.split('=')[1].split('-')[0])
            self.send_response(206)
        else:
            self.send_response(200)
        body = self.payload[start:]
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class ModelStoreTests(unittest.TestCase):
    def setUp(self):
        Server.payload = os.urandom(5000)
        self.httpd = http.server.HTTPServer(('127.0.0.1', 0), Server)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.shutdown)
        self.root = tempfile.mkdtemp()
        self.url = f'http://127.0.0.1:{self.httpd.server_port}/m.bin'

    def store(self, sha):
        return S.ModelStore(self.root, {'m': S.Model('m', 'M', self.url, 'm.bin', sha, 5000)})

    def test_download_verifies_and_installs(self):
        path = self.store(hashlib.sha256(Server.payload).hexdigest()).ensure('m')
        self.assertEqual(Path(path).read_bytes(), Server.payload)

    def test_resumes_partial(self):
        Path(self.root, 'm.bin.part').write_bytes(Server.payload[:1200])
        path = self.store(hashlib.sha256(Server.payload).hexdigest()).ensure('m')
        self.assertEqual(Path(path).read_bytes(), Server.payload)

    def test_model_sha_mismatch_deletes_partial(self):
        with self.assertRaisesRegex(S.ModelError, "didn’t match"):
            self.store('0' * 64).ensure('m')
        self.assertEqual(os.listdir(self.root), [])

    def test_installed_skips_download(self):
        Path(self.root, 'm.bin').write_bytes(b'already')
        self.assertTrue(self.store('ignored').installed('m'))

    def test_whisper_models_match_swift(self):
        m = S.WHISPER_MODELS
        self.assertEqual(m['turbo'].sha256, '394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2')
        self.assertEqual(set(m), {'turbo', 'large', 'small'})


if __name__ == '__main__':
    unittest.main()


@unittest.skipUnless(os.environ.get('ATTICUS_SHERPA_MODEL'), 'needs a sherpa-onnx streaming model dir (ATTICUS_SHERPA_MODEL)')
class SherpaLive(unittest.TestCase):
    def test_transcribes_sample_wav(self):
        import wave
        model = os.environ['ATTICUS_SHERPA_MODEL']
        with wave.open(os.path.join(model, 'test_wavs', '0.wav')) as w:
            self.assertEqual((w.getframerate(), w.getnchannels(), w.getsampwidth()), (16000, 1, 2))
            audio = w.readframes(w.getnframes())
        partials, finals = [], []
        e = S.SherpaStreaming(model)
        e.start('en', partials.append, finals.append)
        for i in range(0, len(audio), 3200):     # 100 ms chunks, like the mic
            e.feed(audio[i:i + 3200])
        e.feed(b'\x00\x00' * 8000)               # tail padding so the last words decode
        e.finish()
        self.assertTrue(partials)
        want = Path(model, 'test_wavs', 'trans.txt').read_text().split('\n')[0].split(maxsplit=1)[-1].lower()
        got = finals[0].lower()
        overlap = len(set(got.split()) & set(want.split())) / len(set(want.split()))
        self.assertGreater(overlap, 0.8, (got, want))
