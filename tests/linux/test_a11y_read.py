import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests import linux
from atticus_shell import a11y as A
from atticus_shell.desktop.bridge import Output, Window
from tests.linux.fakes import FakeBridge

ROOT = Path(__file__).resolve().parents[2]


def acc(role, name='', value='', rect=(0, 0, 0, 0), states=(), kids=(), text=None):
    return A.FakeAcc(role, name, value, rect, set(states), list(kids), text)


def editor_app():
    window = acc('frame', 'notes.txt — Text Editor', rect=(100, 50, 800, 600), states={'active', 'showing'}, kids=[
        acc('tool bar', kids=[acc('push button', 'Save', rect=(110, 60, 60, 30), states={'showing', 'sensitive'}),
                              acc('toggle button', 'Bold', rect=(180, 60, 30, 30), states={'showing', 'sensitive', 'checked'})]),
        acc('text', '', 'Card 4111 1111 1111 1111 and hello', rect=(110, 100, 780, 500),
            states={'showing', 'editable', 'focused', 'focusable'}),
        acc('label', 'Saved 2 minutes ago', rect=(110, 610, 200, 20), states={'showing'}),
        acc('push button', 'Hidden', rect=(0, 0, 10, 10), states=set()),   # not showing: skipped
    ])
    return A.FakeApp('Text Editor', 4242, [window, acc('frame', 'Preferences', rect=(5, 5, 300, 200), states={'showing'})])


class MapTests(unittest.TestCase):
    def test_map_fields_match_swift(self):
        m = A.capture_app(editor_app())
        self.assertEqual(set(m.as_dict()), {'pid', 'app', 'window', 'windowFrame', 'nodes', 'texts', 'page', 'focused', 'scroll',
                                            'windowID', 'focusIdentity'})
        self.assertEqual((m.pid, m.app, m.window, m.windowFrame), (4242, 'Text Editor', 'notes.txt — Text Editor', [100, 50, 800, 600]))

    def test_roles_use_mac_names_and_ids_count_from_1(self):
        nodes = A.capture_app(editor_app()).nodes
        self.assertEqual([(n.id, n.role, n.label) for n in nodes],
                         [(1, 'AXButton', 'Save'), (2, 'AXCheckBox', 'Bold'), (3, 'AXTextArea', '')])
        self.assertEqual(nodes[1].value, '1')   # checked

    def test_node_line_like_swift(self):
        n = A.capture_app(editor_app()).nodes[0]
        self.assertEqual(n.line, '[1] Button "Save" (at 140,75)')

    def test_texts_and_focus(self):
        m = A.capture_app(editor_app())
        self.assertIn('Saved 2 minutes ago', m.texts)
        self.assertEqual(m.focused, '[3] TextArea ""')
        self.assertTrue(m.focusIdentity)

    def test_values_masked(self):
        n = A.capture_app(editor_app()).nodes[2]
        self.assertEqual(n.value, 'Card [SECRET]and hello')   # Hands.mask's pattern takes the trailing space too (parity)

    def test_budget_caps_huge_trees(self):
        big = A.FakeApp('Big', 1, [acc('frame', 'w', rect=(0, 0, 100, 100), states={'active', 'showing'},
                                       kids=[acc('push button', f'b{i}', rect=(0, 0, 5, 5), states={'showing'}) for i in range(5000)])])
        self.assertLessEqual(len(A.capture_app(big).nodes), A.BUDGET)

    def test_single_line_text_is_a_text_field(self):
        app = A.FakeApp('Form', 1, [acc('frame', 'Form', rect=(0, 0, 9, 9), states={'active', 'showing'},
                                        kids=[acc('text', 'Name', 'Sam', rect=(1, 1, 5, 5), states={'showing', 'editable', 'single line'})])])
        self.assertEqual(A.capture_app(app).nodes[0].role, 'AXTextField')

    def test_password_fields_are_secret(self):
        app = A.FakeApp('Login', 1, [acc('frame', 'Sign in', rect=(0, 0, 9, 9), states={'active', 'showing'},
                                         kids=[acc('password text', 'Password', 'hunter2', rect=(1, 1, 5, 5), states={'showing', 'editable'})])])
        n = A.capture_app(app).nodes[0]
        self.assertTrue(n.secret)
        self.assertNotIn('hunter2', n.line)


class MaskTests(unittest.TestCase):
    def test_mask_like_swift(self):
        self.assertEqual(A.mask('card 4111 1111 1111 1111'), 'card [SECRET]')
        self.assertEqual(A.mask('ssn 123-45-6789'), 'ssn [SECRET]')
        self.assertEqual(A.mask('Your verification code is 4821'), 'Your verification code is [SECRET]')
        self.assertEqual(A.mask('Room 4821 at 9'), 'Room 4821 at 9')


class TextCommandTests(unittest.TestCase):
    def test_text_paging_from_accessibility(self):
        out = A.window_text([editor_app()], 'text editor', '')
        self.assertIn('Saved 2 minutes ago', out)
        self.assertIn('[SECRET]', out)

    def test_title_words_pick_the_window(self):
        out = A.window_text([editor_app()], 'Text Editor', 'preferences')
        self.assertNotIn('Saved 2 minutes ago', out)

    def test_no_text_falls_back_to_ocr(self):
        empty = A.FakeApp('Game', 9, [acc('frame', 'Game', rect=(0, 0, 400, 300), states={'active', 'showing'})])
        out = A.window_text([empty], 'game', '', ocr=lambda: 'Score 4111111111111111\nLevel 2')
        self.assertEqual(out, 'Score [SECRET]\nLevel 2')

    def test_ocr_text_from_tsv(self):
        tsv = 'level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n' \
              '5\t1\t1\t1\t1\t1\t0\t0\t1\t1\t96\tHello\n5\t1\t1\t1\t1\t2\t0\t0\t1\t1\t96\tthere\n5\t1\t1\t1\t2\t1\t0\t0\t1\t1\t91\tNext\n'
        self.assertEqual(A.ocr_lines(tsv), ['Hello there', 'Next'])


def desk():
    return FakeBridge([Output('eDP-1', 0, 0, 1280, 800, 1.0, True)],
                      [Window('0x1a', 'firefox', 10, 'Docs — Firefox', 0, 0, 1280, 800, 'eDP-1', '0', focused=True),
                       Window('0x1b', 'gnome-text-editor', 11, 'notes.txt', 100, 100, 400, 300, 'eDP-1', '0'),
                       Window('0x2', 'atticus', 99, 'Atticus box', 0, 0, 500, 200, 'eDP-1', '0')])


class TargetTests(unittest.TestCase):
    def test_user_app_excludes_atticus(self):
        b = FakeBridge(desk().outputs(), [Window('0x2', 'atticus', 99, 'Atticus box', 0, 0, 5, 5, 'eDP-1', '0', focused=True),
                                          Window('0x1a', 'firefox', 10, 'Docs', 0, 0, 5, 5, 'eDP-1', '0')])
        self.assertEqual(A.user_app(b), 'firefox')

    def test_window_target_json(self):
        t = A.window_target_for(desk(), 'gnome-text-editor', '')
        self.assertEqual(t['windowID'], '27')
        self.assertEqual(t['app'], 'gnome-text-editor')
        self.assertFalse(t['visible'])   # Firefox covers it completely

    def test_window_target_frame_must_match(self):
        self.assertIsNone(A.window_target_for(desk(), 'firefox', '', frame=[5, 5, 5, 5]))
        self.assertIsNotNone(A.window_target_for(desk(), 'firefox', '', frame=[0, 0, 1280, 800]))

    def test_target_app_by_name_or_handler(self):
        self.assertEqual(A.target_app(desk(), 'Firefox'), 'firefox')
        self.assertEqual(A.target_app(desk(), 'https://x.org', handler=lambda uri: 'org.mozilla.firefox'), 'org.mozilla.firefox')

    def test_system_ui_lists_dialogs_only(self):
        b = FakeBridge(desk().outputs(), desk().windows() + [Window('0x9', 'xdg-desktop-portal-gnome', 5, 'Share screen', 400, 300, 400, 200, 'eDP-1', '0')])
        ui = A.system_ui(b)
        self.assertEqual(list(ui.values()), ['xdg-desktop-portal-gnome 400×200 on eDP-1'])


@unittest.skipUnless(linux.HAVE_GI and os.environ.get('DISPLAY') and os.environ.get('DBUS_SESSION_BUS_ADDRESS'), 'live AT-SPI: gui-tests.sh')
class LiveAtspi(unittest.TestCase):
    APP = r'''
import gi; gi.require_version('Gtk', '3.0'); from gi.repository import Gtk
w = Gtk.Window(title='Atticus a11y fixture'); box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
box.pack_start(Gtk.Button(label='Save draft'), False, False, 0); e = Gtk.Entry(); e.set_text('typed words'); box.pack_start(e, False, False, 0)
box.pack_start(Gtk.Label(label='Status: ready'), False, False, 0); w.add(box); w.connect('destroy', Gtk.main_quit); w.show_all(); Gtk.main()
'''

    def test_reads_a_real_gtk_window(self):
        A.ensure_a11y_on()
        p = subprocess.Popen([sys.executable, '-c', self.APP], env={**os.environ, 'NO_AT_BRIDGE': '0'})
        self.addCleanup(p.kill)
        app = None
        end = time.time() + 15
        while time.time() < end and app is None:
            app = next((a for a in A.atspi_apps() if a.pid == p.pid), None)
            time.sleep(0.2)
        self.assertIsNotNone(app, 'fixture never showed up on the accessibility bus')
        m = None
        while time.time() < end:
            m = A.capture_app(app)
            if any(n.label == 'Save draft' for n in m.nodes):
                break
            time.sleep(0.2)
        self.assertEqual(m.window, 'Atticus a11y fixture')
        self.assertIn(('AXButton', 'Save draft'), [(n.role, n.label) for n in m.nodes])
        self.assertIn('typed words', [n.value for n in m.nodes])
        self.assertIn('Status: ready', m.texts)


if __name__ == '__main__':
    unittest.main()
