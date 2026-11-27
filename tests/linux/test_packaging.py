import configparser
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests import linux  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / 'linux' / 'packaging'
GI = ('Gtk 3.0', 'WebKit2 4.1', 'GtkLayerShell 0.1', 'AyatanaAppIndicator3 0.1', 'Atspi 2.0', 'Gst 1.0', 'ECal 2.0', 'EBook 1.2')


def build(env_extra, root):
    env = {**os.environ, 'ATTICUS_PREFIX': f'{root}/share/atticus', 'ATTICUS_APPLICATIONS': f'{root}/applications',
           'ATTICUS_SYSTEMD_DIR': f'{root}/systemd', 'ATTICUS_NO_RESTART': '1', 'ATTICUS_SKIP_SYSTEM': '1',
           'ATTICUS_SKIP_DEPS': '1', 'ATTICUS_ALLOW_NON_LINUX': '1', **env_extra}
    return subprocess.run(['bash', str(ROOT / 'build-linux.sh')], capture_output=True, text=True, env=env, timeout=60)


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def test_service_restarts_only_on_failure(self):
        unit = configparser.ConfigParser(interpolation=None)
        unit.read(PKG / 'atticus.service')
        self.assertEqual(unit['Service']['Restart'], 'on-failure')
        self.assertEqual(unit['Install']['WantedBy'], 'graphical-session.target')

    def test_build_installs_layout_and_working_launcher(self):
        r = build({}, self.root)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        prefix = Path(self.root) / 'share/atticus'
        for rel in ('atticus.py', 'brain_linux.py', 'atticus_shell/cli.py', 'ui/ui.html', 'ui/web.js', 'atticus-shell'):
            self.assertTrue((prefix / rel).exists(), rel)
        out = subprocess.run(['sh', str(prefix / 'atticus-shell'), '--nope'], capture_output=True, text=True, timeout=20)
        self.assertEqual(out.returncode, 2, out.stderr)       # the launcher reaches cli.main
        self.assertIn('unknown option --nope', out.stderr)
        unit = (Path(self.root) / 'systemd/atticus.service').read_text()
        self.assertIn(f'ExecStart={prefix}/atticus-shell', unit)
        desktop = (Path(self.root) / 'applications/atticus.desktop').read_text()
        self.assertIn(f'Exec={prefix}/atticus-shell', desktop)
        self.assertIn('X-KDE-DBUS-Restricted-Interfaces=org.kde.KWin.ScreenShot2', desktop)

    def test_rebuild_replaces_previous_install(self):
        self.assertEqual(build({}, self.root).returncode, 0)
        stale = Path(self.root) / 'share/atticus/stale.txt'
        stale.write_text('old')
        self.assertEqual(build({}, self.root).returncode, 0)
        self.assertFalse(stale.exists())

    def test_build_rejects_bad_version(self):
        src = Path(self.root) / 'src'
        shutil.copytree(ROOT, src, ignore=shutil.ignore_patterns('.git', '.superpowers', 'docs', 'tests', '__pycache__'))
        (src / 'VERSION').write_text('2.x\n')
        env = {**os.environ, 'ATTICUS_PREFIX': f'{self.root}/p', 'ATTICUS_ALLOW_NON_LINUX': '1', 'ATTICUS_SKIP_DEPS': '1'}
        r = subprocess.run(['bash', str(src / 'build-linux.sh')], capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(r.returncode, 1)
        self.assertIn('Invalid VERSION', r.stdout + r.stderr)
        self.assertFalse(Path(f'{self.root}/p').exists())

    def test_build_refuses_macos_without_override(self):
        if os.uname().sysname == 'Linux':
            self.skipTest('host is Linux')
        r = build({'ATTICUS_ALLOW_NON_LINUX': ''}, self.root)
        self.assertEqual(r.returncode, 1)
        self.assertIn('Linux', r.stdout + r.stderr)

    def test_deps_cover_every_gi_namespace(self):
        for manager in ('apt', 'dnf', 'pacman', 'zypper'):
            r = subprocess.run(['bash', str(PKG / 'deps.sh'), '--print', manager], capture_output=True, text=True, timeout=10)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertGreater(len(r.stdout.split()), 15, manager)
        r = subprocess.run(['bash', str(PKG / 'deps.sh'), '--namespaces'], capture_output=True, text=True, timeout=10)
        self.assertEqual(sorted(r.stdout.strip().splitlines()), sorted(GI))

    @unittest.skipUnless(shutil.which('apt-get') and os.path.exists('/var/lib/apt/lists') and os.listdir('/var/lib/apt/lists'), 'needs apt with package lists')
    def test_apt_package_names_resolve(self):
        pkgs = subprocess.run(['bash', str(PKG / 'deps.sh'), '--print', 'apt'], capture_output=True, text=True).stdout.split()
        r = subprocess.run(['apt-get', 'install', '--simulate', '-qq', *pkgs], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])

    @linux.needs_gi
    def test_gi_namespaces_load(self):
        import gi
        for spec in GI:
            name, version = spec.split()
            gi.require_version(name, version)

    def test_udev_rule_grants_uaccess(self):
        rule = (PKG / '60-atticus-uinput.rules').read_text()
        self.assertRegex(rule, r'KERNEL=="uinput".*TAG\+="uaccess"')


if __name__ == '__main__':
    unittest.main()
