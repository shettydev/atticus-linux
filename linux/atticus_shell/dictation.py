"""Dictate anywhere: hold the dictation shortcut, talk, let go; double-tap to lock hands-free; one more press inserts.

DictationData ports enum DictationData (Atticus.swift:23): the same dictation.json (history, words, sessions, todayWords,
day, seconds), 30 days / 500 entries, the dictionary and learned spellings. DictationController is the flow, GTK-free:
words land at the caret captured at key-down; never on the clipboard for keeps; when they can't land they go to History
(or stay in the pill with History off).
"""
import json
import os
import re
import time
import uuid

DOUBLE_TAP = 0.35   # a second press this soon after a short tap locks dictation on
TAP = 0.3


def _words_file_known():
    try:
        with open('/usr/share/dict/words') as f:
            words = {w.strip().lower() for w in f}
    except OSError:
        words = set()
    return lambda w: w.lower() in words


class DictationData:
    def __init__(self, path, prefs, set_pref, known=None, today=None, now=time.time):
        self.path, self.prefs, self.set_pref = path, prefs, set_pref
        self.known = known or _words_file_known()
        self.today = today or (lambda: time.strftime('%Y-%m-%d'))
        self.now = now
        try:
            with open(path) as f:
                self.archive = json.load(f)
        except (OSError, ValueError):
            self.archive = {}
        for k, v in (('history', []), ('words', 0), ('sessions', 0), ('todayWords', 0), ('day', ''), ('seconds', 0.0)):
            self.archive.setdefault(k, v)

    @property
    def words(self):
        return list(self.prefs.get('dictateWords') or [])

    @property
    def fixes(self):
        return dict(self.prefs.get('dictateFixes') or {})

    def prune(self):
        cutoff = self.now() - 30 * 86400
        self.archive['history'] = [e for e in self.archive['history'] if e.get('timestamp', 0) > cutoff][:500]

    def save(self):
        folder = os.path.dirname(self.path)
        try:
            os.makedirs(folder, mode=0o700, exist_ok=True)
            tmp = os.path.join(folder, f'.dictation-{uuid.uuid4()}')
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)   # private before any transcript byte is written
            with os.fdopen(fd, 'w') as f:
                json.dump(self.archive, f)
            os.replace(tmp, self.path)
            return True
        except OSError:
            return False

    def record(self, text, raw, app, seconds):
        old = json.loads(json.dumps(self.archive))
        count = len(text.split())
        if self.archive['day'] != self.today():
            self.archive['day'], self.archive['todayWords'] = self.today(), 0
        self.archive['words'] += count
        self.archive['todayWords'] += count
        self.archive['sessions'] += 1
        self.archive['seconds'] += max(0.0, count / 40 * 60 - seconds)   # typing at 40 wpm vs. the time spent talking
        if self.prefs.get('dictateHistory', True):
            row = {'id': str(uuid.uuid4()).upper(), 'timestamp': self.now(), 'app': app, 'text': text}
            if raw != text:
                row['raw'] = raw
            self.archive['history'].insert(0, row)
        self.prune()
        if self.save():
            return True
        self.archive = old
        return False

    def state(self):
        before = len(self.archive['history'])
        self.prune()
        if len(self.archive['history']) != before:
            self.save()
        a = self.archive
        return {'words': self.words, 'history': [dict(e) for e in a['history']],
                'stats': {'today': a['todayWords'] if a['day'] == self.today() else 0, 'words': a['words'], 'sessions': a['sessions'],
                          'minutes': int(a['seconds'] / 60)}}

    def word(self, value, replacing=None):
        value = value.strip()
        if not value or len(value) > 80 or any(ord(c) < 32 for c in value):
            return False
        words = [w for w in self.words if w != replacing]
        if value not in words:
            if len(words) >= 500 or sum(map(len, words)) + len(value) > 4000:
                return False
            words.append(value)
        if not self.set_pref('dictateWords', words):
            return False
        if replacing and replacing != value:
            self.set_pref('dictateFixes', {k: (value if v == replacing else v) for k, v in self.fixes.items()})
        return True

    def forget(self, word):
        if not self.set_pref('dictateWords', [w for w in self.words if w != word]):
            return False
        self.set_pref('dictateFixes', {k: v for k, v in self.fixes.items() if v != word})
        return True

    def fix(self, heard, word):
        key = heard.lower()
        if key == word or word not in self.words or len(self.fixes) >= 500:
            return False
        return self.set_pref('dictateFixes', {**self.fixes, key: word})

    def fixed(self, text):
        """Learned spellings swapped in whole words, only for heard forms that are no real word ("cash" stays cash)."""
        for heard, value in self.fixes.items():
            if self.known(heard):
                continue
            pattern = re.compile(r'(?<![^\W_])' + re.escape(heard) + r'(?![^\W_])', re.I)

            def swap(m, value=value):
                found = m.group(0)
                return value[:1].upper() + value[1:] if found[:1].isupper() and value[:1].islower() else value
            text = pattern.sub(swap, text)
        return text


class DictationController:
    def __init__(self, ui, engine, mic, prefs, data, capture, insert, cleanup, later, cue, now=time.monotonic, app_name=lambda: ''):
        self.ui, self.make_engine, self.mic, self.prefs, self.data = ui, engine, mic, prefs, data
        self.capture, self.insert, self.cleanup, self.later, self.cue, self.now = capture, insert, cleanup, later, cue, now
        self.app_name = app_name
        self.state = 'idle'   # idle | listening | locked | finishing
        self.engine = self.target = None
        self.down_at = self.up_at = 0.0
        self.started = 0.0
        self.session = 0

    def edge(self, edge):
        t = self.now()
        if edge == 'down':
            if self.state == 'idle':
                return self.start()
            if self.state == 'locked':
                return self.finish()
            if self.state == 'listening' and self.up_at and t - self.up_at < DOUBLE_TAP and self.up_at - self.down_at < TAP:
                self.state = 'locked'
                self.ui('locked', True)
            self.down_at = t
            return
        if self.state != 'listening':
            return
        self.up_at = t
        if t - self.down_at < TAP:   # a tap: wait to see whether it's the first of a double tap
            session = self.session
            self.later(DOUBLE_TAP, lambda: self.finish() if self.session == session and self.state == 'listening' else None)
        else:
            self.finish()

    def start(self):
        engine = self.make_engine()
        if engine is None:
            self.ui('dictated', {'text': 'Dictation needs a speech model. Download it in Settings → Voice & Sound.', 'ok': False})
            return
        self.session += 1
        self.engine, self.state = engine, 'listening'
        self.down_at, self.up_at, self.started = self.now(), 0.0, self.now()
        self.target = self.capture()   # the caret when the key went down: words land there or nowhere
        self.ui('dictate', True)
        self.cue('soundStart')
        self.engine.start(self.prefs.get('whisperLanguage', 'auto'), lambda text: self.ui('words', text), self._final)
        if self.mic:
            self.mic.start(self.engine.feed)

    def finish(self):
        if self.state not in ('listening', 'locked'):
            return
        self.state = 'finishing'
        if self.mic:
            self.mic.stop()
        self.cue('soundRelease')
        self.ui('finishing', 'Cleaning up…' if self.prefs.get('dictateMode') == 'cleanup' else '')
        self.engine.finish()

    def _final(self, text):
        if self.state != 'finishing':
            return
        raw = (text or '').strip()
        if not raw:
            self.state = 'idle'
            self.cue('soundStop')
            self.ui('dictate', False)
            return
        value = self.data.fixed(raw)
        if self.prefs.get('dictateMode') == 'cleanup':
            value = (self.cleanup('', value) or value).strip()
        self.deliver(value, raw)

    def deliver(self, value, raw):
        seconds = self.now() - self.started
        if self.target is None:
            return self._failed('No text field.', value, raw, seconds)
        result = self.insert(value, self.target)
        if result == 'saved':
            return self._failed('Couldn’t insert.', value, raw, seconds)
        self.state = 'idle'
        self.data.record(value, raw, self.app_name(), seconds)
        self.cue('soundDone')
        self.ui('dictated', {'text': '', 'ok': True})

    def _failed(self, prefix, value, raw, seconds):
        self.state = 'idle'
        self.cue('soundStop')
        if self.prefs.get('dictateHistory', True) and self.data.record(value, raw, self.app_name(), seconds):
            self.ui('dictated', {'text': prefix + ' Saved to History.', 'ok': False, 'history': True})
        else:
            self.ui('dictated', {'text': prefix + ' Your words are still here.', 'ok': False, 'words': value})

    def cancel(self):
        if self.state == 'idle':
            return
        self.session += 1
        self.state = 'idle'
        if self.engine:
            self.engine.cancel()
        if self.mic:
            self.mic.stop()
        self.cue('soundStop')
        self.ui('dictate', False)


def brain_cleanup(before, text, timeout=20):
    """atticus.py --dictate-cleanup: `before NUL words` in, the cleaned words out (None on failure)."""
    import subprocess
    from atticus_shell import box
    try:
        r = subprocess.run(box.py_command(['--dictate-cleanup']), input=before + '\0' + text, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    out = (r.stdout or '').strip()
    return out if r.returncode == 0 and out else None
