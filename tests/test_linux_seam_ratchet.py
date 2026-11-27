"""Every brain function that touches a macOS-only tool is either replaced on Linux or listed as portable, with a reason."""
import ast
import re
import unittest
from pathlib import Path

from tests import linux  # noqa: F401
import brain_linux

ROOT = Path(__file__).resolve().parent.parent
MAC = re.compile(r'osascript|tell application|System Events|mdfind|launchctl|LaunchAgents|pbpaste|pbcopy|qlmanage|sips|'
                 r'/usr/bin/open|\bopen -a\b|screencapture|defaults (read|write)|NSWorkspace|Library/')


def mac_call_sites():
    tree = ast.parse((ROOT / 'atticus.py').read_text())
    found = set()
    tops = [n for n in tree.body] + [m for c in tree.body if isinstance(c, ast.ClassDef) for m in c.body]
    for node in tops:   # nested helpers belong to (and are covered with) their enclosing function
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body = list(ast.walk(node))
            if any(isinstance(c, ast.Constant) and isinstance(c.value, str) and MAC.search(c.value) for c in body) or \
               any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == 'osa' for c in body):
                found.add(node.name)
    return found


class SeamRatchet(unittest.TestCase):
    def test_scan_finds_known_sites(self):
        self.assertTrue({'osa', 'notify', 'routine_install', 'browser_adapter'} <= mac_call_sites())

    def test_mac_call_sites_are_replaced_or_portable(self):
        covered = set(brain_linux.REPLACED) | set(brain_linux.PORTABLE) | set(brain_linux.VIA_OSA)
        self.assertEqual(sorted(mac_call_sites() - covered), [])

    def test_reasons_are_given(self):
        for table in (brain_linux.PORTABLE, brain_linux.VIA_OSA):
            for name, reason in table.items():
                self.assertGreater(len(reason), 8, name)


if __name__ == '__main__':
    unittest.main()
