import os
import shutil
import subprocess
import sys
import time
import unittest

from tests import linux
from atticus_shell import input as I
from atticus_shell.desktop.bridge import Output


class FakeDevice:
    def __init__(self):
        self.events = []

    def write(self, kind, code, value):
        self.events.append((kind, code, value))

    def syn(self):
        self.events.append('syn')


OUTS = [Output('eDP-1', 0, 0, 1280, 800, 1.0), Output('HDMI-1', 1280, 0, 1920, 1080, 1.5)]


class MappingTests(unittest.TestCase):
    def test_absolute_mapping_two_outputs_mixed_scale(self):
        self.assertEqual(I.to_abs(1280, 540, OUTS), (round(32767 * 1280 / 3200), round(32767 * 540 / 1080)))
        self.assertEqual(I.to_abs(0, 0, OUTS), (0, 0))
        self.assertEqual(I.to_abs(3199, 1079, OUTS), (round(32767 * 3199 / 3200), round(32767 * 1079 / 1080)))

    def test_key_combos(self):
        E = I.KEYS
        self.assertEqual(I.parse_combo('cmd+t'), [E['ctrl'], E['t']])          # the brain's macOS habit maps to Ctrl
        self.assertEqual(I.parse_combo('ctrl+shift+v'), [E['ctrl'], E['shift'], E['v']])
        self.assertEqual(I.parse_combo('Return'), [E['enter']])
        self.assertEqual(I.parse_combo('⌘⇧['), [E['ctrl'], E['shift'], E['[']])
        with self.assertRaises(ValueError):
            I.parse_combo('ctrl+nope')

    def test_text_to_keys(self):
        E = I.KEYS
        self.assertEqual(I.text_keys('Hi!'), [([E['shift']], E['h']), ([], E['i']), ([E['shift']], E['1'])])
        self.assertIsNone(I.text_keys('café'))   # beyond a US layout: paste instead

    def test_terminal_paste_combo(self):
        self.assertEqual(I.paste_combo('org.gnome.Terminal'), 'ctrl+shift+v')
        self.assertEqual(I.paste_combo('kitty'), 'ctrl+shift+v')
        self.assertEqual(I.paste_combo('firefox'), 'ctrl+v')


class UinputTests(unittest.TestCase):
    def setUp(self):
        self.dev = FakeDevice()
        self.where = [(5.0, 5.0)]
        self.inj = I.UinputInjector(lambda: OUTS, pointer=lambda: self.where[0], device=self.dev, sleep=lambda s: None)

    def abs_moves(self):
        xs = [v for k, c, v in [e for e in self.dev.events if e != 'syn'] if k == I.EV_ABS and c == I.ABS_X]
        return xs

    def test_click_presses_and_releases(self):
        self.assertTrue(self.inj.click(100, 200))
        keys = [(c, v) for k, c, v in [e for e in self.dev.events if e != 'syn'] if k == I.EV_KEY]
        self.assertEqual(keys, [(I.BTN_LEFT, 1), (I.BTN_LEFT, 0)])

    def test_pointer_restored_after_click(self):
        self.inj.click(100, 200)
        self.assertEqual(self.abs_moves(), [I.to_abs(100, 200, OUTS)[0], I.to_abs(5, 5, OUTS)[0]])

    def test_double_right_click(self):
        self.inj.click(10, 10, button='right', count=2)
        keys = [(c, v) for k, c, v in [e for e in self.dev.events if e != 'syn'] if k == I.EV_KEY]
        self.assertEqual(keys, [(I.BTN_RIGHT, 1), (I.BTN_RIGHT, 0)] * 2)

    def test_key_combo_order(self):
        self.inj.key('ctrl+shift+v')
        keys = [(c, v) for k, c, v in [e for e in self.dev.events if e != 'syn'] if k == I.EV_KEY]
        E = I.KEYS
        self.assertEqual(keys, [(E['ctrl'], 1), (E['shift'], 1), (E['v'], 1), (E['v'], 0), (E['shift'], 0), (E['ctrl'], 0)])

    def test_type_text_refuses_unicode(self):
        self.assertFalse(self.inj.type_text('naïve'))
        self.assertEqual(self.dev.events, [])

    def test_scroll(self):
        self.inj.scroll(0, -3)
        rel = [(c, v) for k, c, v in [e for e in self.dev.events if e != 'syn'] if k == I.EV_REL]
        self.assertEqual(rel, [(I.REL_WHEEL, -1)] * 3)


class FakeClipboard:
    def __init__(self, contents):
        self.contents = dict(contents)
        self.restored = None

    def read_all(self):
        return dict(self.contents)

    def put_text(self, text):
        self.contents = {'text/plain;charset=utf-8': text.encode()}

    def restore(self, saved):
        self.restored = dict(saved)
        self.contents = dict(saved)


class BorrowTests(unittest.TestCase):
    def test_borrow_restores_all_mime_types(self):
        cb = FakeClipboard({'text/plain': b'mine', 'image/png': b'\x89PNG...', 'text/html': b'<b>mine</b>'})
        with I.ClipboardBorrow(cb) as borrow:
            borrow.put('dictated words')
            self.assertEqual(cb.contents, {'text/plain;charset=utf-8': b'dictated words'})
        self.assertEqual(cb.restored, {'text/plain': b'mine', 'image/png': b'\x89PNG...', 'text/html': b'<b>mine</b>'})

    def test_empty_clipboard_restores_empty(self):
        cb = FakeClipboard({})
        with I.ClipboardBorrow(cb) as borrow:
            borrow.put('x')
        self.assertEqual(cb.restored, {})

    def test_restore_even_if_paste_raises(self):
        cb = FakeClipboard({'text/plain': b'mine'})
        with self.assertRaises(RuntimeError):
            with I.ClipboardBorrow(cb) as borrow:
                borrow.put('x')
                raise RuntimeError('paste failed')
        self.assertEqual(cb.restored, {'text/plain': b'mine'})

    def test_best_single_type_for_wl_copy(self):
        self.assertEqual(I.best_type({'image/png': b'', 'text/plain;charset=utf-8': b'', 'text/html': b''}), 'image/png')
        self.assertEqual(I.best_type({'text/html': b'', 'UTF8_STRING': b''}), 'UTF8_STRING')


@unittest.skipUnless(os.environ.get('ATTICUS_X11_LIVE') and shutil.which('xclip'), 'live X11: x11-live.sh')
class X11ClipboardLive(unittest.TestCase):
    def test_owner_serves_every_type(self):
        cb = I.X11Clipboard()
        cb.restore({'text/plain': b'plain words', 'text/html': b'<i>rich</i>'})
        time.sleep(0.5)
        read = lambda t: subprocess.run(['xclip', '-o', '-selection', 'clipboard', '-t', t], capture_output=True, timeout=5).stdout
        self.assertEqual(read('text/plain'), b'plain words')
        self.assertEqual(read('text/html'), b'<i>rich</i>')
        self.assertEqual(cb.read_all().get('text/html'), b'<i>rich</i>')


if __name__ == '__main__':
    unittest.main()
