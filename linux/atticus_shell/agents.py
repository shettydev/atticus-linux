"""Background agents on screen: the chip shelf and the Agents window (ui/agents.html), driven by `atticus.py --agents`.

Ports AgentPanels (Atticus.swift:5288): one supervisor process (JSON actions in, JSON events out, restarted with
backoff), both surfaces fed the same rows, and the page's actions handled as macOS does.
"""
import json
import subprocess
import threading
import time

LIVE = {'running', 'needs-you', 'waiting-screen', 'queued'}
HANDLED = {}


def backoff(restarts):
    return min(60, 2 ** restarts)


def handles(*actions):
    def mark(fn):
        for a in actions:
            HANDLED[a] = fn.__name__
        return fn
    return mark


class AgentService:
    def __init__(self, command, on_event, post, later=None):
        self.command, self.on_event, self.post = command, on_event, post
        self.later = later or (lambda secs, fn: threading.Timer(secs, fn).start())
        self.proc, self.restarts, self.stopped = None, 0, False
        self.lock = threading.Lock()

    def start(self):
        if self.proc is not None or self.stopped:
            return
        try:
            self.proc = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError:
            self.restarts += 1
            self.later(backoff(self.restarts), self.start)
            return
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()
        self.send({'action': 'list'})

    def _read(self, proc):
        for raw in proc.stdout:
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            if isinstance(event, dict):
                self.restarts = 0 if event.get('event') == 'list' else self.restarts
                self.post(lambda e=event: self.on_event(e))
        proc.wait()
        with self.lock:
            if self.proc is proc:
                self.proc = None
        if not self.stopped:
            self.restarts += 1
            self.later(backoff(self.restarts), self.start)   # a crash: a fresh supervisor relists its saved rows

    def send(self, data):
        with self.lock:
            proc = self.proc
        if proc is None or proc.stdin is None:
            return
        try:
            proc.stdin.write((json.dumps(data) + '\n').encode())
            proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def stop(self):
        self.stopped = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


class AgentsController:
    def __init__(self, send, js, open_path, notify, set_pref, prefs, window=None):
        self.send, self.js_raw, self.open_path, self.notify, self.set_pref, self.prefs = send, js, open_path, notify, set_pref, prefs
        self.window = window or (lambda kind, **args: None)   # shelf/view window operations the GTK side performs
        self.rows, self.named, self.ready = {}, [], set()
        self.dismissed = set(prefs.get('agentDismissed') or [])
        self.notified = set()

    def js(self, surface, call, value=None):
        if surface in self.ready:
            self.js_raw(surface, call, {} if value is None else value)

    @property
    def sorted(self):
        return sorted(self.rows.values(), key=lambda r: r.get('created', 0), reverse=True)

    @property
    def badge(self):
        return {'count': sum(1 for r in self.rows.values() if r.get('status') == 'running'),
                'attention': any(r.get('status') == 'needs-you' for r in self.rows.values())}

    def receive(self, event):
        kind = event.get('event')
        if kind == 'list' and isinstance(event.get('agents'), list):
            before = {i: r.get('status') for i, r in self.rows.items()}
            self.rows = {r['id']: r for r in event['agents'] if isinstance(r, dict) and 'id' in r}
            self.named = event.get('named') or []
            for i, r in self.rows.items():
                if r.get('status') == 'done' and before.get(i) in LIVE and i not in self.notified:
                    self.notified.add(i)
                    self.notify(r.get('summary') or r.get('title') or 'Your task is done')
            for surface in ('shelf', 'view'):
                self.js(surface, 'list', self.sorted)
            self.js('view', 'named', self.named)
            if any(r.get('status') in LIVE for r in self.rows.values()):
                self.window('show_shelf')
        elif kind == 'chats':
            self.js('view', 'chats', event.get('chats') or [])
        elif kind == 'open' and isinstance(event.get('path'), str):
            self.open_path(event['path'], event.get('reveal') is not False)

    def page(self, surface, body):
        if not isinstance(body, dict) or not isinstance(body.get('action'), str):
            return
        method = HANDLED.get(body['action'])
        if method:
            getattr(self, method)(surface, body)
        else:
            self.send(body)

    @handles('ready')
    def _ready(self, surface, b):
        self.ready.add(surface)
        self.js(surface, 'mode', surface)
        self.js(surface, 'prefs', {'dismissed': sorted(self.dismissed), 'stack': self.prefs.get('agentStack', ''), 'tab': self.prefs.get('agentTab', '')})
        self.js(surface, 'list', self.sorted)

    @handles('size')
    def _size(self, surface, b):
        if surface == 'shelf' and isinstance(b.get('h'), (int, float)):
            self.window('shelf_height', h=max(52, b['h']))

    @handles('drag')
    def _drag(self, surface, b):
        self.window('drag', surface=surface)

    @handles('dismiss')
    def _dismiss(self, surface, b):
        ident = b.get('id', '')
        if self.rows.get(ident, {}).get('status') == 'done':
            self.send({'action': 'seen', 'id': ident})
            self.rows[ident]['seenAt'] = time.time()
        self.dismissed.add(ident)
        self.set_pref('agentDismissed', sorted(i for i in self.dismissed if self.rows.get(i, {}).get('status') in LIVE | {'done'}))

    @handles('stack')
    def _stack(self, surface, b):
        self.set_pref('agentStack', b.get('value') or '')

    @handles('tab')
    def _tab(self, surface, b):
        self.set_pref('agentTab', 'chats' if b.get('value') == 'chats' else '')

    @handles('view', 'show')
    def _view(self, surface, b):
        self.window('open_view', id=b.get('id', ''))
        if self.rows.get(b.get('id', ''), {}).get('status') == 'done':
            self.send({'action': 'seen', 'id': b['id']})

    @handles('lingering', 'hover', 'miniRect')
    def _cosmetic(self, surface, b):
        self.window(b['action'], surface=surface, body=b)

    @handles('close')
    def _close(self, surface, b):
        if surface == 'view':
            self.window('close_view')

    @handles('attention')
    def _attention(self, surface, b):
        self.window('show_shelf')

    @handles('open', 'reveal')
    def _open(self, surface, b):
        self.send({'action': 'open', 'id': b.get('id', ''), **({'target': b['target']} if b.get('target') else {}),
                   **({'reveal': True} if b['action'] == 'reveal' else {})})
        if b['action'] == 'open' and self.rows.get(b.get('id', ''), {}).get('status') == 'done':
            self.send({'action': 'seen', 'id': b['id']})

    @handles('connect')
    def _connect(self, surface, b):
        self.open_path('https://claude.ai/settings/connectors', False)

    @handles('answer')
    def _answer(self, surface, b):
        self.send(b)


class AgentsUI:
    """The GTK side: the shelf (a corner overlay that never takes focus) and the Agents window."""

    def __init__(self, env, bridge, prefs, set_pref, service_command):
        from atticus_shell import box, web_bridge
        self.env, self.bridge, self.views = env, bridge, {}
        post = lambda fn: __import__('gi.repository.GLib', fromlist=['x']).idle_add(lambda: (fn(), False)[1])
        self.service = AgentService(service_command or box.py_command(['--agents']), lambda e: self.controller.receive(e), post)
        self.controller = AgentsController(self.service.send, self._js, self._open, self._notify, set_pref, prefs, window=self._window)
        self.web_bridge = web_bridge
        self.service.start()

    def _surface(self, name):
        if name in self.views:
            return self.views[name]
        from atticus_shell import windows
        from gi.repository import Gtk
        view = self.web_bridge.make_view('agents.html', {'agents': lambda m, n=name: self.controller.page(n, m)})
        outs = self.bridge.outputs()
        if name == 'shelf':
            from atticus_shell.desktop.bridge import Output
            out = outs[0] if outs else Output('screen', 0, 0, 1280, 800)
            win = windows.overlay('chip', out, focusable=False, env=self.env)
            win.set_default_size(340, 52)
            x, y, w, h = out.work_area
            windows.move(win, x + w - 354, y + 10)
        else:
            win = Gtk.Window(title='Agents')
            win.set_wmclass('atticus-agents', 'Atticus')
            win.set_default_size(760, 560)
            win.connect('delete-event', lambda *_: (win.hide(), True)[1])
        win.add(view)
        self.views[name] = (win, view)
        return win, view

    def _js(self, surface, call, value):
        if surface in self.views:
            self.web_bridge.call_js(self.views[surface][1], 'agents.' + call, [value])

    def _window(self, kind, **args):
        if kind == 'show_shelf':
            self._surface('shelf')[0].show_all()
        elif kind == 'open_view':
            win, _ = self._surface('view')
            win.show_all()
            win.present()
        elif kind == 'close_view' and 'view' in self.views:
            self.views['view'][0].hide()
        elif kind == 'shelf_height' and 'shelf' in self.views:
            win = self.views['shelf'][0]
            win.resize(win.get_size()[0], int(args['h']))

    def _open(self, path, reveal):
        subprocess.run(['xdg-open', path if not reveal else (path.rsplit('/', 1)[0] or '/')], capture_output=True)

    def _notify(self, text):
        subprocess.run(['notify-send', '--app-name=Atticus', 'Atticus', text[:2000]], capture_output=True)

    def open_view(self):
        self._window('open_view')
