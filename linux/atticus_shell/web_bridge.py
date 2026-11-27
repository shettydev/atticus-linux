"""WebKitGTK views for ui/*.html: the same window.webkit.messageHandlers bridge as macOS's WKWebView.

Ports loadPage, callJS and PageErrors (Atticus.swift:950-998): pages load with tokens.css, orbs.js and i18n.js inlined at
their markers and no base URL; Python calls page functions with JSON-spread arguments; uncaught page errors report only
code locations (page, error class, file, line, column), never the message, which can hold the user's words.
"""
import json
import os
import re

HANDLER_PAGES = ('c', 'h', 'st', 'ob', 'meeting', 'agents', 'drawingMath')
ERROR_SCRIPT = ("(()=>{const seen=new Set(),send=(e,file,line,col)=>{const r=['%s',e?e.constructor?.name||typeof e:'muted',"
                "String(file||'').split('/').pop().slice(0,60),+line||0,+col||0],k=r.join();"
                "if(!seen.has(k)){seen.add(k);window.webkit?.messageHandlers.atticusError?.postMessage(r)}};"
                "addEventListener('error',e=>send(e.error,e.filename,e.lineno,e.colno));"
                "addEventListener('unhandledrejection',e=>{const m=/([^\\/@\\s]*):(\\d+):(\\d+)/.exec(e.reason?.stack||'')||[];"
                "send(e.reason,m[1],m[2],m[3])})})()")


def resources_dir():
    return os.environ.get('ATTICUS_RESOURCES_DIR') or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(path):
    try:
        with open(path, encoding='utf-8') as f:
            return f.read()
    except OSError:
        return None


def compose_page(name, resources, language, auto_language):
    ui = os.path.join(resources, 'ui')
    html = _read(os.path.join(ui, name))
    if html is None:
        return 'missing ' + name
    head = f"window.atticusLanguage = '{language}'; window.atticusAutoLanguage = '{auto_language}'; window.atticusPlatform = 'linux';\n"
    return (html.replace('/*TOKENS*/', _read(os.path.join(ui, 'tokens.css')) or '')
            .replace('/*ORBS*/', _read(os.path.join(ui, 'orbs.js')) or '')
            .replace('/*I18N*/', head + (_read(os.path.join(ui, 'i18n.js')) or '')))


def js_call(fn, args):
    return f'{fn}(...{json.dumps(args).replace("</", "<\\/")})'


def eval_error_row(call, message, line, column):
    m = re.match(r'^[A-Z]\w{0,30}(?:Error|Exception)(?=: )', message or '')
    return [re.match(r'[A-Za-z]*', call).group(0), m.group(0) if m else 'thrown', 'eval', str(line or 0), str(column or 0)]


def _gi():
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('WebKit2', '4.1')
    from gi.repository import Gdk, Gtk, WebKit2
    return Gdk, Gtk, WebKit2


def _value(js_result):
    value = js_result.get_js_value() if hasattr(js_result, 'get_js_value') else js_result
    text = value.to_json(0)
    return json.loads(text) if text else None


def make_view(page, handlers, resources=None, on_error=None, language='en', auto_language='en'):
    """A transparent WebView with page loaded and each handler name registered. view.loaded turns True when it finishes."""
    Gdk, Gtk, WebKit2 = _gi()
    manager = WebKit2.UserContentManager()
    for name, fn in handlers.items():
        manager.register_script_message_handler(name)
        manager.connect(f'script-message-received::{name}', lambda _m, res, fn=fn: fn(_value(res)))
    report = on_error or (lambda row: None)
    manager.register_script_message_handler('atticusError')
    manager.connect('script-message-received::atticusError', lambda _m, res: _report(report, _value(res)))
    stem = os.path.splitext(page)[0]
    manager.add_script(WebKit2.UserScript.new(ERROR_SCRIPT % stem, WebKit2.UserContentInjectedFrames.TOP_FRAME,
                                              WebKit2.UserScriptInjectionTime.START, None, None))
    view = WebKit2.WebView.new_with_user_content_manager(manager)
    view.set_background_color(Gdk.RGBA(0, 0, 0, 0))
    view.loaded = False
    view.page_name = stem
    view.report_error = report

    def changed(_v, event):
        if event == WebKit2.LoadEvent.FINISHED:
            view.loaded = True
    view.connect('load-changed', changed)
    view.load_html(compose_page(page, resources or resources_dir(), language, auto_language), None)
    return view


def _report(report, row):
    if isinstance(row, list) and len(row) == 5:
        report([str(v) for v in row])


def call_js(view, fn, args):
    if view is None:
        return
    script = js_call(fn, args)

    def done(v, result):
        try:
            (v.evaluate_javascript_finish if hasattr(v, 'evaluate_javascript_finish') else v.run_javascript_finish)(result)
        except Exception as e:   # GLib.Error: "TypeError: …" for a JavaScript exception
            message = str(getattr(e, 'message', e))
            view.report_error(eval_error_row(fn, message, 0, 0))
    if hasattr(view, 'evaluate_javascript'):
        view.evaluate_javascript(script, -1, None, None, None, done)
    else:
        view.run_javascript(script, None, done)
