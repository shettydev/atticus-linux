import io
import re
import sys
import types
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from tests import linux  # noqa: F401
from atticus_shell import cli

ROOT = Path(__file__).resolve().parents[2]


class ContractTests(unittest.TestCase):
    def test_every_brain_call_has_a_handler(self):
        flags = set(re.findall(r"\[APP, '(--[a-z-]+)'", (ROOT / 'atticus.py').read_text()))
        self.assertIn('--hands', flags)
        self.assertEqual(sorted(flags - set(cli.HANDLERS)), [])

    def test_linux_only_flags(self):
        for flag in ('--trigger', '--settings', '--bridge', '--selftest'):
            self.assertIn(flag, cli.HANDLERS)

    def test_unknown_flag_exits_2(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(cli.main(['--nope']), 2)
        self.assertIn('--nope', err.getvalue())

    def test_unfilled_handler_names_its_task(self):
        err = io.StringIO()
        with redirect_stderr(err), mock.patch.dict(cli.HANDLERS, {'--x': cli.pending('--x', 'Task 99')}):
            self.assertEqual(cli.main(['--x', 'a']), 1)
        self.assertEqual(err.getvalue().strip(), 'linux: --x lands in Task 99')

    def test_handler_gets_remaining_args(self):
        seen = []
        with mock.patch.dict(cli.HANDLERS, {'--x': lambda args: seen.append(args) or 0}):
            self.assertEqual(cli.main(['--x', 'a', 'b']), 0)
        self.assertEqual(seen, [['a', 'b']])

    def test_no_args_starts_gui(self):
        fake = types.ModuleType('atticus_shell.box')
        fake.run_app = mock.Mock(return_value=0)
        with mock.patch.dict(sys.modules, {'atticus_shell.box': fake}):
            self.assertEqual(cli.main([]), 0)
        fake.run_app.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
