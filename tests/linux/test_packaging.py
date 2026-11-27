import configparser
import json
import sys
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

    def test_launcher_prefers_venv_python(self):
        venv = Path(self.root) / 'venv/bin'
        venv.mkdir(parents=True)
        (venv / 'python3').write_text('#!/bin/sh\necho venv-python "$@"\n')
        (venv / 'python3').chmod(0o755)
        self.assertEqual(build({'ATTICUS_VENV': str(venv.parent)}, self.root).returncode, 0)
        out = subprocess.run(['sh', f'{self.root}/share/atticus/atticus-shell', '--x'], capture_output=True, text=True, timeout=20)
        self.assertEqual(out.stdout.strip(), 'venv-python -m atticus_shell --x')

    @unittest.skipUnless(shutil.which('openssl'), 'needs openssl')
    def test_browser_extension_and_native_hosts_installed(self):
        home = Path(self.root) / 'home'
        r = build({'ATTICUS_BROWSER_HOME': str(home), 'ATTICUS_CONFIG': str(home / '.config/atticus')}, self.root)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        ext = Path(self.root) / 'share/atticus/browser-extension'
        inject = (ext / 'web-inject.js').read_text()
        self.assertTrue(inject.startswith('globalThis.__atticusWeb = ('))
        manifest = json.loads((ext / 'manifest.json').read_text())
        self.assertIn('key', manifest)
        import base64
        sys.path.insert(0, str(ROOT / 'linux'))
        from atticus_shell.web_host import extension_id
        ident = extension_id(base64.b64decode(manifest['key']))
        chrome = json.loads((home / '.config/google-chrome/NativeMessagingHosts/app.atticus.web.json').read_text())
        self.assertEqual(chrome['allowed_origins'], [f'chrome-extension://{ident}/'])
        self.assertEqual(chrome['type'], 'stdio')
        self.assertTrue(os.access(chrome['path'], os.X_OK))
        firefox = json.loads((home / '.mozilla/native-messaging-hosts/app.atticus.web.json').read_text())
        self.assertEqual(firefox['allowed_extensions'], ['atticus@atticus.app'])
        again = build({'ATTICUS_BROWSER_HOME': str(home), 'ATTICUS_CONFIG': str(home / '.config/atticus')}, self.root)
        self.assertEqual(json.loads((Path(self.root) / 'share/atticus/browser-extension/manifest.json').read_text())['key'], manifest['key'])   # same id after a rebuild

    def fake_python(self, folder, gtk):
        """A python3 that behaves like the real one, except for the GTK import check: it passes (gtk=True) or fails like a
        mise/pyenv/Homebrew python. A passing one also fakes `-m venv`, so no network is needed."""
        folder = Path(self.root) / folder
        folder.mkdir(parents=True, exist_ok=True)
        check = 'exit 0' if gtk else 'echo "ModuleNotFoundError: No module named \'gi\'" >&2; exit 1'
        venv = ('if [ "$1 $2" = "-m venv" ]; then mkdir -p "$4/bin"; printf \'#!/bin/sh\\nexit 0\\n\' > "$4/bin/python3"; '
                f'chmod +x "$4/bin/python3"; echo "{folder}" > "$4/made-by"; exit 0; fi') if gtk else ''
        (folder / 'python3').write_text(f'#!/bin/sh\ncase "$2" in *require_version*) {check};; esac\n{venv}\nexec "{sys.executable}" "$@"\n')
        (folder / 'python3').chmod(0o755)
        return folder / 'python3'

    def build_with_pythons(self, path_gtk, system_gtk, **env):
        shadow = self.fake_python('shadow', path_gtk)
        system = self.fake_python('system', system_gtk)
        env = {'PATH': f'{shadow.parent}:{os.environ["PATH"]}', 'ATTICUS_SYSTEM_PYTHON': str(system), 'ATTICUS_SKIP_DEPS': '',
               'ATTICUS_VENV': f'{self.root}/venv', **env}
        return build(env, self.root), shadow, system

    def test_another_python_on_path_does_not_hide_the_system_gtk(self):
        r, _, system = self.build_with_pythons(path_gtk=False, system_gtk=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(Path(self.root, 'venv/made-by').read_text().strip(), str(system.parent))   # the venv sits on the system python

    def test_launcher_names_the_chosen_python_not_whatever_is_on_path(self):
        r, _, system = self.build_with_pythons(path_gtk=False, system_gtk=True, ATTICUS_SKIP_DEPS='1', ATTICUS_VENV=f'{self.root}/none')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f'exec "{system}" -m atticus_shell', Path(self.root, 'share/atticus/atticus-shell').read_text())

    def test_path_python_used_when_there_is_no_system_one(self):
        r, shadow, _ = self.build_with_pythons(path_gtk=True, system_gtk=False, ATTICUS_SYSTEM_PYTHON='/nonexistent/python3')   # NixOS
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(Path(self.root, 'venv/made-by').read_text().strip(), str(shadow.parent))

    def test_no_usable_python_shows_the_real_error(self):
        r, shadow, system = self.build_with_pythons(path_gtk=False, system_gtk=False)
        self.assertEqual(r.returncode, 1)
        out = r.stdout + r.stderr
        for expected in (str(system), str(shadow), "No module named 'gi'", 'ATTICUS_PYTHON'):
            self.assertIn(expected, out)
        self.assertFalse(Path(self.root, 'share/atticus').exists())

    def test_atticus_python_overrides_the_search(self):
        chosen = self.fake_python('chosen', True)
        r, _, _ = self.build_with_pythons(path_gtk=False, system_gtk=True, ATTICUS_PYTHON=str(chosen))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(Path(self.root, 'venv/made-by').read_text().strip(), str(chosen.parent))

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
