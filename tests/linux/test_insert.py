import unittest

from tests import linux  # noqa: F401
from atticus_shell import a11y as A
from atticus_shell import insert as I
from atticus_shell import selection as S


def field(value='Hello world', caret=5, selection=None, readable=True, insert_works=True):
    f = A.FakeAcc('text', 'Message', value, (10, 10, 300, 40), {'showing', 'editable', 'focused', 'single line'})
    f.caret, f.selection, f.readable = caret, selection, readable
    if not insert_works:
        f.insert_text = lambda pos, text: True   # claims success, changes nothing
    return f


def app_with(f):
    return A.FakeApp('Notes', 42, [A.FakeAcc('frame', 'Note', rect=(0, 0, 400, 300), states={'active', 'showing'}, kids=[f])])


class SelectionTests(unittest.TestCase):
    def test_caret_in_editable_field(self):
        sel = S.capture([app_with(field())], enabled=True, allow_empty=True)
        self.assertEqual((sel.app, sel.pid, sel.text, sel.caret, sel.editable), ('Notes', 42, '', True, True))

    def test_highlighted_text(self):
        sel = S.capture([app_with(field(selection=(0, 5)))], enabled=True)
        self.assertEqual((sel.text, sel.caret), ('Hello', False))

    def test_nothing_selected_without_allow_empty(self):
        self.assertIsNone(S.capture([app_with(field())], enabled=True))

    def test_disabled(self):
        self.assertIsNone(S.capture([app_with(field(selection=(0, 5)))], enabled=False))

    def test_password_fields_never_read(self):
        f = A.FakeAcc('password text', 'Password', 'hunter2', (1, 1, 5, 5), {'showing', 'editable', 'focused'})
        f.caret, f.selection = 0, (0, 7)
        self.assertIsNone(S.capture([app_with(f)], enabled=True, allow_empty=True))

    def test_atspi_selection_preferred_over_primary(self):
        sel = S.capture([app_with(field(selection=(6, 11)))], enabled=True, primary=lambda: 'stale primary')
        self.assertEqual(sel.text, 'world')

    def test_primary_when_no_accessible_selection(self):
        sel = S.capture([], enabled=True, primary=lambda: 'from primary')
        self.assertEqual((sel.text, sel.editable), ('from primary', False))


class Clip:
    def __init__(self):
        self.puts, self.restored = [], False

    def read_all(self):
        return {'text/plain': b'mine'}

    def put_text(self, t):
        self.puts.append(t)

    def restore(self, saved):
        self.restored = True


class Keys:
    def __init__(self, f=None, text=''):
        self.calls, self.f, self.text = [], f, text

    def key(self, combo, times=1):
        self.calls.append(combo)
        if self.f is not None:   # the paste lands
            self.f.value = self.f.value[:self.f.caret] + self.text + self.f.value[self.f.caret:]
        return True


class InsertTests(unittest.TestCase):
    def run_insert(self, f, text=' there', same=True, clip=None, keys=None):
        sel = S.capture([app_with(f)], enabled=True, allow_empty=True)
        identity = sel.focus_identity if same else 'other'
        return I.insert(text, sel, expected_identity=identity, current_identity=lambda: sel.focus_identity,
                        clipboard=clip or Clip(), injector=keys or Keys(), app_id='notes')

    def test_typed_through_accessibility(self):
        f = field()
        self.assertEqual(self.run_insert(f), 'typed')
        self.assertEqual(f.value, 'Hello there world')

    def test_focus_changed_saves_to_history(self):
        f = field()
        keys = Keys()
        self.assertEqual(self.run_insert(f, same=False, keys=keys), 'saved')
        self.assertEqual((f.value, keys.calls), ('Hello world', []))

    def test_never_replaces_selected_text(self):
        f = field(selection=(0, 5))
        sel = S.capture([app_with(f)], enabled=True, allow_empty=True)
        with self.assertRaises(I.NotDone):
            I.insert('x', sel, expected_identity=sel.focus_identity, current_identity=lambda: sel.focus_identity, clipboard=Clip(), injector=Keys(), app_id='')
        self.assertEqual(f.value, 'Hello world')

    def test_paste_when_insert_is_refused_and_clipboard_comes_back(self):
        f = field(insert_works=False)
        f.insert_text = lambda pos, text: False
        clip, keys = Clip(), Keys(f, ' there')
        self.assertEqual(self.run_insert(f, clip=clip, keys=keys), 'pasted')
        self.assertEqual((clip.puts, keys.calls, clip.restored), ([' there'], ['ctrl+v'], True))

    def test_unverified_insert_not_retried(self):
        f = field(insert_works=False)   # says it inserted, but the text never shows up
        keys = Keys()
        self.assertEqual(self.run_insert(f, keys=keys), 'saved')
        self.assertEqual(keys.calls, [])   # no paste on top: that could type it twice

    def test_terminal_paste_combo(self):
        f = field()
        f.insert_text = lambda pos, text: False
        keys = Keys(f, ' there')
        sel = S.capture([app_with(f)], enabled=True, allow_empty=True)
        I.insert(' there', sel, expected_identity=sel.focus_identity, current_identity=lambda: sel.focus_identity,
                 clipboard=Clip(), injector=keys, app_id='org.gnome.Terminal')
        self.assertEqual(keys.calls, ['ctrl+shift+v'])

    def test_receipt_lines_like_swift(self):
        self.assertEqual(I.receipt_line('typed'), 'verified: Typing dispatched to the chosen composer. Do not repeat typing without inspecting it.')
        self.assertEqual(I.receipt_line('saved'), 'not_done: The chosen field lost focus or input could not be sent.')


if __name__ == '__main__':
    unittest.main()
