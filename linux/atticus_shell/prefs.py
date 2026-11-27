"""settings.json, shared with the macOS app: same keys and migrations as `enum Prefs` (Atticus.swift:186)."""
import json
import os
import tempfile

MEETING_STYLES = [
    {'name': 'Direct', 'text': 'Lead with the answer in plain words. No hedging or filler.'},
    {'name': 'Persuasive', 'text': 'Speak to feelings first: why it matters to them and what it means for them, a vivid picture or short story, then the fact that backs it.'},
    {'name': 'Technical', 'text': 'Precise terms, numbers and how it works. Name the tradeoff.'},
    {'name': 'Friendly', 'text': 'Warm and conversational, like talking to a colleague. Simple words.'},
]

# Mirrors Prefs.defaults. hotkeyCode/hotkeyMods are macOS key codes, kept so the file round-trips; Linux reads linux*.
DEFAULTS = {
    'hotkeyCode': 49, 'hotkeyMods': 4096 | 2048, 'hotkeyLabel': '⌃⌥Space', 'login': True, 'paused': False, 'corner': 'top-left', 'linger': 'normal',
    'soundStart': 'Pop', 'soundRelease': 'Bottle', 'soundDone': 'Glass', 'soundStop': 'Tink', 'volume': 0.16, 'client': 'claude', 'model': 'haiku', 'instant': True,
    'wakeWord': False, 'wakeWordName': 'Atticus', 'microphone': 'automatic', 'quietAudio': False, 'quietLevel': 0.18, 'agentModel': 'sonnet', 'drawModel': 'sonnet',
    'agentUserTools': True, 'fixSetup': False, 'modelSuggestions': True,
    'meetingStyle': 'Direct', 'meetingCustom': '', 'meetingStyles': MEETING_STYLES,
    'selection': True, 'ahead': True, 'aheadDelay': 0.7, 'live': True, 'screen': 'auto', 'openOn': 'here', 'workIn': 'back', 'miniScreen': True, 'draw': True,
    'guideSteps': 'auto', 'pageJS': False, 'speak': True, 'showAnswers': 'stream', 'autoCopy': False, 'hideWhenSharing': True, 'menuIcon': True, 'autoUpdate': True,
    'learn': True, 'safe': True, 'pointer': False, 'meeting': False, 'meetingSave': True, 'meetingAuto': 'sure', 'meetingNudge': False, 'meetingDetect': True,
    'learnMe': True, 'saveChats': True, 'stats': True, 'tips': True, 'support': True, 'comeBack': True, 'askMe': True, 'meetingSummary': True, 'typeRight': True,
    'talkMode': 'hold', 'uiLanguage': 'auto', 'speechModel': 'speech', 'whisperModel': 'turbo', 'whisperLanguage': 'auto', 'improve': False, 'improveAsked': False,
    'draftInsert': 'ask', 'dictate': True, 'dictateHotkeyCode': -1, 'dictateHotkeyMods': 0, 'dictateHotkeyLabel': '', 'dictateMode': 'cleanup', 'dictateHistory': True,
    'dictateLearn': True, 'dictateWords': [], 'dictateFixes': {}, 'boxW': 500, 'boxH': 420, 'autoMinimize': True, 'tinyFold': False, 'followPointer': True,
    'voiceRate': 196,
}
# Linux has no fn key and no Carbon codes: shortcuts are portal/GTK triggers, and the labels say Ctrl/Super.
LINUX_DEFAULTS = {
    'linuxHotkey': 'CTRL+ALT+space', 'linuxDictateHotkey': 'CTRL+LOGO+space', 'linuxHandoffHotkey': 'CTRL+ALT+Return',
    'linuxMeetingHotkey': 'CTRL+Return', 'hotkeyLabel': 'Ctrl+Alt+Space', 'dictateHotkeyLabel': 'Ctrl+Super+Space',
}
RETIRED = ('quietAudio', 'quietLevel', 'autoCopy', 'askMe', 'corner', 'linger', 'soundStart', 'soundRelease', 'soundDone', 'soundStop',
           'instant', 'live', 'ahead', 'aheadDelay', 'learn', 'selection', 'guideSteps', 'workIn', 'openOn', 'draftInsert',
           'meetingNudge', 'meetingDetect', 'meetingAuto', 'fixSetup', 'dictateLearn')   # still readable, no longer offered
LANGUAGES = ('it', 'es', 'fr', 'de')


def default_path(environ=os.environ):
    base = environ.get('ATTICUS_DIR') or os.path.join(environ.get('HOME') or os.path.expanduser('~'), '.config/atticus')
    return os.path.join(base, 'settings.json')


def resolve_ui_language(choice, preferred=None):
    if choice == 'auto':
        if preferred is None:
            preferred = [os.environ.get(k) for k in ('LC_ALL', 'LC_MESSAGES', 'LANG') if os.environ.get(k)]
        choice = (preferred or ['en'])[0]
    code = choice.replace('-', '_').split('_')[0].split('.')[0].lower()
    return code if code in LANGUAGES else 'en'


class Prefs:
    def __init__(self, path=None):
        self.path = path or default_path()
        self.d = {}
        self.load()

    def load(self):
        try:
            with open(self.path) as f:
                saved = json.load(f)
            if not isinstance(saved, dict):
                saved = {}
        except (OSError, ValueError):
            saved = {}
        values = {**DEFAULTS, **LINUX_DEFAULTS, **saved}
        if 'support' not in saved and saved.get('tips') is False:
            values['support'] = False   # tips turned off before the tip jar existed keep its line off too
        if values.get('routing') not in ('cheapest', 'balanced', 'capable'):
            values['routing'] = ('capable' if values.get('model') != 'haiku' else
                                 'balanced' if values.get('escalate') is not False and values.get('escalateAuto') is not False else 'cheapest')
        for key, old in (('boxW', 460), ('boxH', 360)):
            if values.get(key) == old:
                values[key] = DEFAULTS[key]   # the old default untouched was never a choice
        if values.get('dictateHotkeyLabel') == '' and values.get('dictateHotkeyOff') is not True:
            values['dictateHotkeyLabel'] = LINUX_DEFAULTS['dictateHotkeyLabel']   # Swift's fn default, Linux-style
        self.d = values

    def set(self, key, value):
        """Persist one value; False (and the old value kept) when the file can't be written."""
        old, had = self.d.get(key), key in self.d
        self.d[key] = value
        try:
            folder = os.path.dirname(self.path)
            os.makedirs(folder, mode=0o700, exist_ok=True)
            with tempfile.NamedTemporaryFile('w', dir=folder, delete=False, prefix='.settings-') as f:
                json.dump(self.d, f, indent=2, sort_keys=True, ensure_ascii=False)
                tmp = f.name
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except OSError:
            if had:
                self.d[key] = old
            else:
                self.d.pop(key, None)
            return False
        return True

    def bool(self, key):
        v = self.d.get(key)
        return v if isinstance(v, bool) else False

    def str(self, key):
        v = self.d.get(key)
        return v if isinstance(v, str) else ''

    def num(self, key):
        v = self.d.get(key)
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0

    @property
    def ui_language(self):
        return resolve_ui_language(self.str('uiLanguage'))
