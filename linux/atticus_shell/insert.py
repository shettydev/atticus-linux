"""Putting text at the user's caret (--type, dictation): accessibility first, then one borrowed-clipboard paste.

Never twice: when the field changed since it was checked, or an insert reported success but the text isn't there, the
words go to History instead of being typed again (GUIDE, Dictate anywhere).
"""
import sys
import time

from atticus_shell import a11y as A
from atticus_shell.input import ClipboardBorrow, paste_combo


class NotDone(Exception):
    pass


NEED_CARET = 'A verified empty caret is required. Place it with --hands, and never replace selected text.'


def insert(text, sel, expected_identity, current_identity, clipboard, injector, app_id, sleep=time.sleep):
    """'typed' | 'pasted' | 'unverified' (pasted, unreadable field) | 'saved' (nothing sent)."""
    if sel is None or sel.text or not sel.caret:
        raise NotDone(NEED_CARET)
    if current_identity() != expected_identity:
        return 'saved'
    acc, offset = sel.acc, sel.offset
    before = A._content(acc) or ''
    want = before[:offset] + text + before[offset:]
    if acc.insert_text(offset, text):
        return 'typed' if (A._content(acc) or '') == want else 'saved'
    if injector is None:
        return 'saved'
    with ClipboardBorrow(clipboard) as borrow:
        borrow.put(text)
        injector.key(paste_combo(app_id))
        sleep(0.15)   # the app reads the clipboard before it is given back
    after = A._content(acc)
    if after is None or after == before and not before:
        return 'unverified'
    return 'pasted' if text in after else 'saved'


def receipt_line(result):
    if result in ('typed', 'pasted'):
        return 'verified: Typing dispatched to the chosen composer. Do not repeat typing without inspecting it.'
    if result == 'unverified':
        return 'unverified: Typing dispatched to the chosen composer. Do not repeat typing without inspecting it.'
    return 'not_done: The chosen field lost focus or input could not be sent.'


def type_command(args):
    """--type: stdin's text at the verified caret; a hands map from this ask pins the window and composer."""
    from atticus_shell import hands, input as I
    from atticus_shell.bridge_cli import current_bridge
    from atticus_shell.desktop.env import detect
    try:
        apps = A.atspi_apps()
    except Exception:
        apps = []
    from atticus_shell import selection
    sel = selection.capture(apps, enabled=True, allow_empty=True)
    if sel is None or not sel.caret:
        print('not_done: ' + NEED_CARET)
        return 1
    mapped = hands.Hands(lambda: apps, current_bridge(), None).load()
    pinned = mapped if mapped and mapped.focusIdentity else None
    if pinned and (pinned.pid != sel.pid or pinned.focusIdentity != sel.focus_identity):
        print('not_done: The chosen window or composer changed. Run --hands see again.')
        return 1
    text = sys.stdin.read()
    env, bridge = detect(), current_bridge()
    try:
        injector = I.injector_for(env, bridge)
    except Exception:
        injector = None
    w = next((w for w in bridge.windows() if w.pid == sel.pid), None)
    result = insert(text, sel, sel.focus_identity, lambda: A.capture_app(sel.app_obj).focusIdentity,
                    I.clipboard_for(env), injector, w.app_id if w else '')
    print(receipt_line(result))
    return 0 if result != 'saved' else 1
