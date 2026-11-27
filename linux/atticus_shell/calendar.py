"""--calendar: the brain's calendar JSON (CalendarTool, Atticus.swift:7401) over Evolution Data Server.

EDS holds the desktop's calendars, including GNOME Online Accounts (Google, Nextcloud, Microsoft). The contract is
macOS's: list events and writable calendars; add is immediate; move and delete act only on the exact event the brain
read (the user saw an Allow card for that one); recurring events change one occurrence's copy only.
"""
import json
import sys
from datetime import datetime, timedelta, timezone

NO_CALENDAR = 'No calendar is connected. Add one in Settings → Online Accounts (GNOME) or Evolution, then ask again.'


def parse(text):
    if not isinstance(text, str):
        return None
    try:
        d = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return None
    return d if d.tzinfo else None   # timezone offsets are required, as on macOS


def iso(d):
    return d.astimezone().isoformat(timespec='milliseconds')


def run(data, backend):
    if backend is None:
        return {'error': NO_CALENDAR}
    if not backend.authorized():
        return {'need': 'calendar'}
    action = data.get('action', '')
    if action == 'list':
        start, end = parse(data.get('from')), parse(data.get('to'))
        if not start or not end or end <= start or end - start > timedelta(days=4 * 365):
            return {'error': 'Use a valid range shorter than four years.'}
        query = (data.get('query') or '').lower()
        events = sorted(backend.events(start, end), key=lambda r: parse(r['start']))
        events = [e for e in events if not query or query in ' '.join([e['title']] + e.get('attendees', [])).lower()]
        return {'events': events, 'calendars': backend.calendars()}
    if action not in ('add', 'move', 'delete'):
        return {'error': 'Unknown calendar action.'}
    writable = {c['id']: c['writable'] for c in backend.calendars()}
    if action == 'add':
        title = data.get('title')
        if not isinstance(title, str) or not title.strip():
            return {'error': 'A title is required.'}
        target = data.get('calendarID') if 'calendarID' in data else backend.default_calendar()
        if not target or not writable.get(target):
            return {'error': 'Choose a writable calendar from list.'}
        row = None
    else:
        expected = data.get('event')
        start, end = (parse(expected.get('start')), parse(expected.get('end'))) if isinstance(expected, dict) else (None, None)
        if not start or not end:
            return {'error': 'Read the event before changing it.'}
        matches = [r for r in backend.events(start, end) if r == expected]
        if len(matches) != 1:
            return {'error': 'Event changed or disappeared. Read it again and ask for approval again.'}
        row = matches[0]
        if not row.get('writable'):
            return {'error': 'This calendar is read-only.'}
    if action == 'delete':
        backend.remove(row)
        return {'deleted': data.get('event') or {}}
    start, end = parse(data.get('start')), parse(data.get('end'))
    if not start or not end or end <= start:
        return {'error': 'Use a start and a later end with timezone offsets.'}
    if action == 'add':
        return {'event': backend.add(target, data['title'].strip(), start, end)}
    return {'event': backend.move(row, start, end)}


class EdsCalendar:
    """Evolution Data Server through its GObject API (ECal 2.0)."""

    def __init__(self):
        import gi
        gi.require_version('ECal', '2.0')
        gi.require_version('EDataServer', '1.2')
        gi.require_version('ICalGLib', '3.0')
        from gi.repository import ECal, EDataServer, ICalGLib
        self.ECal, self.EDS, self.ICal = ECal, EDataServer, ICalGLib
        self.registry = EDataServer.SourceRegistry.new_sync(None)
        self.sources = list(self.registry.list_enabled(EDataServer.SOURCE_EXTENSION_CALENDAR))
        self.clients = {}

    def authorized(self):
        return bool(self.sources)

    def _client(self, source):
        uid = source.get_uid()
        if uid not in self.clients:
            self.clients[uid] = self.ECal.Client.connect_sync(source, self.ECal.ClientSourceType.EVENTS, 30, None)
        return self.clients[uid]

    def calendars(self):
        out = []
        for s in self.sources:
            try:
                out.append({'id': s.get_uid(), 'title': s.get_display_name(), 'writable': not self._client(s).is_readonly()})
            except Exception:
                continue
        return out

    def default_calendar(self):
        s = self.registry.ref_default_calendar()
        return s.get_uid() if s else (self.sources[0].get_uid() if self.sources else '')

    def _time(self, t):
        return datetime.fromtimestamp(t.as_timet_with_zone(t.get_timezone() or self.ICal.Timezone.get_utc_timezone()), timezone.utc)

    def _row(self, source, writable, comp, start, end):
        attendees = []
        for a in comp.get_attendees() or []:
            attendees.append(' '.join(filter(None, [a.get_cn() or '', (a.get_value() or '').replace('mailto:', '')])))
        summary = comp.get_summary()
        return {'id': comp.get_id().get_uid() + ('@' + comp.get_id().get_rid() if comp.get_id().get_rid() else ''),
                'title': summary.get_value() if summary else '', 'start': iso(start), 'end': iso(end),
                'calendarID': source.get_uid(), 'calendar': source.get_display_name(), 'writable': writable,
                'allDay': bool(comp.get_dtstart() and comp.get_dtstart().get_value().is_date()), 'attendees': attendees, 'modified': ''}

    def events(self, start, end):
        rows = []
        for s in self.sources:
            try:
                client = self._client(s)
            except Exception:
                continue
            writable = not client.is_readonly()

            def each(icomp, istart, iend, *_):
                comp = self.ECal.Component.new_from_icalcomponent(icomp.clone())
                rows.append(self._row(s, writable, comp, self._time(istart), self._time(iend)))
                return True
            client.generate_instances_sync(int(start.timestamp()), int(end.timestamp()), None, each)
        return rows

    def _source(self, uid):
        return next(s for s in self.sources if s.get_uid() == uid)

    def add(self, calendar_id, title, start, end):
        comp = self.ECal.Component.new()
        comp.set_new_vtype(self.ECal.ComponentVType.EVENT)
        comp.set_uid(self.ICal.Component.new_vevent().get_uid() or __import__('uuid').uuid4().hex)
        comp.set_summary(self.ECal.ComponentText.new(title, None))
        utc = self.ICal.Timezone.get_utc_timezone()
        comp.set_dtstart(self.ECal.ComponentDateTime.new(self.ICal.Time.new_from_timet_with_zone(int(start.timestamp()), False, utc), None))
        comp.set_dtend(self.ECal.ComponentDateTime.new(self.ICal.Time.new_from_timet_with_zone(int(end.timestamp()), False, utc), None))
        source = self._source(calendar_id)
        self._client(source).create_object_sync(comp.get_icalcomponent(), self.ECal.OperationFlags.NONE, None)
        return self._row(source, True, comp, start, end)

    def _find(self, row):
        source = self._source(row['calendarID'])
        uid, _, rid = row['id'].partition('@')
        ok, icomp = self._client(source).get_object_sync(uid, rid or None, None)
        return source, self.ECal.Component.new_from_icalcomponent(icomp)

    def move(self, row, start, end):
        source, comp = self._find(row)
        utc = self.ICal.Timezone.get_utc_timezone()
        comp.set_dtstart(self.ECal.ComponentDateTime.new(self.ICal.Time.new_from_timet_with_zone(int(start.timestamp()), False, utc), None))
        comp.set_dtend(self.ECal.ComponentDateTime.new(self.ICal.Time.new_from_timet_with_zone(int(end.timestamp()), False, utc), None))
        self._client(source).modify_object_sync(comp.get_icalcomponent(), self.ECal.ObjModType.THIS, self.ECal.OperationFlags.NONE, None)
        return self._row(source, True, comp, start, end)

    def remove(self, row):
        source = self._source(row['calendarID'])
        uid, _, rid = row['id'].partition('@')
        self._client(source).remove_object_sync(uid, rid or None, self.ECal.ObjModType.THIS, self.ECal.OperationFlags.NONE, None)


def backend():
    try:
        b = EdsCalendar()
        return b if b.authorized() else None
    except Exception:
        return None


def calendar_command(args):
    """--calendar: one JSON object on stdin, one JSON reply."""
    try:
        data = json.loads(sys.stdin.read())
        if not isinstance(data, dict):
            raise ValueError
        result = run(data, backend())
    except ValueError:
        result = {'error': 'Expected a calendar JSON object.'}
    except Exception as e:
        result = {'error': f'Calendar could not finish: {e}'}
    print(json.dumps(result))
    return 0
