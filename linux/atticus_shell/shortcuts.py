"""Gets Atticus's shortcuts onto this desktop, each the way it works best, and never fails silently.

  x11       passive key grabs (hotkeys.X11Grab)
  gnome     Atticus's own Shell extension grabs the keys: press and release, no dialog, GNOME 46-50
  hyprland  `hyprctl keyword bind` / `bindr` that run one gdbus call per edge (apps can't set keys there by themselves)
  sway      `swaymsg bindsym` / `bindsym --release`, the same way
  portal    org.freedesktop.portal.GlobalShortcuts (KDE): the desktop asks the user once
  manual    nothing above: say how to add the shortcut by hand
The result goes to $XDG_RUNTIME_DIR/atticus-shortcuts.json (atticus-shell --doctor reads it) and, if the talk shortcut
isn't working, to one desktop notification.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

from atticus_shell.hotkeys import ORDER, parse_trigger

GNOME_MODS = {'CTRL': '<Control>', 'ALT': '<Alt>', 'SHIFT': '<Shift>', 'LOGO': '<Super>'}
HYPR_MODS = {'CTRL': 'CTRL', 'ALT': 'ALT', 'SHIFT': 'SHIFT', 'LOGO': 'SUPER'}
HYPR_BITS = {'SHIFT': 1, 'CTRL': 4, 'ALT': 8, 'LOGO': 64}
SWAY_MODS = {'CTRL': 'Ctrl', 'ALT': 'Mod1', 'SHIFT': 'Shift', 'LOGO': 'Mod4'}
LABEL_MODS = {'CTRL': 'Ctrl', 'ALT': 'Alt', 'SHIFT': 'Shift', 'LOGO': 'Super'}
MOD_ORDER = ('CTRL', 'ALT', 'SHIFT', 'LOGO')
MARK = 'app.atticus.Shell'   # how Atticus recognises a binding it made itself
MANUAL = ('Atticus can’t set its shortcut on this desktop by itself. Add a keyboard shortcut in your system settings '
          'that runs: ~/.local/share/atticus/atticus-shell --trigger talk press (then press it once to talk, once to send).')


@dataclass
class Result:
    route: str
    bound: dict = field(default_factory=dict)
    error: str = ''


def route(env, gnome_ready):
    if env.session == 'x11':
        return 'x11'
    if env.compositor == 'gnome' and gnome_ready:
        return 'gnome'
    if env.compositor == 'hyprland':
        return 'hyprland'
    if env.compositor == 'sway':
        return 'sway'
    if 'GlobalShortcuts' in env.portals:
        return 'portal'
    return 'manual'


def _mods(trigger):
    mods, key = parse_trigger(trigger)
    return [m for m in MOD_ORDER if m in mods], key


def gnome_accel(trigger):
    mods, key = _mods(trigger)
    return ''.join(GNOME_MODS[m] for m in mods) + key


def label(trigger):
    mods, key = _mods(trigger)
    return '+'.join([LABEL_MODS[m] for m in mods] + [key[:1].upper() + key[1:]])


def trigger_command(name, edge):
    """One gdbus call: much faster than starting Python for each key press."""
    return f'gdbus call --session --dest app.atticus.Shell --object-path /app/atticus/Shell --method app.atticus.Shell.Trigger {name} {edge}'


def _run(args, **kw):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=5, **kw)
    except (OSError, subprocess.SubprocessError) as e:
        return subprocess.CompletedProcess(args, 1, '', str(e))


def _names(bindings):
    return [n for n in ORDER if n in bindings]


def bind_hyprland(bindings, run=_run):
    names = _names(bindings)
    r = run(['hyprctl', '-j', 'binds'])
    if r.returncode:
        return Result('hyprland', {n: False for n in names}, f'hyprctl didn’t answer: {(r.stderr or r.stdout).strip()[:200]}')
    try:
        existing = json.loads(r.stdout or '[]')
    except ValueError:
        existing = []
    cmds, bound, problems = [], {}, []
    for name in names:
        mods, key = _mods(bindings[name])
        mask = sum(HYPR_BITS[m] for m in mods)
        combo = f'{" ".join(HYPR_MODS[m] for m in mods)}, {key}'
        same = [b for b in existing if b.get('modmask') == mask and str(b.get('key', '')).lower() == key.lower()]
        theirs = [b for b in same if MARK not in str(b.get('arg', ''))]
        if theirs:
            bound[name] = False
            problems.append(f'{label(bindings[name])} is already used in Hyprland ({str(theirs[0].get("arg", "")).strip()[:80]}); '
                            f'change it there or pick another shortcut in Atticus Settings')
            continue
        if same:
            cmds.append(f'keyword unbind {combo}')   # Atticus restarted: replace its own binds
        cmds += [f'keyword bind {combo}, exec, {trigger_command(name, "down")}',
                 f'keyword bindr {combo}, exec, {trigger_command(name, "up")}']
        bound[name] = True
    if cmds:
        r = run(['hyprctl', '--batch', ' ; '.join(cmds)])
        if r.returncode or 'error' in (r.stdout or '').lower():
            return Result('hyprland', {n: False for n in names}, f'Hyprland refused the shortcuts: {(r.stdout or r.stderr).strip()[:200]}')
    return Result('hyprland', bound, '. '.join(problems))


def _sway_combo_set(combo):
    alias = {'mod1': 'alt', 'alt': 'alt', 'ctrl': 'ctrl', 'control': 'ctrl', 'mod4': 'super', 'shift': 'shift'}
    return frozenset(alias.get(p.lower(), p.lower()) for p in combo.split('+'))


def bind_sway(bindings, run=_run):
    names = _names(bindings)
    r = run(['swaymsg', '-t', 'get_config'])
    try:
        config = json.loads(r.stdout or '{}').get('config', '') if not r.returncode else ''
    except ValueError:
        config = ''
    taken = {}
    for line in config.splitlines():
        m = re.match(r'\s*bindsym\s+((?:--\S+\s+)*)(\S+)\s+(.*)', line)
        if m and MARK not in m.group(3):
            taken[_sway_combo_set(m.group(2))] = m.group(3).strip()
    bound, problems = {}, []
    for name in names:
        mods, key = _mods(bindings[name])
        combo = '+'.join([SWAY_MODS[m] for m in mods] + [key])
        if _sway_combo_set(combo) in taken:
            bound[name] = False
            problems.append(f'{label(bindings[name])} is already used in Sway ({taken[_sway_combo_set(combo)][:80]})')
            continue
        ok = True
        for args in (f'bindsym --no-repeat {combo} exec {trigger_command(name, "down")}',
                     f'bindsym --release {combo} exec {trigger_command(name, "up")}'):
            ok = run(['swaymsg', args]).returncode == 0 and ok
        bound[name] = ok
        if not ok:
            problems.append(f'Sway refused {label(bindings[name])}')
    return Result('sway', bound, '. '.join(problems))


def bind_gnome(bindings, bridge):
    names = _names(bindings)
    r = bridge._call('bind_shortcuts', shortcuts={n: gnome_accel(bindings[n]) for n in names}) or {}
    if not r.get('ok'):
        return Result('gnome', {n: False for n in names}, f'The Atticus GNOME extension didn’t take the shortcuts: {r.get("error", "no answer")}')
    bound = {n: bool((r.get('bound') or {}).get(n)) for n in names}
    refused = [label(bindings[n]) for n in names if not bound[n]]
    error = (f'GNOME wouldn’t give Atticus {", ".join(refused)}: another app or GNOME itself uses it. '
             'Pick another shortcut in Atticus Settings, or free it in GNOME Settings → Keyboard.') if refused else ''
    return Result('gnome', bound, error)


def status_path():
    return os.path.join(os.environ.get('XDG_RUNTIME_DIR') or tempfile.gettempdir(), 'atticus-shortcuts.json')


def notify_send(text):
    _run(['notify-send', '--app-name=Atticus', 'Atticus shortcut', text[:1000]])


def report(result, path=None, notify=notify_send):
    """Saved for --doctor; logged; and one notification when the talk shortcut doesn't work."""
    try:
        with open(path or status_path(), 'w') as f:
            json.dump({'route': result.route, 'bound': result.bound, 'error': result.error}, f)
    except OSError:
        pass
    if result.error:
        print(f'atticus: shortcuts ({result.route}): {result.error}', file=sys.stderr, flush=True)
    if result.route == 'manual' or (result.bound and not result.bound.get('talk', True)):
        notify(result.error or MANUAL)
