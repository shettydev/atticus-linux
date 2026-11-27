"""The ask box: hold the shortcut, talk, let go; the brain's lines stream into ui/ui.html; the answer stays a while.

Ports class App (Atticus.swift:3000-5265). Three layers:
  route_line      the brain's stdout protocol, one line at a time (App.spawn's feed)
  BrainRun        `atticus.py --wait` started warm at key-down; send() commits the request (App.spawn/run)
  BoxController   listen → final transcript → run → answer → linger; Esc and the shortcut stop each layer; every message
                  ui.html posts has a handler. GTK-free, so it is tested directly.
  Box / run_app   the GTK window, WebView, shortcuts, mic and speech wired to the controller.
Subsystems that land in later tasks (agents, drawing, mini screen, onboarding, Settings) are reached through `app.*`.
"""
import json
import math
import os
import shlex
import subprocess
import tempfile
import threading
import uuid

NOARG = object()
NO_SPEECH = 'Speech needs a model. Download it in Settings → Voice & Sound, or type your ask.'


# --- the brain's line protocol -----------------------------------------------------------------------------------------
JSON_PREFIXES = {'♧ ': 'upgrade', '▣ ': 'mini', '☷ ': 'walkthrough', '◇ ': 'pick', '⇢ ': 'movable', '⤴ ': 'handoff', '↺ ': 'rewrite'}
CONTROL = {'! setup': 'setup', '! feedback': 'feedback', '! improve': 'improve', '♧ retry': 'upgrade_retry', '♬!': 'shush'}


def _json(text):
    try:
        v = json.loads(text)
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


def route_line(line):
    """One stdout line -> (kind, payload). Lines no rule claims go to the page as they are ('line')."""
    if line in CONTROL:
        return CONTROL[line], None
    for prefix, kind in JSON_PREFIXES.items():
        if line.startswith(prefix):
            value = _json(line[len(prefix):])
            if value is None:
                break
            if kind == 'upgrade' and 'id' not in value:
                return 'drop', None
            if kind == 'rewrite' and not isinstance(value.get('t'), str):
                return 'drop', None
            return kind, value
    if line.startswith('✎ '):
        return 'draw', line[2:]
    if line.startswith('@ '):
        return 'point', line
    if line.startswith('? '):
        parts = line[2:].split('\t')
        return ('ask', parts) if 3 <= len(parts) <= 6 else ('drop', None)
    if line.startswith('⚙ '):
        parts = line.split(' ')
        return ('setting', (parts[1], parts[2])) if len(parts) == 3 else ('drop', None)
    if line.startswith('▤ '):
        return 'show_agent', line[2:]
    if line.startswith('▥ '):
        return 'open_agents', line[2:]
    if line.startswith('♬ '):
        return 'speech', line[2:].replace(' ', '\n')
    if line.startswith('✓'):
        return 'answer', line[2:].replace(' ', '\n')
    if line.startswith('! '):
        return 'msg', line[2:]
    return 'line', line


def setting_change(key, value, prefs):
    """'⚙ key value' from the AI: flip one switch or choice. Never pause; Safe mode only on; never shortcuts or numbers."""
    from atticus_shell.prefs import DEFAULTS
    if isinstance(DEFAULTS.get(key), bool) and key != 'paused' and (key != 'safe' or value == 'true') and value in ('true', 'false'):
        return key, value == 'true'
    if isinstance(prefs.get(key), str) and isinstance(DEFAULTS.get(key, ''), str) and key in DEFAULTS and not key.startswith('hotkey'):
        return key, value
    return None


# --- geometry (layout pixels, y down) ----------------------------------------------------------------------------------
def box_width(saved, bounds_width):
    saved = saved if isinstance(saved, (int, float)) and math.isfinite(saved) else 500
    return min(min(900, max(340, saved)), bounds_width - 16)


def initial_position(output, width, corner):
    x, y, w, _ = output.work_area
    return (x + w - width - 14 if corner == 'top-right' else x + 14), y + 10


def clamp_frame(frame, work_area):
    """Keep the box, its ✕ and composer reachable: inside the work area less 8 px, never taller than it."""
    x, y, w, h = frame
    bx, by, bw, bh = work_area[0] + 8, work_area[1] + 8, work_area[2] - 16, work_area[3] - 16
    h = min(h, bh)
    w = min(w, bw)
    return min(max(x, bx), bx + bw - w), min(max(y, by), by + bh - h), w, h


def linger_after(answer):
    """Seconds an answer stays: longer answers stay longer (6–30 s)."""
    return max(6, min(30, len(answer) / 12))


# --- the brain process ------------------------------------------------------------------------------------------------
def resources():
    return os.environ.get('ATTICUS_RESOURCES_DIR') or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def app_path():
    return os.environ.get('ATTICUS_APP') or os.path.join(resources(), 'atticus-shell')


def py_command(args):
    return ['/bin/sh', os.path.join(resources(), 'atticus.py'), *args]


def py_env(base, app, extra):
    env = dict(base)
    home = env.get('HOME') or os.path.expanduser('~')
    env['PATH'] = f'{home}/.local/bin:' + env.get('PATH', '/usr/bin:/bin')   # where Claude Code, Devin and Pi install
    env['ATTICUS_APP'] = app
    env.update(extra)
    return env


class BrainRun:
    """One `atticus.py --wait`: started early, handed its request once, its lines posted back to the main loop."""

    def __init__(self, command, env, on_line, on_exit, post):
        self.command, self.env = command, env
        self.on_line, self.on_exit, self.post = on_line, on_exit, post
        self.commit = os.path.join(tempfile.gettempdir(), f'atticus-commit-{uuid.uuid4()}')
        self.proc = None
        self.cancelled = False

    def start(self):
        env = dict(self.env, ATTICUS_COMMIT=self.commit)
        self.proc = subprocess.Popen(self.command, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        threading.Thread(target=self._read, daemon=True).start()

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None and not self.cancelled

    def send(self, request, speculative=False):
        """Commit (unless speculative) and hand over the request: one JSON line on stdin, then EOF."""
        if not speculative:
            with open(self.commit, 'w') as f:
                f.write('go')
        try:
            self.proc.stdin.write((json.dumps(request) + '\n').encode())
            self.proc.stdin.close()
        except (BrokenPipeError, OSError):
            self.cancel()
            raise

    def go(self):
        with open(self.commit, 'w') as f:
            f.write('go')

    def cancel(self):
        self.cancelled = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def _read(self):
        for raw in self.proc.stdout:
            line = raw.decode('utf-8', 'replace').rstrip('\n')
            if not self.cancelled:
                self.post(lambda line=line: None if self.cancelled else self.on_line(line))
        code = self.proc.wait()
        try:
            os.unlink(self.commit)
        except OSError:
            pass
        if not self.cancelled:
            self.post(lambda: None if self.cancelled else self.on_exit(code))


# --- the controller ---------------------------------------------------------------------------------------------------
HANDLED = {}


def handles(*keys):
    def mark(fn):
        for k in keys:
            HANDLED[k] = fn.__name__
        return fn
    return mark


class NullSubsystem:
    """A subsystem that hasn't landed yet: every call is accepted and does nothing."""

    def __getattr__(self, name):
        return lambda *a, **k: None


class BoxController:
    def __init__(self, ui, win, brain, engine, prefs, later, cue, mic=None, speak=None, app=None, set_pref=None):
        self.ui, self.win, self.make_brain, self.make_engine = ui, win, brain, engine
        self.prefs, self.later, self.cue, self.mic, self.speak = prefs, later, cue, mic, speak
        self.app = app or NullSubsystem()
        self.set_pref = set_pref or (lambda k, v: prefs.__setitem__(k, v))
        self.open_settings = lambda tab='': self.app.settings_open(tab)
        self.copy_text = lambda text: self.app.copy(text)
        self.open_uri = lambda uri: self.app.open_uri(uri)
        self.engine = None
        self.voice = None   # .speaking / .stop(): the read-aloud Speaker
        self.pen = None     # .start() / .stop() -> [[t, x, y]]: the pointer while the shortcut is held
        self.classify = lambda samples, text: None   # atticus.py --gesture
        self.pointing = None   # .begin(done) / .finish(): Point at your screen
        self.make_point_context = lambda points: None
        self.thumb = lambda path: ''
        self.talk_path, self.point_context, self.point_active = [], None, False
        self.brain = None
        self.warm = None
        self.listening = self.busy = self.shortcut_down = False
        self.text = self.answer = ''
        self.ask_file = ''
        self.gen = 0
        self.front = ''
        self.screen = ''

    # helpers
    def js(self, fn, arg=NOARG):
        self.ui(fn, arg) if arg is not NOARG else self.ui(fn)

    def show(self):
        self.gen += 1
        self.js('visible', True)
        self.js('mode', bool(self.prefs.get('safe', True)))
        self.win.show()

    def hide(self, after):
        g = self.gen

        def go():
            if g == self.gen and not self.busy and not self.listening:
                self.win.hide()
        self.later(after, go)

    def _brain(self):
        return self.make_brain(self._line, self._exit)

    # the shortcut (Atticus.swift:4077)
    def shortcut(self, pressed):
        if not pressed:
            self.shortcut_down = False
            if self.listening and self.prefs.get('talkMode', 'hold') == 'hold':
                self.stop()
            return
        if self.shortcut_down:
            return   # one action per physical press, key repeat included
        self.shortcut_down = True
        if self.prefs.get('paused'):
            self.js('paused')
            self.show()
            self.hide(4)
            return
        if self.voice is not None and self.voice.speaking:
            return self.voice.stop()   # during spoken playback the shortcut stops the voice
        if self.listening:
            return self.stop() if self.prefs.get('talkMode') == 'toggle' else self.dismiss()
        if self.busy:
            return self.cancel()
        self.start()

    def start(self):
        self.engine = self.make_engine()
        if self.engine is None:
            self.show()
            self.js('msg', NO_SPEECH)
            return self.hide(6)
        self.text, self.answer, self.listening = '', '', True
        self.show()
        self.js('listen', False)
        self.cue('soundStart')
        self.warm = self._brain()
        self.warm.start()   # Claude is up by the time the user lets go
        self.engine.start(self.prefs.get('whisperLanguage', 'auto'), self.heard, self._final)
        self.talk_path = []
        if self.pen:
            self.pen.start()
        if self.mic:
            self.mic.start(self.engine.feed)
        if self.prefs.get('typeRight'):
            self.win.take_keys()

    def heard(self, text):
        if self.listening:
            self.text = text
            self.js('words', text)

    def stop(self):
        """Released: let the engine finalize, then act on its final words (they confirm the request)."""
        if not self.listening:
            return
        self.listening = False
        self.busy = True
        if self.mic:
            self.mic.stop()
        self.talk_path = self.pen.stop() if self.pen else []
        self.cue('soundRelease')
        self.js('think')
        self.js('line', '· Confirming your words…')
        self.win.release_keys()
        self.engine.finish()

    def _final(self, text):
        if not self.busy or self.listening:
            return
        text = (text or self.text).strip()
        from atticus_shell.pen import settle
        self.talk_path, text = settle(self.talk_path, text, self.classify)
        text = text.strip()
        if not text:
            self.busy = False
            self._unwarm()
            self.js('msg', '')
            return self.hide(3)
        self.text = text
        self.js('words', text)
        self.run(text)

    def dismiss(self):
        self.hush()
        self.cue('soundStop')
        self.js('stopped', 'Answer stopped')
        self.hide(0.5)

    def typed(self, t, keep=False):
        if self.busy or self.listening:
            return
        if ' '.join(t.lower().split()).strip(' .,!?:;') in ('report feedback', 'report a bug'):
            return self.open_settings('feedback')
        self.talk_path = []
        self.text, self.answer = t, ''
        self.js('listen', keep)
        self.show()
        self.js('words', t)
        self.run(t)

    def run(self, task):
        self.busy = True
        self.js('think')
        self.js('line', '· Waiting for the answer…')
        brain = self.warm if self.warm and self.warm.running else self._brain()
        if brain is not self.warm:
            brain.start()
        self.warm, self.brain = None, brain
        request = {'front': self.front, 'task': task}
        if self.talk_path:
            request['pointerPath'] = self.talk_path
        if self.point_context:
            request['point'] = self.point_context
        self.talk_path, self.point_context = [], None
        try:
            brain.send(request)
        except OSError:
            self.cancel()
            self.js('msg', 'The request worker stopped. Please try again.')

    def _unwarm(self):
        if self.warm:
            self.warm.cancel()
            self.warm = None

    # brain output (App.spawn's feed)
    def _line(self, line):
        kind, value = route_line(line)
        if kind == 'answer':
            self.answer = value
            self.js('line', '✓ ' + value)
            if self.speak and self.prefs.get('speak'):
                self.speak(value)
        elif kind == 'line':
            self.js('line', value)
        elif kind == 'msg':
            self.js('msg', value)
        elif kind == 'ask':
            self.ask_file = value[2]
            self.show()
            self.js('ask', [value[0], value[1], 'retry' if len(value) >= 5 and value[4] == 'retry' else '', ''])
        elif kind == 'pick':
            self.ask_file = value.get('a', '')
            self.show()
            self.js('pick', value)
        elif kind == 'setting':
            change = setting_change(*value, self.prefs)
            if change:
                self.set_pref(*change)
        elif kind == 'improve':
            self.js('improve')
        elif kind in ('setup', 'feedback'):
            self.app.onboarding_open() if kind == 'setup' else self.open_settings('feedback')
        elif kind == 'shush':
            self.app.shush()
        elif kind == 'speech':
            self.app.stream_speech(value)
        else:
            getattr(self.app, f'line_{kind}', lambda v: None)(value)   # drawing, mini screen, agents, walkthrough… (later tasks)

    def _exit(self, code):
        self.busy = False
        self.brain = None
        self.js('finished')
        if self.answer:
            self.cue('soundDone')
        self.hide(linger_after(self.answer))

    # Esc and cancel (Atticus.swift:3971, 4059)
    def hush(self):
        self.listening = False
        if self.engine:
            self.engine.cancel()
        if self.mic:
            self.mic.stop()
        self._unwarm()
        if self.brain:
            self.brain.cancel()
            self.brain = None
        self.ask_file = ''
        self.busy = False
        self.win.release_keys()

    def cancel(self):
        self.hush()
        self.cue('soundStop')
        self.js('stopped', 'Answer stopped')
        self.hide(1.5)

    def escape(self):
        if self.listening:
            self.hush()
            self.cue('soundStop')
            self.js('stopped', 'Stopped listening')
            return self.hide(1.5)
        if self.busy:
            return self.cancel()
        if self.win.visible:
            self.close(clear=False)

    def close(self, clear):
        self.hush()
        self.gen += 1
        self.win.hide()

    # page messages
    def handle(self, name, body):
        if name == 'h':
            size = body if isinstance(body, list) else [body]
            if size and isinstance(size[-1], (int, float)) and size[-1] > 0:
                self.win.set_height(size[-1])
            return
        if name != 'c' or not isinstance(body, dict):
            return
        for key in body:
            method = HANDLED.get(key)
            if method:
                getattr(self, method)(body)

    @handles('send')
    def _send(self, b):
        if isinstance(b['send'], str):
            self.typed(b['send'], keep=True)

    @handles('allow')
    def _allow(self, b):
        if self.ask_file:
            self._answer_card('y' if b['allow'] else 'n')

    @handles('pick')
    def _pick(self, b):
        if self.ask_file and isinstance(b['pick'], str):
            self._answer_card(b['pick'])

    def _answer_card(self, value):
        path, self.ask_file = self.ask_file, ''
        try:
            with open(path, 'w') as f:
                f.write(value)
        except OSError:
            self.js('notice', 'Could not answer. Please ask again.')

    @handles('copy')
    def _copy(self, b):
        if isinstance(b['copy'], str) and b['copy']:
            self.copy_text(b['copy'])
            self.js('copied')

    @handles('link')
    def _link(self, b):
        uri = b['link'] if isinstance(b['link'], str) else ''
        if uri.split(':', 1)[0].lower() in ('https', 'http', 'mailto'):
            self.open_uri(uri)

    @handles('close')
    def _close(self, b):
        self.close(clear=True)

    @handles('ideaClose')
    def _idea_close(self, b):
        self.close(clear=False)

    @handles('retry', 'workerRetry')
    def _retry(self, b):
        if not self.busy and not self.listening and self.text:
            self.typed(self.text, keep=True)

    @handles('type')
    def _type(self, b):
        if self.listening:
            self.hush()   # typing wins over listening

    @handles('focus')
    def _focus(self, b):
        if b['focus'] is False and not self.busy:
            self.hide(3)

    @handles('resume')
    def _resume(self, b):
        self.set_pref('paused', False)
        self.js('paused', False)
        self.hide(1.5)

    @handles('safe')
    def _safe(self, b):
        self.set_pref('safe', True)
        self.js('mode', True)

    @handles('model')
    def _model(self, b):
        if isinstance(b['model'], str):
            self.set_pref('apiModel' if self.prefs.get('apiBase') else 'routing', b['model'])

    @handles('off')
    def _off(self, b):
        if b['off'] in ('tips', 'support'):
            self.set_pref(b['off'], False)

    @handles('improve')
    def _improve(self, b):
        if isinstance(b['improve'], bool):
            self.set_pref('improve', b['improve'])

    @handles('autoMinimize')
    def _auto_minimize(self, b):
        if isinstance(b['autoMinimize'], bool):
            self.set_pref('autoMinimize', b['autoMinimize'])

    @handles('boxH')
    def _box_h(self, b):
        if isinstance(b['boxH'], (int, float)):
            self.set_pref('boxH', b['boxH'])

    @handles('ready')
    def _ready(self, b):
        self.js('autoMinimize', bool(self.prefs.get('autoMinimize', True)))
        self.js('tiny', bool(self.prefs.get('tinyFold', False)))

    @handles('fold', 'h', 'tiny')
    def _fold(self, b):
        if isinstance(b.get('fold'), int) and isinstance(b.get('h'), (int, float)):
            self.win.set_height(b['h'])
            self.js('foldSettled', b['fold'])

    @handles('mic')
    def _mic(self, b):
        if not self.busy and not self.listening:
            self.start()

    @handles('kofi')
    def _kofi(self, b):
        self.open_uri('https://ko-fi.com/saarthr')

    @handles('updateNews')
    def _news(self, b):
        self.app.update_news()

    @handles('setup')
    def _setup(self, b):
        self.app.onboarding_open()

    @handles('agents', 'agentOpen', 'agentLater', 'agentConnect', 'agentGone', 'agentDefault', 'target')
    def _agents(self, b):
        self.app.agents_message(b)

    @handles('point')
    def _point(self, b):
        if not self.pointing:
            return
        if self.point_active:
            self.point_active = False
            self.pointing.finish()
            if self.point_context:
                self.js('point', {'active': False, 'marked': True})
            return
        if self.busy or self.listening:
            return self.js('notice', 'Finish or stop listening first.')
        self.point_active = True
        self.pointing.begin(self._pointed)
        self.js('point', {'active': True, 'marked': False})

    def _pointed(self, points):
        ctx = self.make_point_context(points)
        if not ctx:
            return self.js('notice', 'Couldn’t capture that mark.')
        old = (self.point_context or {}).get('image')
        if old:
            try:
                os.unlink(old)   # a redrawn mark replaces the last crop
            except OSError:
                pass
        self.point_context = ctx
        self.js('point', {'active': self.point_active, 'marked': True, 'picked': False, 'thumb': self.thumb(ctx['image']) if ctx.get('image') else ''})

    @handles('pointClear')
    def _point_clear(self, b):
        self.point_context = None

    @handles('pointCoached')
    def _point_coached(self, b):
        self.set_pref('pointCoached', True)

    @handles('replayDrawing')
    def _replay_drawing(self, b):
        self.app.point_message(b)

    @handles('miniRect', 'miniOpen', 'miniClear')
    def _mini(self, b):
        self.app.mini_message(b)

    @handles('dictationHistory', 'learnedUndo', 'permissionSettings')
    def _dictation(self, b):
        self.app.dictation_message(b)

    @handles('memory')
    def _memory(self, b):
        self.app.settings_reveal(b['memory'])

    @handles('models', 'comp', 'overText', 'idea', 'replace', 'upgrade')
    def _box_state(self, b):
        self.app.box_message(b)


# --- GTK ---------------------------------------------------------------------------------------------------------------
def run_app():
    """The app: one instance, the box, shortcuts, tray. Returns the process exit code."""
    from atticus_shell.desktop.env import detect
    from atticus_shell import windows
    env = detect()
    windows.choose_gdk_backend(env)
    from atticus_shell import app as application
    return application.main(env)
