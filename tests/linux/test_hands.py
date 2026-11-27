import io
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
from atticus_shell import hands as H
from atticus_shell.desktop.bridge import Output, Window
from tests.linux.fakes import FakeBridge


class FakeInjector:
    def __init__(self):
        self.calls = []

    def click(self, x, y, button='left', count=1):
        self.calls.append(('click', round(x), round(y), button, count))
        return True

    def key(self, combo, times=1):
        self.calls.append(('key', combo, times))
        return True

    def scroll(self, dx, dy):
        self.calls.append(('scroll', dx, dy))
        return True

    def drag(self, a, b):
        self.calls.append(('drag', a, b))
        return True


def acc(role, name='', value='', rect=(0, 0, 0, 0), states=('showing', 'sensitive'), kids=(), actions=(), on_action=None):
    a = A.FakeAcc(role, name, value, rect, set(states), list(kids))
    a.actions, a.on_action = list(actions), on_action
    return a


def mail_app(log):
    field = acc('entry', 'To', '', (110, 100, 300, 30), ('showing', 'editable', 'focusable', 'sensitive'))
    body = acc('text', 'Message', 'Hello Sam, see you at noon.', (110, 140, 600, 300), ('showing', 'editable', 'sensitive'))
    win = acc('frame', 'New message', rect=(100, 50, 800, 600), states=('active', 'showing'), kids=[
        acc('push button', 'Attach', rect=(120, 60, 60, 30), actions=['click'], on_action=lambda n: log.append(('attach', n))),
        acc('push button', 'Send', rect=(200, 60, 60, 30), actions=['click'], on_action=lambda n: log.append(('send', n))),
        acc('push button', 'Canvas', rect=(500, 500, 100, 100)),   # no action: mouse
        field, body,
        acc('menu bar', kids=[acc('menu', 'File', kids=[acc('menu item', 'Save Draft', actions=['click'], on_action=lambda n: log.append(('save', n)))])]),
    ])
    return A.FakeApp('Mail', 777, [win]), field, body


class Rig:
    def __init__(self, safe=True):
        self.log = []
        self.app, self.field, self.body = mail_app(self.log)
        self.inj = FakeInjector()
        self.bridge = FakeBridge([Output('eDP-1', 0, 0, 1280, 800)], [Window('0x5', 'mail', 777, 'New message', 100, 50, 800, 600, 'eDP-1', '0')])
        self.state = os.path.join(tempfile.mkdtemp(), 'hands.json')
        self.safe = safe

    def run(self, args, stdin='', env=None):
        out = io.StringIO()
        h = H.Hands(lambda: [self.app], self.bridge, self.inj, state=self.state, safe=self.safe, env=env or {}, sleep=lambda s: None)
        code = h.run(args, io.StringIO(stdin), out)
        return code, out.getvalue()


class ValidateTests(unittest.TestCase):
    def test_messages_like_swift(self):
        for args, msg in [([], 'Use see|click|scroll|scroll-to|press|drag|type|wait.'), (['see', 'a', 'b'], 'Use see [app].'),
                          (['scroll', 'sideways'], 'Use scroll'), (['press'], 'Use press keys [times].'), (['drag', 'a'], 'Use drag from to.'),
                          (['type', 'x'], 'Use type target text [submit] [append].'), (['wait', '99'], 'Use wait [seconds, max 30].'),
                          (['fly'], 'Unknown hands verb: fly'), (['click', '--x'], 'Hands accepts no flags.')]:
            with self.subTest(args):
                with self.assertRaises(H.Failure) as e:
                    H.validate(args)
                self.assertTrue(e.exception.message.startswith(msg), e.exception.message)


class SeeTests(unittest.TestCase):
    def test_see_prints_map_and_saves_state(self):
        r = Rig()
        code, out = r.run(['see', 'Mail'])
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith('App: Mail (pid 777). Window: New message'))
        self.assertIn('[1] Button "Attach"', out)
        self.assertIn('Visible text (data): ', out)
        self.assertEqual(json.loads(Path(r.state).read_text())['app'], 'Mail')

    def test_unknown_app(self):
        code, out = Rig().run(['see', 'Nope'])
        self.assertEqual(code, 1)
        self.assertIn('Nope', out)


class ClickTests(unittest.TestCase):
    def test_click_by_id_uses_accessibility_action(self):
        r = Rig()
        r.run(['see', 'Mail'])
        code, out = r.run(['click', '1'])
        self.assertEqual((code, r.log, r.inj.calls), (0, [('attach', 'click')], []))
        self.assertTrue(out.startswith('Clicked "Attach"'))

    def test_click_by_label(self):
        r = Rig()
        r.run(['see', 'Mail'])
        self.assertEqual(r.run(['click', 'attach'])[0], 0)
        self.assertEqual(r.log, [('attach', 'click')])

    def test_no_action_falls_back_to_the_mouse(self):
        r = Rig()
        r.run(['see', 'Mail'])
        code, out = r.run(['click', 'Canvas'])
        self.assertEqual(code, 0)
        self.assertEqual(r.inj.calls, [('click', 550, 550, 'left', 1)])

    def test_double_right_click_use_the_mouse(self):
        r = Rig()
        r.run(['see', 'Mail'])
        r.run(['click', 'Attach', 'double', 'right'])
        self.assertEqual(r.inj.calls, [('click', 150, 75, 'right', 2)])
        self.assertEqual(r.log, [])

    def test_risky_click_requires_authorize(self):
        r = Rig()
        r.run(['see', 'Mail'])
        code, out = r.run(['click', 'Send'])
        self.assertEqual(code, 1)
        self.assertIn('Use atticus.py --hands so the user can allow this action.', out)
        self.assertEqual(r.log, [])

    def test_session_asks_then_acts(self):
        r = Rig()
        r.run(['see', 'Mail'])
        code, out = r.run(['click', 'Send'], stdin='y\n', env={'ATTICUS_HANDS_SESSION': '1'})
        check = json.loads(out.splitlines()[0])
        self.assertTrue(check['risky'])
        self.assertEqual(check['labels'], ['Send'])
        self.assertEqual((check['app'], check['pid']), ('Mail', 777))
        self.assertEqual((code, r.log), (0, [('send', 'click')]))

    def test_session_refused(self):
        r = Rig()
        r.run(['see', 'Mail'])
        code, out = r.run(['click', 'Send'], stdin='n\n', env={'ATTICUS_HANDS_SESSION': '1'})
        self.assertEqual(code, 1)
        self.assertIn('Action cancelled.', out)
        self.assertEqual(r.log, [])

    def test_receipt_must_match(self):
        r = Rig()
        r.run(['see', 'Mail'])
        code, out = r.run(['click', 'Send'], stdin=json.dumps({'labels': ['Other']}), env={'ATTICUS_HANDS_RECEIPT': '1'})
        self.assertIn('The screen changed while checking permission. Run see again.', out)

    def test_ids_from_a_stale_map_are_refused(self):
        r = Rig()
        r.run(['see', 'Mail'])
        r.app.windows[0].kids.insert(0, acc('push button', 'New thing', rect=(1, 1, 5, 5), actions=['click']))
        code, out = r.run(['click', '1'])
        self.assertEqual(code, 1)
        self.assertIn('The screen changed. Run see again.', out)

    def test_menu_path(self):
        r = Rig()
        code, out = r.run(['menu', 'Mail', 'File > Save Draft'])
        self.assertEqual((code, r.log), (0, [('save', 'click')]))
        self.assertTrue(out.startswith('Clicked "Save Draft"'))


class TypeTests(unittest.TestCase):
    def test_type_replaces_and_verifies(self):
        r = Rig()
        r.run(['see', 'Mail'])
        code, out = r.run(['type', 'To', 'sam@example.com'])
        self.assertEqual(code, 0)
        self.assertEqual(r.field.value, 'sam@example.com')
        self.assertTrue(out.startswith('verified: Typed into "To".'))

    def test_append(self):
        r = Rig()
        r.field.value = 'sam'
        r.run(['see', 'Mail'])
        r.run(['type', 'To', '@example.com', 'append'])
        self.assertEqual(r.field.value, 'sam@example.com')

    def test_submit_is_risky_and_presses_return(self):
        r = Rig()
        r.run(['see', 'Mail'])
        code, out = r.run(['type', 'To', 'hi', 'submit'], stdin='y\n', env={'ATTICUS_HANDS_SESSION': '1'})
        self.assertTrue(json.loads(out.splitlines()[0])['risky'])
        self.assertIn(('key', 'return', 1), r.inj.calls)

    def test_type_after_exact_anchor(self):
        r = Rig()
        r.run(['see', 'Mail'])
        code, out = r.run(['type-after', 'Hello Sam,', ' quick note:'])
        self.assertEqual(code, 0)
        self.assertEqual(r.body.value, 'Hello Sam, quick note: see you at noon.')
        self.assertTrue(out.startswith('verified:'))

    def test_type_after_missing_anchor(self):
        r = Rig()
        r.run(['see', 'Mail'])
        code, out = r.run(['type-after', 'Goodbye', 'x'])
        self.assertEqual(code, 1)
        self.assertIn('not_done', out)


class OtherVerbTests(unittest.TestCase):
    def test_press_goes_to_the_app(self):
        r = Rig()
        r.run(['see', 'Mail'])
        code, out = r.run(['press', 'cmd+b', '2'])
        self.assertEqual(code, 0)
        self.assertIn(('key', 'cmd+b', 2), r.inj.calls)
        self.assertIn(('activate', '0x5'), r.bridge.calls)
        self.assertTrue(out.startswith('Pressed "cmd+b" in Mail'))

    def test_scroll(self):
        r = Rig()
        r.run(['see', 'Mail'])
        code, out = r.run(['scroll', 'down', '2'])
        self.assertEqual(code, 0)
        self.assertEqual(r.inj.calls[-1][0], 'scroll')
        self.assertLess(r.inj.calls[-1][2], 0)

    def test_wait(self):
        r = Rig()
        r.run(['see', 'Mail'])
        code, out = r.run(['wait', '1'])
        self.assertEqual(code, 0)
        self.assertIn('App: Mail', out)


@unittest.skipUnless(linux.HAVE_GI and os.environ.get('DISPLAY') and os.environ.get('DBUS_SESSION_BUS_ADDRESS'), 'live AT-SPI: gui-tests.sh')
class HandsLive(unittest.TestCase):
    APP = r'''
import gi, sys; gi.require_version('Gtk', '3.0'); from gi.repository import Gtk
w = Gtk.Window(title='Hands fixture'); box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
b = Gtk.Button(label='Mark done'); b.connect('clicked', lambda *_: open(sys.argv[1], 'w').write('clicked'))
e = Gtk.Entry(); box.pack_start(b, False, False, 0); box.pack_start(e, False, False, 0)
w.add(box); w.show_all(); Gtk.main()
'''

    def test_click_and_type_in_a_real_gtk_app(self):
        A.ensure_a11y_on()
        flag = os.path.join(tempfile.mkdtemp(), 'flag')
        p = subprocess.Popen([sys.executable, '-c', self.APP, flag], env={**os.environ, 'NO_AT_BRIDGE': '0'})
        self.addCleanup(p.kill)
        state = os.path.join(tempfile.mkdtemp(), 'h.json')
        name = None
        end = time.time() + 15
        while time.time() < end and not name:
            name = next((a.name for a in A.atspi_apps() if a.pid == p.pid), None)
            time.sleep(0.2)
        self.assertTrue(name)

        def run(args):
            out = io.StringIO()
            code = H.Hands(A.atspi_apps, FakeBridge([], []), FakeInjector(), state=state, safe=True, env={}, sleep=time.sleep).run(args, io.StringIO(''), out)
            return code, out.getvalue()
        while time.time() < end and 'Mark done' not in run(['see', name])[1]:
            time.sleep(0.2)
        code, out = run(['click', 'Mark done'])
        self.assertEqual(code, 0, out)
        while time.time() < end and not os.path.exists(flag):
            time.sleep(0.1)
        self.assertEqual(Path(flag).read_text(), 'clicked')
        run(['see', name])
        code, out = run(['type', 'text field', 'hello world'])
        self.assertTrue(out.startswith('verified:'), out)


if __name__ == '__main__':
    unittest.main()
