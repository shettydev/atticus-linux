"""Shared pieces of the Hyprland and Sway backends: running their IPC CLIs and grim for pictures."""
import json
import re
import subprocess

ADDR = re.compile(r'^0x[0-9a-fA-F]+$')


def run_cli(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, timeout=5, **kw)


class CliBackend:
    available = True
    unavailable_reason = ''
    covers_hidden = False   # grim pictures what is visible on screen

    def __init__(self, run=run_cli):
        self.run = run

    def _json(self, args):
        try:
            r = self.run(args)
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode:
            return None
        try:
            return json.loads(r.stdout)
        except ValueError:
            return None

    def screenshot_output(self, name, path):
        return self._grim(['-o', name, path])

    def screenshot_window(self, win_id, path):
        w = next((w for w in self.windows() if w.id == win_id), None)
        return bool(w) and self._grim(['-g', f'{w.x},{w.y} {w.width}x{w.height}', path])

    def _grim(self, args):
        try:
            return self.run(['grim', *args]).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def focused(self):
        return next((w for w in self.windows() if w.focused), None)
