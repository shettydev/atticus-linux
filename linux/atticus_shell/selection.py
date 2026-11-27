"""What the user selected, or where their caret is (struct TextSelection, Atticus.swift:1051), through AT-SPI.

Highlighted text in the focused field, or (allow_empty) an empty caret in an editable one where a draft or dictation can
go. Password fields are never read. Without an accessible focus, the PRIMARY selection is the user's highlight.
"""
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

from atticus_shell import a11y as A

MAX_TEXT = 6000


@dataclass
class TextSelection:
    app: str
    pid: int
    text: str
    caret: bool                 # an empty selection with a caret: insertion point
    editable: bool
    pastable: bool
    focus_identity: str = ''
    offset: int = 0
    acc: Any = None
    app_obj: Any = None


def read_primary():
    for cmd in (['wl-paste', '--primary', '--no-newline'], ['xclip', '-o', '-selection', 'primary']):
        if shutil.which(cmd[0]):
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=1)
                if r.returncode == 0 and r.stdout:
                    return r.stdout
            except (OSError, subprocess.SubprocessError):
                continue
    return ''


def focused(apps):
    """(app, accessible) with keyboard focus in an active window, or (None, None)."""
    for app in apps:
        for w in app.windows:
            if 'active' not in w.states:
                continue
            stack = [w]
            while stack:
                acc = stack.pop()
                if acc is not w and 'focused' in acc.states:
                    return app, acc
                stack.extend(reversed(acc.children()))
    return None, None


def capture(apps, enabled=True, allow_empty=False, primary=read_primary):
    if not enabled:
        return None
    app, acc = focused(apps)
    if acc is None:
        text = primary()
        return TextSelection('', 0, text[:MAX_TEXT], False, False, False) if text else None
    if acc.role == 'password text':
        return None
    content = A._content(acc) or ''
    rng, caret = acc.selection, acc.caret
    text = content[rng[0]:rng[1]] if rng and rng[1] > rng[0] else ''
    identity = A.capture_app(app).focusIdentity
    if text and len(text) <= MAX_TEXT:
        return TextSelection(app.name, app.pid, text, False, acc.editable, acc.editable, identity, rng[0], acc, app)
    if allow_empty and caret is not None and acc.editable:
        return TextSelection(app.name, app.pid, '', True, True, True, identity, caret, acc, app)
    return None
