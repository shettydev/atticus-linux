"""uninstall-linux.sh against a throwaway HOME. System tools are stubs on PATH that only log, so nothing real is touched."""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests import linux  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'uninstall-linux.sh'
RULE = Path('/etc/udev/rules.d/60-atticus-uinput.rules')
APP_PATHS = ('.local/share/atticus', '.local/share/atticus-venv', '.local/share/gnome-shell/extensions/atticus@atticus.app',
             '.local/share/applications/atticus.desktop', '.cache/atticus', '.config/systemd/user/atticus.service',
             '.config/systemd/user/atticus-routine-abcdef123456.timer', '.config/systemd/user/atticus-routine-abcdef123456.service',
             '.config/google-chrome/NativeMessagingHosts/app.atticus.web.json',
             '.config/BraveSoftware/Brave-Browser/NativeMessagingHosts/app.atticus.web.json',
             '.mozilla/native-messaging-hosts/app.atticus.web.json')
KEPT = ('.config/other-app/keep.json', '.config/google-chrome/NativeMessagingHosts/com.other.host.json',
        '.local/share/applications/other.desktop', '.config/systemd/user/other.service')


class UninstallTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name, 'home')
        self.calls = Path(tmp.name, 'calls.log')
        stubs = Path(tmp.name, 'stubs')
        stubs.mkdir()
        for name in ('systemctl', 'sudo', 'udevadm', 'gnome-extensions', 'gsettings', 'pkill'):
            stub = stubs / name
            stub.write_text(f'#!/bin/sh\necho "{name} $*" >> "{self.calls}"\n')
            stub.chmod(0o755)
        self.env = {'HOME': str(self.home), 'PATH': f'{stubs}:{os.environ["PATH"]}', 'ATTICUS_ALLOW_NON_LINUX': '1',
                    'GIT_CONFIG_NOSYSTEM': '1', 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.com',
                    'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.com'}
        for rel in APP_PATHS + KEPT + ('.config/atticus/settings.json',):
            p = self.home / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if rel in ('.local/share/atticus', '.local/share/atticus-venv', '.local/share/gnome-shell/extensions/atticus@atticus.app', '.cache/atticus'):
                p.mkdir(exist_ok=True)
                (p / 'file').write_text('x')
            else:
                p.write_text('x')

    def git(self, *args, cwd):
        subprocess.run(['git', *args], cwd=cwd, env=self.env, check=True, capture_output=True)

    def clone_source(self):
        """~/.atticus as the installer leaves it: a clean clone of a remote."""
        upstream = self.home.parent / 'atticus-linux.git'
        seed = self.home.parent / 'seed'
        seed.mkdir()
        self.git('init', '-q', '-b', 'main', cwd=seed)
        (seed / 'atticus.py').write_text('x\n')
        self.git('add', '.', cwd=seed)
        self.git('commit', '-qm', 'seed', cwd=seed)
        self.git('clone', '-q', '--bare', str(seed), str(upstream), cwd=seed)
        self.git('clone', '-q', str(upstream), str(self.home / '.atticus'), cwd=seed)
        return self.home / '.atticus'

    def run_script(self, *args, **kw):
        return subprocess.run(['bash', str(SCRIPT), *args], env=self.env, capture_output=True, text=True, timeout=60,
                              stdin=subprocess.DEVNULL, start_new_session=True, **kw)   # new session: no terminal to ask

    def log(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def present(self, rels):
        return [r for r in rels if (self.home / r).exists()]

    def test_removes_the_app_and_keeps_data_and_source(self):
        src = self.clone_source()
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.present(APP_PATHS), [])
        self.assertEqual(self.present(KEPT), list(KEPT))
        self.assertTrue((self.home / '.config/atticus/settings.json').exists())
        self.assertTrue(src.exists())
        self.assertIn('--purge', r.stdout)

    def test_stops_services_routines_and_extension(self):
        self.run_script()
        log = self.log()
        self.assertIn('systemctl --user disable --now atticus.service', log)
        self.assertIn('systemctl --user disable --now atticus-routine-abcdef123456.timer', log)
        self.assertIn('systemctl --user daemon-reload', log)
        self.assertIn('gnome-extensions disable atticus@atticus.app', log)
        self.assertLess(log.index('systemctl --user disable --now atticus.service'), log.index('systemctl --user daemon-reload'))

    def test_kill_patterns_match_only_atticus_processes(self):
        self.run_script()
        patterns = [re.search(r'^pkill -u \d+ -f (.*)$', line).group(1) for line in self.log() if line.startswith('pkill ')]
        self.assertEqual(len(patterns), 3, self.log())
        ours = ['/usr/bin/python3 -m atticus_shell', 'python3 -m atticus_shell.web_host chrome-extension://x/',
                'python3 -c from atticus_shell.input import serve_clipboard_stdin; serve_clipboard_stdin()',
                'python3 /home/u/.local/share/atticus/atticus.py --watching x']
        theirs = ['vim linux/atticus_shell/app.py', 'vim /home/u/.local/share/atticus/atticus.py', 'code /home/u/.atticus', 'python3 -m unittest tests.linux.test_pen',
                  'tail -f /home/u/.config/atticus/app.err', 'bash ./uninstall-linux.sh']
        for cmd in ours:
            self.assertTrue(any(re.search(p, cmd) for p in patterns), cmd)
        for cmd in theirs:
            self.assertFalse(any(re.search(p, cmd) for p in patterns), cmd)

    def test_second_run_is_harmless(self):
        self.assertEqual(self.run_script().returncode, 0)
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_purge_without_a_terminal_changes_nothing(self):
        r = self.run_script('--purge')
        self.assertEqual(r.returncode, 1)
        self.assertIn('--yes', r.stdout + r.stderr)
        self.assertEqual(self.present(APP_PATHS), list(APP_PATHS))
        self.assertEqual(self.log(), [])

    def test_purge_removes_data_and_clean_source(self):
        src = self.clone_source()
        r = self.run_script('--purge', '--yes')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((self.home / '.config/atticus').exists())
        self.assertFalse(src.exists())
        self.assertEqual(self.present(KEPT), list(KEPT))

    def purge_keeps_source(self, src, why):
        r = self.run_script('--purge', '--yes')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(src.exists(), r.stdout)
        self.assertIn(why, r.stdout)

    def test_purge_keeps_source_with_uncommitted_changes(self):
        src = self.clone_source()
        (src / 'mine.txt').write_text('work in progress')
        self.purge_keeps_source(src, 'uncommitted changes')

    def test_purge_keeps_source_with_unpushed_commits(self):
        src = self.clone_source()
        (src / 'atticus.py').write_text('y\n')
        self.git('commit', '-qam', 'local only', cwd=src)
        self.purge_keeps_source(src, 'commits that were never pushed')

    def test_purge_keeps_source_with_a_stash(self):
        src = self.clone_source()
        (src / 'atticus.py').write_text('stashed\n')
        self.git('stash', cwd=src)
        self.purge_keeps_source(src, 'stashed changes')

    def test_purge_keeps_a_folder_that_is_not_a_checkout(self):
        src = self.home / '.atticus'
        src.mkdir()
        (src / 'notes.txt').write_text('mine')
        self.purge_keeps_source(src, 'not a git checkout')

    def test_purge_keeps_a_linked_source(self):
        real = self.clone_source()
        moved = self.home / 'code'
        real.rename(moved)
        (self.home / '.atticus').symlink_to(moved)
        self.purge_keeps_source(moved, 'is a link')

    def test_unknown_option_changes_nothing(self):
        r = self.run_script('--force')
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.present(APP_PATHS), list(APP_PATHS))
        self.assertEqual(self.log(), [])

    def test_help_changes_nothing(self):
        r = self.run_script('--help')
        self.assertEqual(r.returncode, 0)
        self.assertIn('--purge', r.stdout)
        self.assertEqual(self.log(), [])

    def test_refuses_macos(self):
        if os.uname().sysname == 'Linux':
            self.skipTest('host is Linux')
        self.env.pop('ATTICUS_ALLOW_NON_LINUX')
        r = self.run_script()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.present(APP_PATHS), list(APP_PATHS))

    @unittest.skipUnless(os.uname().sysname == 'Linux' and os.access('/etc/udev/rules.d', os.W_OK) and not RULE.exists(),
                         'needs a throwaway Linux with a writable /etc/udev/rules.d (the test image)')
    def test_removes_the_udev_rule_with_sudo(self):
        RULE.write_text('x')
        self.addCleanup(lambda: RULE.unlink(missing_ok=True))
        self.run_script()
        self.assertIn(f'sudo rm -f {RULE}', self.log())


if __name__ == '__main__':
    unittest.main()
