import os
import shlex
import unittest

from tests import linux
import atticus
import brain_linux


class SeamTests(unittest.TestCase):
    def test_legacy_suite_runs_mac_platform(self):
        self.assertEqual(atticus.PLATFORM, 'mac')   # tests/__init__ pins it, whatever the host

    def test_platform_follows_sys_platform_without_override(self):
        self.assertEqual(atticus.platform_name({}, 'linux'), 'linux')
        self.assertEqual(atticus.platform_name({}, 'darwin'), 'mac')
        self.assertEqual(atticus.platform_name({'ATTICUS_PLATFORM': 'linux'}, 'darwin'), 'linux')

    def test_linux_prompt_has_no_mac_tools(self):
        a = linux.linux_brain(self)
        for word in ('osascript', 'AppleScript', 'mdfind', 'defaults or', 'open -a', 'Mac helper', 'cmd+'):
            self.assertNotIn(word, a.SYSTEM, word)
            self.assertNotIn(word, a.WORKFLOW, word)
            self.assertNotIn(word, a.AGENT_SYSTEM, word)
        for word in ('localsearch', 'gsettings', 'gtk-launch atticus', 'ctrl+t'):
            self.assertIn(word, a.SYSTEM, word)

    def test_linux_prompt_budget(self):
        a = linux.linux_brain(self)
        installed = os.path.expanduser('~/.local/share/atticus/atticus.py')
        self.assertLessEqual(len(a.SYSTEM), 10000)
        full = a.system_prompt('x')   # reads the swapped SYSTEM through the module globals
        self.assertIn('localsearch', full)
        self.assertLessEqual(len(full.replace(a.ME, installed)), 10000)

    def test_mac_prompt_unchanged_after_restore(self):
        before = atticus.SYSTEM
        with self.subTest('installed'):
            linux.linux_brain(self)
            self.assertNotEqual(atticus.SYSTEM, before)
        self.doCleanups()
        self.assertEqual(atticus.SYSTEM, before)

    def test_deny_list_adds_linux_disk_tools(self):
        a = linux.linux_brain(self)
        self.assertTrue({'mkfs', 'fdisk', 'parted', 'wipefs', 'systemctl poweroff', 'systemctl reboot'} <= set(a.DENIED))
        self.assertIn('Bash(mkfs:*)', a.DENY)
        self.assertIn('Bash(rm:*)', a.DENY)          # the macOS list stays

    def test_safe_out_swaps_mac_tools(self):
        a = linux.linux_brain(self)
        self.assertNotIn('osascript *', a.SAFE_OUT)
        self.assertNotIn('pbpaste', a.SAFE_OUT)
        for c in ('xdg-open *', 'localsearch *', 'plocate *', 'wl-paste', 'curl *'):
            self.assertIn(c, a.SAFE_OUT)

    def test_mkfs_is_blocked_by_the_gate_rule(self):
        a = linux.linux_brain(self)
        self.assertTrue(a.UNSAFE.search('mkfs.ext4 /dev/sda1'))

    def test_desktop_files_count_as_programs(self):
        a = linux.linux_brain(self)
        self.assertIn('.desktop', a.RUNS)
        self.assertIn('.AppImage', a.RUNS)
        self.assertIn(os.path.expanduser('~/.local/share/applications/'), a.APP_DIRS)

    def test_app_defaults_to_installed_shell(self):
        env = {k: v for k, v in os.environ.items() if k != 'ATTICUS_APP'}
        self.assertEqual(brain_linux.default_app(env, '/h'), '/h/.local/share/atticus/atticus-shell')
        self.assertEqual(brain_linux.default_app({'ATTICUS_APP': '/x/shell'}, '/h'), '/x/shell')

    def test_needs_names_linux_switches(self):
        a = linux.linux_brain(self)
        self.assertEqual(a.NEEDS['accessibility'], 'Assistive technologies')
        self.assertEqual(a.NEEDS['automation'], 'the Atticus browser extension')

    def test_osa_is_inert_on_linux(self):
        a = linux.linux_brain(self)
        self.assertEqual(a.osa('display notification "x"'), '')   # never execs osascript

    def test_install_rebinds_every_replaced_name(self):
        a = linux.linux_brain(self)
        for name, fn in brain_linux.REPLACED.items():
            self.assertIs(getattr(a, name), fn, name)

    def test_stubs_name_their_task(self):
        for name, task in brain_linux.STUBS.items():
            with self.assertRaisesRegex(NotImplementedError, f'linux: {name} lands in {task}'):
                brain_linux.REPLACED[name]()


if __name__ == '__main__':
    unittest.main()
