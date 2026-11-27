import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import linux
from tests.linux.test_brain_apps import app_dirs
from atticus_shell import apps as P


class LinuxOpenTests(unittest.TestCase):
    def setUp(self):
        self.a = linux.linux_brain(self)
        import brain_linux
        p = mock.patch.object(brain_linux, '_installed', lambda: P.installed(app_dirs(), cache=False))
        p.start()
        self.addCleanup(p.stop)
        self.tmp = tempfile.mkdtemp()

    def test_xdg_open_of_a_desktop_file_launches_a_program(self):
        launcher = Path(self.tmp, 'evil.desktop')
        launcher.write_text('[Desktop Entry]\nExec=rm -rf ~\n')
        self.assertTrue(self.a.launches_program(f'xdg-open {launcher}'))
        self.assertTrue(self.a.launches_program(f'gio open {launcher}'))
        self.assertTrue(self.a.launches_program(f'gio launch {launcher}'))

    def test_scripts_and_missing_paths_count(self):
        script = Path(self.tmp, 'run.sh')
        script.write_text('echo hi\n')
        self.assertTrue(self.a.launches_program(f'xdg-open {script}'))
        self.assertTrue(self.a.launches_program(f'xdg-open {self.tmp}/not-made-yet.AppImage'))

    def test_documents_links_and_installed_apps_do_not(self):
        doc = Path(self.tmp, 'report.pdf')
        doc.write_text('%PDF')
        self.assertFalse(self.a.launches_program(f'xdg-open {doc}'))
        self.assertFalse(self.a.launches_program('xdg-open https://example.com'))
        self.assertFalse(self.a.launches_program('gtk-launch firefox'))

    def test_open_command(self):
        self.assertEqual(self.a.open_command('Spotify', app=True), ['gtk-launch', 'com.spotify.Client'])
        self.assertEqual(self.a.open_command('https://example.com'), ['xdg-open', 'https://example.com'])

    def test_fast_opens_a_site_with_xdg_open(self):
        ran = []
        with mock.patch('subprocess.run', side_effect=lambda args, **kw: ran.append(args) or subprocess.CompletedProcess(args, 0, '', '')), \
                mock.patch.object(self.a, 'free_focus'):
            reply = self.a.fast('open example.com')
        self.assertEqual(reply, 'Opened example.com.')
        self.assertIn(['xdg-open', 'https://example.com'], ran)
        self.assertEqual(self.a.fast.cmds, ['xdg-open https://example.com'])


class MacOpenUnchangedTests(unittest.TestCase):
    def test_mac_open_command(self):
        import atticus
        self.assertEqual(atticus.open_command('https://x.org'), ['open', 'https://x.org'])
        self.assertEqual(atticus.open_command('Notes', app=True), ['open', '-a', 'Notes'])
        self.assertEqual(atticus.open_command('Notes', app=True, background=True), ['open', '-g', '-a', 'Notes'])


if __name__ == '__main__':
    unittest.main()
