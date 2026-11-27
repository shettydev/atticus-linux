import unittest

from tests import linux  # noqa: F401
from atticus_shell import calendar as C


class FakeBackend:
    def __init__(self, authorized=True):
        self.ok = authorized
        self.rows = [{'id': 'e1', 'title': 'Dentist', 'start': '2026-11-28T15:00:00.000+00:00', 'end': '2026-11-28T16:00:00.000+00:00',
                      'calendarID': 'personal', 'calendar': 'Personal', 'writable': True, 'allDay': False, 'attendees': [], 'modified': ''},
                     {'id': 'e2', 'title': 'Standup', 'start': '2026-11-28T09:00:00.000+00:00', 'end': '2026-11-28T09:15:00.000+00:00',
                      'calendarID': 'work', 'calendar': 'Work', 'writable': False, 'allDay': False, 'attendees': ['Sam sam@x.org'], 'modified': ''}]

    def authorized(self):
        return self.ok

    def calendars(self):
        return [{'id': 'personal', 'title': 'Personal', 'writable': True}, {'id': 'work', 'title': 'Work', 'writable': False}]

    def default_calendar(self):
        return 'personal'

    def events(self, start, end):
        return [dict(r) for r in self.rows if C.parse(r['start']) < end and C.parse(r['end']) > start]

    def add(self, calendar_id, title, start, end):
        row = dict(self.rows[0], id='new', title=title, start=C.iso(start), end=C.iso(end), calendarID=calendar_id)
        self.rows.append(row)
        return row

    def move(self, row, start, end):
        for r in self.rows:
            if r['id'] == row['id']:
                r.update(start=C.iso(start), end=C.iso(end))
                return dict(r)

    def remove(self, row):
        self.rows = [r for r in self.rows if r['id'] != row['id']]


DAY = {'from': '2026-11-28T00:00:00+00:00', 'to': '2026-11-29T00:00:00+00:00'}


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.b = FakeBackend()

    def run_tool(self, data):
        return C.run(data, self.b)

    def test_list_sorted_with_calendars(self):
        out = self.run_tool({'action': 'list', **DAY})
        self.assertEqual([e['title'] for e in out['events']], ['Standup', 'Dentist'])
        self.assertEqual(len(out['calendars']), 2)

    def test_query_matches_title_or_attendee(self):
        self.assertEqual([e['id'] for e in self.run_tool({'action': 'list', **DAY, 'query': 'sam'})['events']], ['e2'])

    def test_range_rules(self):
        self.assertEqual(self.run_tool({'action': 'list', 'from': DAY['to'], 'to': DAY['from']}), {'error': 'Use a valid range shorter than four years.'})

    def test_no_backend_error_names_settings(self):
        out = C.run({'action': 'list', **DAY}, None)
        self.assertIn('Settings', out['error'])

    def test_not_authorized_asks_for_calendar(self):
        self.assertEqual(C.run({'action': 'list', **DAY}, FakeBackend(authorized=False)), {'need': 'calendar'})

    def test_add_to_default_calendar(self):
        out = self.run_tool({'action': 'add', 'title': 'Lunch', 'start': '2026-11-28T12:00:00+00:00', 'end': '2026-11-28T13:00:00+00:00'})
        self.assertEqual((out['event']['title'], out['event']['calendarID']), ('Lunch', 'personal'))

    def test_add_to_read_only_refused(self):
        out = self.run_tool({'action': 'add', 'title': 'x', 'calendarID': 'work', 'start': '2026-11-28T12:00:00+00:00', 'end': '2026-11-28T13:00:00+00:00'})
        self.assertEqual(out, {'error': 'Choose a writable calendar from list.'})

    def test_move_requires_the_event_as_read(self):
        row = self.run_tool({'action': 'list', **DAY})['events'][1]
        self.assertEqual(self.run_tool({'action': 'move', 'start': '2026-11-28T16:00:00+00:00', 'end': '2026-11-28T17:00:00+00:00'}),
                         {'error': 'Read the event before changing it.'})
        changed = dict(row, title='Something else')
        self.assertEqual(self.run_tool({'action': 'move', 'event': changed, 'start': '2026-11-28T16:00:00+00:00', 'end': '2026-11-28T17:00:00+00:00'}),
                         {'error': 'Event changed or disappeared. Read it again and ask for approval again.'})
        out = self.run_tool({'action': 'move', 'event': row, 'start': '2026-11-28T16:00:00+00:00', 'end': '2026-11-28T17:00:00+00:00'})
        self.assertEqual(C.parse(out['event']['start']), C.parse('2026-11-28T16:00:00+00:00'))

    def test_read_only_event_not_changed(self):
        row = self.run_tool({'action': 'list', **DAY})['events'][0]
        out = self.run_tool({'action': 'delete', 'event': row})
        self.assertEqual(out, {'error': 'This calendar is read-only.'})

    def test_delete(self):
        row = self.run_tool({'action': 'list', **DAY})['events'][1]
        self.assertEqual(self.run_tool({'action': 'delete', 'event': row}), {'deleted': row})
        self.assertEqual(len(self.b.rows), 1)

    def test_unknown_action(self):
        self.assertEqual(self.run_tool({'action': 'nap'}), {'error': 'Unknown calendar action.'})


if __name__ == '__main__':
    unittest.main()
