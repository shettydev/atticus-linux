"""Read answers aloud: Piper (local neural voice) when one is installed, else speech-dispatcher's spd-say; and the cues.

spoken(), read_aloud_text() and SpeechSentences port Atticus.swift:714-778: math and Markdown become words, code and
links are skipped, sentences stream as they complete, and a long answer stops at 900 characters ("The rest is on screen.").
"""
import glob
import os
import re
import shutil
import subprocess

MATH = re.compile(r'\$\$([\s\S]+?)\$\$|\\\(([\s\S]+?)\\\)|\\\[([\s\S]+?)\\\]|(?<!\\)\$(?=\S)([^$\n]+?)(?<=\S)\$(?!\d)')
WORDS = [
    (r'\^\{?2\}?(?![\d.])|²', ' squared '), (r'\^\{?3\}?(?![\d.])|³', ' cubed '),
    (r'\^\{?(-?\d+|[a-zA-Z])\}?', r' to the power of \1 '),
    (r'(?<![\w/])([a-zA-Z]|\d+(?:\.\d+)?)\s*/\s*([a-zA-Z]|\d+(?:\.\d+)?)(?![\w/])', r'\1 over \2'),
    (r'\\(?:leq|le)\b|≤', ' less than or equal to '), (r'\\(?:geq|ge)\b|≥', ' greater than or equal to '),
    (r'\\(?:times|cdot)\b|[×·]', ' times '), (r'√', ' square root of '),
    (r'<', ' less than '), (r'>', ' greater than '),
    (r'\\([a-zA-Z]+)|[{}\\]', r'\1 '),
]
EMPHASIS = re.compile(r'\*{2,}|(?<!\S)\*(?=\S)|(?<=\S)\*(?!\S)|(?<!\w)_+|_+(?!\w)')
STAR_TIMES = re.compile(r'(?:(?<=[^\W_])|(?<=\)))\s*\*\s*(?=[^\W_]|\()')


def spoken(answer):
    s = MATH.sub(lambda m: next(g for g in m.groups() if g is not None), answer)
    for _ in range(3):
        s = re.sub(r'\\sqrt\{([^{}]*)\}', r' square root of \1 ', s)
        s = re.sub(r'\\[dt]?frac\{([^{}]*)\}\{([^{}]*)\}', r' \1 over \2 ', s)
    for pattern, words in WORDS:
        s = re.sub(pattern, words, s)
    s = EMPHASIS.sub('', s)
    s = STAR_TIMES.sub(' times ', s)
    s = re.sub(r'(?m)^#{1,6}\s+', '', s)
    s = re.sub(r'[ \t]+', ' ', s)
    return re.sub(r' +([,.!?;:])', r'\1', s).strip()


def read_aloud_text(text):
    clean = re.sub(r'(?m)^[ \t]*```graph[ \t]*(?:\n[\s\S]*?(?:^[ \t]*```[^\n]*|\Z)|\Z)', ' Graph on screen. ', text)
    clean = re.sub(r'```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|`[^`]*(?:`|$)', ' ', clean)
    clean = re.sub(r'</?[A-Za-z][A-Za-z0-9]*(?:\s+[A-Za-z_:][A-Za-z0-9_:.-]*(?:\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s>]+))?)*\s*/?>', ' ', clean)
    clean = re.sub(r'!?\[([^\]]*)\]\([^)]*\)', r'\1', clean)
    clean = re.sub(r'(?i)<?\b(?:[a-z][a-z0-9+.-]*://|mailto:|www\.)[^\s<>]*>?', ' ', clean)
    clean = re.sub(r'(?m)^[ \t]*(?:[-*+>]|\d+[.)])[ \t]+', '', clean)
    end = '\n' if text.endswith('\n') else ' ' if text[-1:].isspace() else ''
    return spoken(clean) + end


ENDS = re.compile(r'[.!?]+["”’\']*(?:\s+|$)|[。！？]+[”’」』]*\s*|\n\s*')


class SpeechSentences:
    """Cumulative answer text in, whole sentences out; an incomplete sentence waits for the next chunk."""
    CAP = 900

    def __init__(self):
        self.consumed, self.count, self.capped = 0, 0, False

    def take(self, text, final=False):
        if self.capped or len(text) < self.consumed:
            return []
        tail = text[self.consumed:]
        result, last = [], 0
        for m in ENDS.finditer(tail):
            if not (final or m.end() < len(tail) or tail[-1:].isspace()):
                continue
            sentence = tail[last:m.end()].strip()
            if sentence:
                result.append(sentence)
            last = m.end()
        if final and last < len(tail):
            result.append(tail[last:].strip())
        self.consumed += len(tail) if final else last
        accepted = []
        for sentence in result:
            if not sentence:
                continue
            if self.count + len(sentence) > self.CAP:
                self.capped = True
                accepted.append('The rest is on screen.')
                break
            self.count += len(sentence)
            accepted.append(sentence)
        return accepted


def spd_rate(wpm):
    """Atticus's words-a-minute setting -> spd-say's -100..100 (0 is its ~175 wpm default)."""
    return max(-100, min(100, round((wpm - 175) / 1.75)))


def piper_length_scale(wpm):
    return 175 / max(60, wpm)


def voices_dir():
    base = os.environ.get('ATTICUS_DIR') or os.path.expanduser('~/.config/atticus')
    return os.path.join(base, 'voices')


def speak_command(text, wpm, voices_dir=None, which=shutil.which):
    """argv that speaks text. Piper reads the words from stdin (the last argv item is what to send there)."""
    voices = sorted(glob.glob(os.path.join(voices_dir or globals()['voices_dir'](), '*.onnx')))
    player = which('pw-play') and 'pw-play --rate 22050 --channels 1 --format s16 -' or which('aplay') and 'aplay -q -r 22050 -f S16_LE -t raw -'
    if voices and which('piper') and player:
        pipeline = f'piper --model "$1" --length_scale {piper_length_scale(wpm):.3f} --output-raw 2>/dev/null | {player}'
        return ['sh', '-c', pipeline, 'piper', voices[0], text]
    if which('spd-say'):
        return ['spd-say', '-w', '-r', str(spd_rate(wpm)), '--', text]
    return None


class Speaker:
    def __init__(self, command=speak_command):
        self.command, self.proc = command, None

    def say(self, text, wpm):
        self.stop()
        argv = self.command(text, wpm)
        if not argv:
            return False
        stdin_text = None
        if argv[0] == 'sh':          # Piper: the words go on stdin, never into the shell line
            argv, stdin_text = argv[:-1], argv[-1]
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        if stdin_text is not None:
            try:
                self.proc.stdin.write(stdin_text.encode())
                self.proc.stdin.close()
            except OSError:
                pass
        return True

    @property
    def speaking(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if self.proc is not None:
            if self.proc.poll() is None:
                try:
                    os.killpg(self.proc.pid, 15)   # the whole pipeline: piper and the player
                except OSError:
                    self.proc.terminate()
            self.proc = None


SOUNDS = {'Pop': 'start', 'Bottle': 'release', 'Glass': 'done', 'Tink': 'stop'}   # the macOS cue names Settings stores


def sound_path(name, resources):
    if not name or name == 'None':
        return None
    path = os.path.join(resources, 'ui', 'sounds', SOUNDS.get(name, name.lower()) + '.oga')
    return path if os.path.exists(path) else None


def play_sound(name, volume, resources):
    path = sound_path(name, resources)
    if not path:
        return
    import gi
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst
    Gst.init(None)
    player = Gst.ElementFactory.make('playbin', None)
    player.set_property('uri', 'file://' + path)
    player.set_property('volume', max(0.0, min(1.0, volume)))
    bus = player.get_bus()
    bus.add_signal_watch()
    bus.connect('message::eos', lambda *_: player.set_state(Gst.State.NULL))
    bus.connect('message::error', lambda *_: player.set_state(Gst.State.NULL))
    player.set_state(Gst.State.PLAYING)
    play_sound.keep = getattr(play_sound, 'keep', [])[-3:] + [player]   # keep the pipeline alive until it ends
