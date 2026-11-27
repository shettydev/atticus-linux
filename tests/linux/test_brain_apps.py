import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import linux
from atticus_shell import apps as P

DESKTOP = {
    'firefox.desktop': '[Desktop Entry]\nType=Application\nName=Firefox\nExec=firefox %u\nStartupWMClass=firefox\nIcon=firefox\n',
    'org.gnome.TextEditor.desktop': '[Desktop Entry]\nType=Application\nName=Text Editor\nExec=gnome-text-editor %U\n',
    'hidden.desktop': '[Desktop Entry]\nType=Application\nName=Hidden Helper\nExec=helper\nNoDisplay=true\n',
    'link.desktop': '[Desktop Entry]\nType=Link\nName=A Link\nURL=https://x\n',
    'Name[de].desktop': '[Desktop Entry]\nType=Application\nName=Rechner\nName[de]=Rechner\nExec=env FOO=1 gnome-calculator\n',
}


def app_dirs():
    root = tempfile.mkdtemp()
    sys_dir, flatpak = Path(root, 'usr'), Path(root, 'flatpak/exports/share/applications')
    sys_dir.mkdir()
    flatpak.mkdir(parents=True)
    for name, body in DESKTOP.items():
        (sys_dir / name).write_text(body)
    (flatpak / 'com.spotify.Client.desktop').write_text('[Desktop Entry]\nType=Application\nName=Spotify\nExec=/usr/bin/flatpak run com.spotify.Client\n')
    return [str(sys_dir), str(flatpak)]


class DesktopEntryTests(unittest.TestCase):
    def test_installed_skips_hidden_and_links(self):
        names = {a.name for a in P.installed(app_dirs(), cache=False)}
        self.assertEqual(names, {'Firefox', 'Text Editor', 'Rechner', 'Spotify'})

    def test_flatpak_app_discovered(self):
        spotify = P.find('spotify', P.installed(app_dirs(), cache=False))
        self.assertEqual(spotify.id, 'com.spotify.Client')
        self.assertEqual(P.exe_name(spotify), 'com.spotify.Client')   # a flatpak runs under its app id

    def test_exe_name_strips_env_and_field_codes(self):
        apps = P.installed(app_dirs(), cache=False)
        self.assertEqual(P.exe_name(P.find('rechner', apps)), 'gnome-calculator')
        self.assertEqual(P.exe_name(P.find('Firefox', apps)), 'firefox')

    def test_find_by_id_or_name(self):
        apps = P.installed(app_dirs(), cache=False)
        self.assertEqual(P.find('org.gnome.TextEditor', apps).name, 'Text Editor')
        self.assertIsNone(P.find('nope', apps))

    def test_window_belongs_to_app(self):
        apps = P.installed(app_dirs(), cache=False)
        self.assertTrue(P.owns(P.find('Text Editor', apps), 'org.gnome.TextEditor'))
        self.assertTrue(P.owns(P.find('Firefox', apps), 'firefox'))
        self.assertFalse(P.owns(P.find('Firefox', apps), 'org.gnome.TextEditor'))


SCREENS = [['eDP-1', 0, 0, 1280, 800, True], ['HDMI-1', 1280, 0, 1920, 1080, False]]
VISIBLE = [['eDP-1', 0, 32, 1280, 768, True], ['HDMI-1', 1280, 0, 1920, 1080, False]]


class FakeShell:
    """atticus-shell --bridge, answered from a little desktop."""

    def __init__(self, windows):
        self.windows = windows
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        verb = args[0]
        if verb == 'screens':
            return VISIBLE if args[1:] == ['visible'] else SCREENS
        if verb == 'windows':
            return self.windows
        if verb == 'focused':
            return next((w for w in self.windows if w['focused']), None)
        if verb == 'move_resize':
            w = next(w for w in self.windows if w['id'] == args[1])
            w['frame'] = [int(v) for v in args[2:6]]
            return {'ok': True}
        if verb in ('activate', 'set_fullscreen'):
            return {'ok': True}
        raise AssertionError(args)


def win(i, app, title, frame, focused=False):
    return {'id': str(i), 'app': app, 'pid': 10 + i, 'title': title, 'frame': frame, 'output': '', 'workspace': '0',
            'minimized': False, 'fullscreen': False, 'focused': focused}


class BrainPlaceTests(unittest.TestCase):
    def setUp(self):
        self.a = linux.linux_brain(self)
        import brain_linux
        self.shell = FakeShell([win(1, 'firefox', 'Docs — Firefox', [0, 32, 800, 600], True),
                                win(2, 'org.gnome.TextEditor', 'notes.txt', [100, 100, 400, 300]),
                                win(3, 'org.gnome.TextEditor', 'todo.txt', [120, 120, 400, 300])])
        p = mock.patch.object(brain_linux, '_bridge', self.shell)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(brain_linux, '_installed', lambda: P.installed(app_dirs(), cache=False))
        p.start()
        self.addCleanup(p.stop)

    def test_place_receipt_matches_PLACED(self):
        r = self.a.place('Firefox', 'left', 'monitor')
        self.assertRegex(r, self.a.PLACED)
        self.assertEqual(r, 'Firefox window -> left of HDMI-1')
        self.assertEqual(self.shell.windows[0]['frame'], [1280, 0, 960, 1080])

    def test_laptop_means_builtin_output_work_area(self):
        self.a.place('Firefox', 'full', 'laptop')
        self.assertEqual(self.shell.windows[0]['frame'], [0, 32, 1280, 768])

    def test_two_matching_windows_is_not_done(self):
        r = self.a.place('Text Editor', 'right', 'laptop')
        self.assertTrue(r.startswith('NOT DONE: more than one'), r)

    def test_title_picks_one(self):
        self.assertEqual(self.a.place('Text Editor', 'right', 'laptop', 'todo'), 'Text Editor window -> right of eDP-1')

    def test_no_window(self):
        self.assertEqual(self.a.place('Spotify', 'left', 'laptop'), 'no window found for "Spotify" ')

    def test_bad_where(self):
        self.assertEqual(self.a.place('Firefox', 'diagonal', 'laptop'), 'where must be left, right, full, fullscreen or center')

    def test_front_and_request_screen(self):
        with mock.patch.dict(os.environ, {'ATTICUS_FRONT': '', 'ATTICUS_SCREEN': ''}):
            os.environ.pop('ATTICUS_FRONT')
            os.environ.pop('ATTICUS_SCREEN')
            self.assertEqual(self.a.front(), ('Firefox', 'Docs — Firefox'))
            self.assertEqual(self.a.request_screen(), 'eDP-1')

    def test_app_ids_and_running(self):
        self.assertEqual(self.a.app_id('Text Editor'), 'org.gnome.TextEditor')
        self.assertTrue(self.a.app_running('org.gnome.TextEditor'))
        with mock.patch('subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', '')):
            self.assertFalse(self.a.app_running('com.spotify.Client'))

    def test_installed_apps_feed_app_named(self):
        self.a._APPS_CACHE = (0.0, set())
        self.assertEqual(self.a.app_named('the text editor app'), 'Text Editor')

    def test_quit_app_terms_and_waits(self):
        ran = []
        state = {'up': True}

        def run(args, **kw):
            ran.append(args)
            if args[0] == 'pkill':
                state['up'] = False
            return subprocess.CompletedProcess(args, 0 if state['up'] else 1, '', '')
        self.shell.windows = []
        with mock.patch('subprocess.run', side_effect=run), mock.patch('time.sleep'):
            self.assertTrue(self.a.quit_app('com.spotify.Client'))
        self.assertIn(['pkill', '-TERM', '-x', 'com.spotify.Client'], ran)

    def test_open_spotify_is_instant_no_ai(self):
        opened = []
        with mock.patch('subprocess.run', side_effect=lambda args, **kw: opened.append(args) or subprocess.CompletedProcess(args, 0, '', '')), \
                mock.patch('subprocess.Popen', side_effect=lambda args, **kw: opened.append(args) or mock.Mock(returncode=0, wait=lambda **k: 0)):
            r = self.a.show('Spotify')
        self.assertEqual(r, 'opened Spotify in front')
        self.assertIn(['gtk-launch', 'com.spotify.Client'], opened)

    def test_show_url_and_missing(self):
        opened = []
        with mock.patch('subprocess.run', side_effect=lambda args, **kw: opened.append(args) or subprocess.CompletedProcess(args, 0, '', '')):
            self.assertEqual(self.a.show('https://example.com'), 'opened https://example.com in front')
            self.assertEqual(self.a.show('Nope App'), 'no app or file called "Nope App"')
        self.assertIn(['xdg-open', 'https://example.com'], opened)

    def test_notify(self):
        with mock.patch('subprocess.run') as run:
            self.a.notify('Codex is done')
        self.assertEqual(run.call_args[0][0], ['notify-send', '--app-name=Atticus', 'Atticus', 'Codex is done'])

    def test_learned_quit_replays_through_quit_app(self):
        self.assertEqual(self.a.replay_quit(['osascript', '-e', 'tell application id "firefox" to quit']), 'firefox')


if __name__ == '__main__':
    unittest.main()
