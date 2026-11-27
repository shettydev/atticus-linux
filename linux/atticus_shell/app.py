"""The running Atticus: GTK main loop, one instance, the box window and its WebView, shortcuts, mic and speech."""
import os
import threading

from atticus_shell import box as B
from atticus_shell import windows
from atticus_shell.desktop.bridge import PointerUnavailable, output_at


def _gtk():
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import GLib, Gtk
    return GLib, Gtk


def post(fn):
    GLib, _ = _gtk()
    GLib.idle_add(lambda: (fn(), False)[1])


def later(secs, fn):
    GLib, _ = _gtk()
    GLib.timeout_add(max(0, int(secs * 1000)), lambda: (fn(), False)[1])


class PostingEngine:
    """Engines call back on audio or worker threads; the controller lives on the main loop."""

    def __init__(self, engine):
        self.engine = engine
        self.listening = False

    def start(self, lang, on_partial, on_final):
        self.listening = True
        self.engine.start(lang, lambda t: post(lambda: on_partial(t)), lambda t: post(lambda: on_final(t)))
        if hasattr(self.engine, 'pump'):   # Whisper: re-transcribe the growing recording for live words
            def pump():
                import time
                while self.listening:
                    self.engine.pump()
                    time.sleep(0.3)
            threading.Thread(target=pump, daemon=True).start()

    def feed(self, pcm):
        self.engine.feed(pcm)

    def finish(self):
        self.listening = False
        threading.Thread(target=self.engine.finish, daemon=True).start()   # Whisper's last pass takes a moment

    def cancel(self):
        self.listening = False
        self.engine.cancel()


def models_dir():
    base = os.environ.get('ATTICUS_DIR') or os.path.expanduser('~/.config/atticus')
    return os.path.join(base, 'models')


def engine_factory(prefs):
    """The speech engine Settings picked, or None when its model isn't downloaded yet."""
    from atticus_shell.speech import stt
    cache = {}

    def make():
        if prefs.get('speechModel') == 'whisper':
            model = stt.WHISPER_MODELS.get(prefs.get('whisperModel', 'turbo'), stt.WHISPER_MODELS['turbo'])
            path = os.path.join(os.path.dirname(models_dir()), 'whisper', model.file)
            if not os.path.exists(path):
                return None
            server = cache.setdefault(('whisper', path), stt.WhisperServer(path))
            return PostingEngine(stt.WhisperCpp(transcribe=server.transcribe))
        store = stt.ModelStore(models_dir(), stt.SHERPA_MODELS)
        if not store.installed('builtin'):
            return None
        try:
            if 'sherpa' not in cache:
                cache['sherpa'] = stt.SherpaStreaming(store.ensure('builtin')).recognizer
            return PostingEngine(stt.SherpaStreaming(recognizer=cache['sherpa']))
        except (ImportError, stt.ModelError):
            return None
    return make


class MicAdapter:
    def __init__(self, prefs):
        self.prefs, self.mic = prefs, None

    def start(self, feed):
        from atticus_shell import audio
        dev = audio.listen_mic(self.prefs.get('microphone', 'automatic'))
        self.mic = audio.Mic()
        self.mic.start(dev.uid if dev else '', feed)

    def stop(self):
        if self.mic:
            self.mic.stop()
            self.mic = None


class GtkWin:
    """The window side of BoxController."""

    def __init__(self, box):
        self.box = box

    @property
    def visible(self):
        return self.box.window.get_visible()

    def show(self):
        self.box.place()
        self.box.window.show_all()

    def hide(self):
        self.box.window.hide()

    def take_keys(self):
        self.box.window.set_accept_focus(True)
        self.box.window.present()

    def release_keys(self):
        self.box.window.set_accept_focus(False)

    def set_height(self, h):
        self.box.height = int(h)
        self.box.place(keep_position=True)


class Box:
    def __init__(self, prefs, env, bridge, brain_command=None, engine=None, mic=None, speak=None, app=None, set_pref=None, cue=None):
        from atticus_shell import web_bridge
        self.prefs, self.env, self.bridge = prefs, env, bridge
        self.height = int(prefs.get('boxH') or 140)
        self.output = None
        self.brain_command = brain_command or B.py_command(['--wait'])
        self.ready, self.queued = False, []   # ui.html defines window.ui after loading; it says so with {ready: 1}
        self.view = web_bridge.make_view('ui.html', {'c': self._page_message, 'h': lambda m: self.controller.handle('h', m)})
        outputs = bridge.outputs()
        from atticus_shell.desktop.bridge import Output
        first = outputs[0] if outputs else Output('screen', 0, 0, 1280, 800)
        self.window = windows.overlay('box', first, focusable=False, env=env)
        self.window.add(self.view)
        self.window.connect('key-press-event', self._key)
        self.controller = B.BoxController(self._ui, GtkWin(self),
                                          brain=self._brain, engine=engine or engine_factory(prefs), prefs=prefs, later=later,
                                          cue=cue or (lambda name: None), mic=mic, speak=speak, app=app, set_pref=set_pref)
        self._wire_pointing()

    def _wire_pointing(self):
        from atticus_shell import pen
        c, bridge = self.controller, self.bridge
        sampler, state = pen.PointerSampler(bridge.pointer), {}

        class Pen:   # the trail while the shortcut is held
            def start(_):
                GLib, _gtk_ = _gtk()
                out = self.pointer_output()
                if out is None:
                    return
                sampler.start()
                sampler.tick()
                trail = pen.TrailOverlay(out, self.env, capture=sampler.needs_capture, on_motion=sampler.motion)
                state['trail'] = trail
                trail.show()

                def tick():
                    if not sampler.running:
                        return False
                    sampler.tick()
                    if sampler.samples:
                        trail.add(*sampler.samples[-1][1:])
                    return True
                GLib.timeout_add(16, tick)

            def stop(_):
                samples = sampler.stop()
                trail = state.pop('trail', None)
                if trail:
                    trail.hide()
                    trail.window.destroy()
                return samples

        class Pointing:   # Point at your screen
            def begin(_, done):
                out = self.pointer_output()
                x, y = self.window.get_position()
                w, h = self.window.get_size()
                state['view'] = pen.PointerView(out, self.env, box=(x, y, w, h), done=done)
                state['view'].show()

            def finish(_):
                view = state.pop('view', None)
                if view:
                    view.hide()
                    view.window.destroy()

        def element(point):
            try:
                from atticus_shell.a11y import atspi_apps
                return pen.element_at(bridge, point, atspi_apps())
            except Exception:
                return ''
        c.pen, c.pointing, c.classify, c.thumb = Pen(), Pointing(), pen.brain_classify, pen.thumb
        c.make_point_context = lambda points: pen.point_context(points, bridge.outputs(), lambda r, p: pen.capture_region(bridge, r, p), element)

    def _ui(self, fn, *args):
        from atticus_shell import web_bridge
        if not self.ready:
            self.queued.append((fn, args))
            return
        web_bridge.call_js(self.view, 'ui.' + fn, list(args))

    def _page_message(self, m):
        if isinstance(m, dict) and 'ready' in m and not self.ready:
            self.ready = True
            queued, self.queued = self.queued, []
            for fn, args in queued:
                self._ui(fn, *args)
        self.controller.handle('c', m)

    def _brain(self, on_line, on_exit):
        env = B.py_env(os.environ, B.app_path(), {'ATTICUS_UI': '1', 'ATTICUS_FRONT': self.controller.front if hasattr(self, 'controller') else '',
                                                  'ATTICUS_SCREEN': self.output.name if self.output else '', 'ATTICUS_FOCUS_GUARD': '1'})
        return B.BrainRun(self.brain_command, env, on_line, on_exit, post)

    def _key(self, _w, event):
        from gi.repository import Gdk
        if event.keyval == Gdk.KEY_Escape:
            self.controller.escape()
            return True
        return False

    def pointer_output(self):
        outs = self.bridge.outputs()
        if not outs:
            return None
        try:
            x, y = self.bridge.pointer()
        except PointerUnavailable:
            return outs[0]
        name = output_at(outs, x, y) if self.prefs.get('followPointer', True) else (self.output.name if self.output else outs[0].name)
        return next((o for o in outs if o.name == name), outs[0])

    def place(self, keep_position=False):
        out = self.output if keep_position and self.output else self.pointer_output()
        if out is None:
            return
        self.output = out
        width = B.box_width(self.prefs.get('boxW', 500), out.work_area[2])
        if keep_position:
            x, y = self.window.get_position()
        else:
            x, y = B.initial_position(out, width, self.prefs.get('corner', 'top-left'))
        x, y, width, height = B.clamp_frame((x, y, width, self.height), out.work_area) if not keep_position else (x, y, width, self.height)
        windows.move(self.window, x, y)
        self.window.resize(int(width), int(height))
        self.view.set_size_request(int(width), int(height))


class WorkerFinalEngine(PostingEngine):
    """Dictation: live words go to the main loop, the final result stays on the worker (cleanup and insert may take seconds)."""

    def start(self, lang, on_partial, on_final):
        self.listening = True
        self.engine.start(lang, lambda t: post(lambda: on_partial(t)), on_final)


def _dictation(prefs, env, bridge, app_box, cue):
    from atticus_shell import a11y, dictation, input as I, insert as ins, selection
    data = dictation.DictationData(os.path.join(os.path.dirname(prefs.path), 'dictation.json'), prefs.d, prefs.set)
    base = engine_factory(prefs.d)

    def engine():
        e = base()
        return WorkerFinalEngine(e.engine) if e else None

    def capture():
        try:
            return selection.capture(a11y.atspi_apps(), enabled=True, allow_empty=True)
        except Exception:
            return None
    try:
        injector = I.injector_for(env, bridge)
    except Exception:
        injector = None

    def do_insert(text, sel):
        w = next((w for w in bridge.windows() if w.pid == sel.pid), None)
        try:
            return ins.insert(text, sel, sel.focus_identity, lambda: a11y.capture_app(sel.app_obj).focusIdentity,
                              I.clipboard_for(env), injector, w.app_id if w else '')
        except ins.NotDone:
            return 'saved'

    def ui(fn, arg=None):
        def show():
            if fn == 'dictate' and arg:
                app_box.controller.show()
            app_box._ui(fn, *([] if arg is None else [arg]))
            if fn == 'dictated':
                app_box.controller.hide(0.8 if arg and arg.get('ok') else 6)
        post(show)
    return dictation.DictationController(ui, engine, MicAdapter(prefs.d), prefs.d, data, capture, do_insert, dictation.brain_cleanup,
                                         later, lambda key: post(lambda: cue(key)))


def main(env):
    GLib, Gtk = _gtk()
    from atticus_shell import hotkeys, ipc
    from atticus_shell.desktop.bridge import bridge_for
    from atticus_shell.prefs import Prefs
    prefs = Prefs()

    class Shell:   # what app.atticus.Shell callers reach
        def raise_(self, user):
            app_box.controller.show()

        def trigger(self, which, edge):
            edges(which, edge)

        def hide_overlays(self, ms):
            app_box.window.set_opacity(0)
            later(ms / 1000, lambda: app_box.window.set_opacity(1))

        def bridge_event(self, data):
            pass

        def open_settings(self):
            pass

    if not ipc.serve(Shell()):
        ipc.call('Raise', (False,))
        return 0
    bridge = bridge_for(env)
    from atticus_shell.speech import tts
    speaker = tts.Speaker()
    res = B.resources()
    app_box = Box(prefs.d, env, bridge, mic=MicAdapter(prefs.d), set_pref=prefs.set,
                  speak=lambda text: speaker.say(tts.read_aloud_text(text), prefs.num('voiceRate') or 196),
                  cue=lambda key: tts.play_sound(prefs.str(key), prefs.num('volume'), res))
    app_box.controller.voice = speaker
    from atticus_shell import tray

    def menu(item):
        c = app_box.controller
        if item == 'ask':
            c.show()
            c.js('compose', True)
            app_box.window.set_accept_focus(True)
            app_box.window.present()
        elif item in ('pause', 'resume'):
            prefs.set('paused', item == 'pause')
            panel.refresh()
        elif item == 'tips-again':
            prefs.set('tips', True)
            panel.refresh()
        elif item == 'tip':
            Gtk.show_uri_on_window(None, 'https://ko-fi.com/saarthr', 0)
        elif item == 'quit':
            Gtk.main_quit()
        else:
            getattr(c.app, 'menu_' + item.replace('-', '_'), lambda: None)()   # agents, shelf, dictation, meeting, settings: later tasks
    panel = tray.Tray(menu, lambda: {'paused': prefs.bool('paused'), 'tips': prefs.bool('tips'), 'dictate': prefs.bool('dictate'),
                                     'listening': app_box.controller.listening})
    debounce = hotkeys.HoldMachine('hold')

    dictate = _dictation(prefs, env, bridge, app_box, cue=lambda key: tts.play_sound(prefs.str(key), prefs.num('volume'), res))

    def edges(which, edge):
        if which == 'dictate':
            if prefs.bool('dictate'):
                dictate.edge(edge)
            return
        if which != 'talk':
            return
        for intent in (debounce.down() if edge == 'down' else debounce.up()):
            app_box.controller.shortcut(intent == 'start')

    def tick():
        for _ in debounce.tick():
            app_box.controller.shortcut(False)
        return True
    GLib.timeout_add(20, tick)
    bindings = {'talk': prefs.str('linuxHotkey'), 'dictate': prefs.str('linuxDictateHotkey'),
                'handoff': prefs.str('linuxHandoffHotkey'), 'meeting': prefs.str('linuxMeetingHotkey')}
    source = hotkeys.pick_source(env)
    if source == 'x11':
        hotkeys.X11Grab(bindings, lambda w, e: post(lambda: edges(w, e))).start()
    elif source == 'portal':
        try:
            hotkeys.PortalShortcuts(hotkeys.GioPortal(), bindings, lambda w, e: post(lambda: edges(w, e))).start()
        except Exception:
            pass   # onboarding shows how to bind the shortcut by hand
    Gtk.main()
    return 0
