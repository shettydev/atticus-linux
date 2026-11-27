"""Regression tests for the Atticus Linux platform backend (`atticus_platform/linux.py`),
cross-platform `atticus.py` dispatch, Linux prompt budget, and browser/systemd/XDG integrations."""
import json
import os
import socket
import struct
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import atticus
from atticus_platform import linux as lp


class LinuxPlatformTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.orig_dir = atticus.DIR
        atticus.DIR = self.tmp.name
        self.addCleanup(setattr, atticus, 'DIR', self.orig_dir)

    def test_pure_python_image_dimensions_png_and_jpeg(self):
        png = Path(self.tmp.name) / 'sample.png'
        # Minimal valid PNG header with 1280x720 IHDR
        ihdr = b'\x89PNG\r\n\x1a\n' + struct.pack('>I', 13) + b'IHDR' + struct.pack('>II', 1280, 720) + b'\x08\x02\x00\x00\x00'
        png.write_bytes(ihdr)
        self.assertEqual(lp.image_dimensions(str(png)), (1280, 720))

        jpg = Path(self.tmp.name) / 'sample.jpg'
        # Minimal JPEG SOI + SOF0 with 640x480
        sof0 = b'\xff\xd8\xff\xc0' + struct.pack('>H', 11) + b'\x08' + struct.pack('>HH', 480, 640) + b'\x01\x01\x11\x00\xff\xd9'
        jpg.write_bytes(sof0)
        self.assertEqual(lp.image_dimensions(str(jpg)), (640, 480))

    def test_xdg_desktop_entry_parsing_and_app_lookup(self):
        app_dir = Path(self.tmp.name) / 'apps'
        app_dir.mkdir()
        (app_dir / 'firefox.desktop').write_text(
            '[Desktop Entry]\nType=Application\nName=Firefox Web Browser\nExec=/usr/bin/firefox %u\nStartupWMClass=firefox\n',
            encoding='utf-8',
        )
        (app_dir / 'hidden.desktop').write_text(
            '[Desktop Entry]\nType=Application\nName=Hidden Tool\nExec=/bin/true\nNoDisplay=true\n',
            encoding='utf-8',
        )
        with patch.object(lp, 'DESKTOP_DIRS', (str(app_dir),)), \
             patch.object(lp, '_DESKTOP_INDEX_CACHE', (0.0, {})), \
             patch.dict(os.environ, {'XDG_DATA_DIRS': ''}):
            apps = lp.installed_apps()
            self.assertIn('Firefox Web Browser', apps)
            self.assertNotIn('Hidden Tool', apps)
            self.assertEqual(lp.app_id('Firefox Web Browser'), 'firefox.desktop')
            self.assertEqual(lp.proc_of('Firefox Web Browser'), 'firefox')

    def test_systemd_user_routine_install_and_remove(self):
        row_time = {'id': 'abcdef123456', 'kind': 'time', 'at': '08:30', 'days': 'weekdays', 'ask': 'Check calendar'}
        row_int = {'id': '123456abcdef', 'kind': 'interval', 'every': 1800, 'ask': 'Check inbox'}
        with patch.dict(os.environ, {'ATTICUS_DIR': self.tmp.name}):
            lp.routine_install(self.tmp.name, atticus.ME, row_time)
            tmr = Path(lp.routine_timer_path(self.tmp.name, row_time['id']))
            svc = Path(lp.routine_service_path(self.tmp.name, row_time['id']))
            self.assertTrue(tmr.is_file() and svc.is_file())
            self.assertIn('OnCalendar=Mon..Fri *-*-* 08:30:00', tmr.read_text())
            self.assertIn('--routine-fire abcdef123456', svc.read_text())

            lp.routine_install(self.tmp.name, atticus.ME, row_int)
            tmr2 = Path(lp.routine_timer_path(self.tmp.name, row_int['id']))
            self.assertIn('OnUnitActiveSec=1800s', tmr2.read_text())

            lp.routine_remove(self.tmp.name, row_time['id'])
            self.assertFalse(tmr.exists())
            self.assertFalse(svc.exists())

    def test_browser_unix_socket_rpc_roundtrip(self):
        # macOS UNIX socket path limit is 104 bytes; use a short temp dir for the socket
        short_dir = tempfile.mkdtemp(prefix='att-sock-')
        self.addCleanup(lambda: subprocess.run(['rm', '-rf', short_dir]))
        sock_path = lp.browser_socket_path(short_dir)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(sock_path)
        srv.listen(2)

        def serve():
            for _ in range(2):
                conn, _ = srv.accept()
                req = json.loads(conn.recv(65536).decode('utf-8').strip())
                if req['cmd'] == 'ping':
                    conn.sendall(b'{"ok":true}\n')
                elif req['cmd'] == 'exec_js':
                    conn.sendall(json.dumps({'ok': True, 'result': 'Clicked Submit'}).encode('utf-8') + b'\n')
                conn.close()
            srv.close()

        t = threading.Thread(target=serve, daemon=True)
        t.start()
        self.assertEqual(lp.browser_available(short_dir, 'Chrome'), 'webext')
        self.assertEqual(lp.browser_exec_js(short_dir, 1, 2, '1+1'), 'Clicked Submit')
        t.join(timeout=2)

    def test_vcard_contacts_parsing(self):
        vcf = 'BEGIN:VCARD\nVERSION:3.0\nFN:Ada Lovelace\nORG:Analytical Engine\nEMAIL:ada@example.com\nTEL:+1-555-0100\nEND:VCARD\n'
        cards = lp._parse_vcards(vcf)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]['name'], 'Ada Lovelace')
        self.assertEqual(cards[0]['emails'], ['ada@example.com'])

    def test_linux_system_prompt_stays_within_hard_budget(self):
        installed = '/home/exampleuser/.local/share/atticus/atticus.py'
        with patch.object(atticus, 'IS_LINUX', True):
            for draw in (False, True):
                with patch.object(atticus, 'pref', side_effect=lambda k, d=None: {'draw': draw, 'pointer': True, 'askMe': True, 'learnMe': True, 'tips': True, 'learn': True, 'speak': True}.get(k, d)):
                    prompt = atticus.system_prompt(atticus.ASK_FIRST)
                    self.assertIn('Linux helper', prompt)
                    self.assertIn('gio trash', prompt)
                    self.assertNotIn('mdfind', prompt)
                    self.assertLessEqual(len(prompt.replace(atticus.ME, installed)), 10050 + 85 + len(atticus.READ_ALOUD))

    def test_hands_go_back_uses_alt_left_on_linux(self):
        with patch.object(atticus, 'IS_LINUX', True):
            self.assertEqual(atticus.hands_command('go back'), (['press', 'alt+left'], None))
        with patch.object(atticus, 'IS_LINUX', False):
            self.assertEqual(atticus.hands_command('go back'), (['press', 'cmd+['], None))

    def test_browser_extension_js_syntax_valid(self):
        node = subprocess.run(['which', 'node'], capture_output=True, text=True)
        if node.returncode != 0:
            self.skipTest('node not installed')
        root = Path(__file__).resolve().parents[1]
        bg = root / 'linux' / 'browser-extension' / 'background.js'
        gn = root / 'linux' / 'gnome-extension' / 'extension.js'
        r1 = subprocess.run(['node', '--check', str(bg)], capture_output=True, text=True)
        self.assertEqual(r1.returncode, 0, r1.stderr)
        r2 = subprocess.run(['node', '--check', '--input-type=module'], input=gn.read_text(), capture_output=True, text=True)
        self.assertEqual(r2.returncode, 0, r2.stderr)

    def test_linux_feedback_includes_os_and_session(self):
        with patch.object(atticus, 'IS_LINUX', True), \
             patch.dict(os.environ, {'XDG_SESSION_TYPE': 'wayland', 'XDG_CURRENT_DESKTOP': 'Hyprland'}):
            fb = atticus.feedback({'text': 'Overlay misaligned', 'version': '2.0.2', 'include': False})
            self.assertIn('Session: wayland (Hyprland)', fb['body'])
            self.assertNotIn('Mac model:', fb['body'])


if __name__ == '__main__':
    unittest.main()
