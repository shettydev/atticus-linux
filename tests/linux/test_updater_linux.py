import subprocess
import unittest

from tests import linux  # noqa: F401
from atticus_shell import updater as U


class Git:
    def __init__(self, dirty='', local='aaa', remote='bbb', pull_ok=True):
        self.dirty, self.local, self.remote, self.pull_ok, self.calls = dirty, local, remote, pull_ok, []

    def __call__(self, args, **kw):
        self.calls.append(args)
        out = ''
        if args[-2:] == ['status', '--porcelain'] or 'status' in args:
            out = self.dirty
        elif 'rev-parse' in args and args[-1] == 'HEAD':
            out = self.local + '\n'
        elif 'rev-parse' in args:
            out = self.remote + '\n'
        elif 'pull' in args:
            return subprocess.CompletedProcess(args, 0 if self.pull_ok else 1, '', 'conflict')
        return subprocess.CompletedProcess(args, 0, out, '')


class UpdaterTests(unittest.TestCase):
    def test_new_version_found(self):
        self.assertEqual(U.Updater('/src', run=Git()).check(), 'available')

    def test_up_to_date(self):
        self.assertEqual(U.Updater('/src', run=Git(remote='aaa')).check(), 'current')

    def test_dirty_checkout_skips_update(self):
        u = U.Updater('/src', run=Git(dirty=' M atticus.py\n'))
        self.assertEqual(u.check(), 'local changes')
        self.assertFalse(u.apply())

    def test_apply_pulls_builds_and_restarts(self):
        git = Git()
        self.assertTrue(U.Updater('/src', run=git).apply())
        flat = [' '.join(c) for c in git.calls]
        self.assertTrue(any('pull -q --ff-only' in c for c in flat))
        self.assertTrue(any(c.endswith('build-linux.sh') for c in flat))
        self.assertIn('systemctl --user restart atticus.service', flat)

    def test_nix_install_shows_hint(self):
        self.assertEqual(U.Updater('/nix/store/abc-atticus', run=Git()).check(), 'nix')
        self.assertIn('nix profile upgrade', U.NIX_HINT)

    def test_feedback_fields_no_paths(self):
        info = U.system_info(os_release='PRETTY_NAME="Fedora Linux 41"\nID=fedora\n', product='ThinkPad X1', desktop='gnome wayland')
        self.assertEqual(info, {'os': 'Fedora Linux 41', 'model': 'ThinkPad X1', 'desktop': 'gnome wayland'})
        self.assertNotIn('/', ''.join(info.values()))


if __name__ == '__main__':
    unittest.main()
