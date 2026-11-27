"""How each desktop gets Atticus's shortcuts: route choice, the commands sent, conflicts, and failures that are never silent."""
import json
import os
import subprocess
import tempfile
import unittest
from types import SimpleNamespace

from tests import linux  # noqa: F401
from atticus_shell import shortcuts as S

BINDINGS = {'talk': 'CTRL+ALT+space', 'dictate': 'CTRL+LOGO+space'}


def env(session='wayland', compositor='gnome', portals=()):
    return SimpleNamespace(session=session, compositor=compositor, portals=frozenset(portals))


class RouteTests(unittest.TestCase):
    def test_routes(self):
        self.assertEqual(S.route(env('x11', 'x11'), gnome_ready=False), 'x11')
        self.assertEqual(S.route(env(compositor='gnome', portals={'GlobalShortcuts'}), gnome_ready=True), 'gnome')   # no portal dialog
        self.assertEqual(S.route(env(compositor='gnome', portals={'GlobalShortcuts'}), gnome_ready=False), 'portal')   # before relogin
        self.assertEqual(S.route(env(compositor='gnome'), gnome_ready=False), 'manual')
        self.assertEqual(S.route(env(compositor='hyprland', portals={'GlobalShortcuts'}), gnome_ready=False), 'hyprland')
        self.assertEqual(S.route(env(compositor='sway'), gnome_ready=False), 'sway')
        self.assertEqual(S.route(env(compositor='kde', portals={'GlobalShortcuts'}), gnome_ready=False), 'portal')


class FormatTests(unittest.TestCase):
    def test_gnome_accelerators(self):
        self.assertEqual(S.gnome_accel('CTRL+ALT+space'), '<Control><Alt>space')
        self.assertEqual(S.gnome_accel('CTRL+LOGO+space'), '<Control><Super>space')
        self.assertEqual(S.gnome_accel('CTRL+ALT+Return'), '<Control><Alt>Return')

    def test_trigger_command_is_one_fast_dbus_call(self):
        self.assertEqual(S.trigger_command('talk', 'down'),
                         'gdbus call --session --dest app.atticus.Shell --object-path /app/atticus/Shell --method app.atticus.Shell.Trigger talk down')

    def test_label(self):
        self.assertEqual(S.label('CTRL+ALT+space'), 'Ctrl+Alt+Space')
        self.assertEqual(S.label('CTRL+LOGO+space'), 'Ctrl+Super+Space')


class Runner:
    def __init__(self, outputs=None, fail=()):
        self.calls, self.outputs, self.fail = [], outputs or {}, set(fail)

    def __call__(self, args, **kw):
        self.calls.append(args)
        key = ' '.join(args[:3])
        out = next((v for k, v in self.outputs.items() if ' '.join(args).startswith(k)), '')
        return subprocess.CompletedProcess(args, 1 if args[0] in self.fail else 0, out, 'boom' if args[0] in self.fail else '')


class HyprlandTests(unittest.TestCase):
    def test_binds_press_and_release(self):
        run = Runner({'hyprctl -j binds': '[]'})
        result = S.bind_hyprland(BINDINGS, run=run)
        self.assertEqual(result.bound, {'talk': True, 'dictate': True})
        batch = run.calls[-1]
        self.assertEqual(batch[:2], ['hyprctl', '--batch'])
        self.assertIn('keyword bind CTRL ALT, space, exec, ' + S.trigger_command('talk', 'down'), batch[2])
        self.assertIn('keyword bindr CTRL ALT, space, exec, ' + S.trigger_command('talk', 'up'), batch[2])
        self.assertIn('keyword bind CTRL SUPER, space, exec, ' + S.trigger_command('dictate', 'down'), batch[2])

    def test_restart_replaces_its_own_binds(self):
        mine = [{'modmask': 12, 'key': 'space', 'dispatcher': 'exec', 'arg': S.trigger_command('talk', 'down')}]
        run = Runner({'hyprctl -j binds': json.dumps(mine)})
        S.bind_hyprland({'talk': 'CTRL+ALT+space'}, run=run)
        self.assertIn('keyword unbind CTRL ALT, space', run.calls[-1][2])

    def test_never_takes_a_key_the_user_bound(self):
        theirs = [{'modmask': 12, 'key': 'space', 'dispatcher': 'exec', 'arg': 'rofi -show drun'}]
        run = Runner({'hyprctl -j binds': json.dumps(theirs)})
        result = S.bind_hyprland(BINDINGS, run=run)
        self.assertEqual(result.bound, {'talk': False, 'dictate': True})
        self.assertIn('Ctrl+Alt+Space is already used in Hyprland (rofi -show drun)', result.error)
        self.assertNotIn('CTRL ALT, space', run.calls[-1][2])

    def test_hyprctl_failure_is_reported(self):
        result = S.bind_hyprland(BINDINGS, run=Runner(fail={'hyprctl'}))
        self.assertEqual(result.bound, {'talk': False, 'dictate': False})
        self.assertTrue(result.error)


class SwayTests(unittest.TestCase):
    def test_binds_press_and_release(self):
        run = Runner({'swaymsg -t get_config': json.dumps({'config': 'bindsym Mod4+Return exec foot\n'})})
        result = S.bind_sway({'talk': 'CTRL+ALT+space'}, run=run)
        self.assertEqual(result.bound, {'talk': True})
        sent = [c for c in run.calls if c[0] == 'swaymsg' and c[1] != '-t']
        self.assertEqual(sent, [['swaymsg', 'bindsym --no-repeat Ctrl+Mod1+space exec ' + S.trigger_command('talk', 'down')],
                                ['swaymsg', 'bindsym --release Ctrl+Mod1+space exec ' + S.trigger_command('talk', 'up')]])

    def test_never_takes_a_key_the_user_bound(self):
        run = Runner({'swaymsg -t get_config': json.dumps({'config': 'bindsym Mod1+Control+space exec wofi\n'})})
        result = S.bind_sway({'talk': 'CTRL+ALT+space'}, run=run)
        self.assertEqual(result.bound, {'talk': False})
        self.assertIn('already used in Sway', result.error)


class GnomeTests(unittest.TestCase):
    def test_asks_the_extension(self):
        calls = []
        bridge = SimpleNamespace(_call=lambda method, **a: calls.append((method, a)) or {'ok': True, 'bound': {'talk': True, 'dictate': False}})
        result = S.bind_gnome(BINDINGS, bridge)
        self.assertEqual(calls, [('bind_shortcuts', {'shortcuts': {'talk': '<Control><Alt>space', 'dictate': '<Control><Super>space'}})])
        self.assertEqual(result.bound, {'talk': True, 'dictate': False})
        self.assertIn('Ctrl+Super+Space', result.error)   # GNOME refused it: another app or GNOME itself uses it


class ReportTests(unittest.TestCase):
    def test_status_file_and_one_notification_when_talk_fails(self):
        path = os.path.join(tempfile.mkdtemp(), 'shortcuts.json')
        notes = []
        S.report(S.Result('hyprland', {'talk': False, 'dictate': True}, 'Ctrl+Alt+Space is already used in Hyprland (rofi)'),
                 path=path, notify=notes.append)
        saved = json.loads(open(path).read())
        self.assertEqual(saved['route'], 'hyprland')
        self.assertEqual(saved['bound'], {'talk': False, 'dictate': True})
        self.assertEqual(len(notes), 1)
        self.assertIn('rofi', notes[0])

    def test_success_is_quiet(self):
        notes = []
        S.report(S.Result('gnome', {'talk': True}, ''), path=os.path.join(tempfile.mkdtemp(), 's.json'), notify=notes.append)
        self.assertEqual(notes, [])

    def test_manual_route_explains(self):
        notes = []
        S.report(S.Result('manual', {}, S.MANUAL), path=os.path.join(tempfile.mkdtemp(), 's.json'), notify=notes.append)
        self.assertIn('--trigger talk press', notes[0])


if __name__ == '__main__':
    unittest.main()
