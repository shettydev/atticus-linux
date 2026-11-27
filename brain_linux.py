"""Atticus on Linux: replacements for the brain's macOS-only functions and prompt lines.

atticus.py stays one shared file. On Linux (atticus.PLATFORM == 'linux') it calls install(globals()) once, after every
definition, and this module rebinds the names in REPLACED and rewrites the macOS-specific constants. Functions not
listed here either never touch macOS (PORTABLE) or reach it only through osa(), which is inert on Linux (VIA_OSA).
tests/test_linux_seam_ratchet.py keeps the three tables complete.
"""
import os
import re

# ---------------------------------------------------------------------------------------------------------------------
# Prompt lines: (macOS text, Linux text). A pair whose macOS text drifted upstream is skipped, and the seam tests fail.
SYSTEM_SWAPS = (
    ('You are Atticus, a Mac helper in a small box.', 'You are Atticus, a Linux desktop helper in a small box.'),
    ('press "cmd+t"', 'press "ctrl+t"'),
    ('- System settings: shell, defaults or AppleScript first;', '- System settings: shell (gsettings, kwriteconfig6, wpctl, nmcli) first;'),
    ('- Files (if needed): mdfind -name "<words>" | head -20; content: mdfind -onlyin ~/Documents "<words>" | head.',
     '- Files (if needed): localsearch search -f "<words>" | head -20, else plocate -i "<words>" | head -20; content: rg -il "<words>" ~/Documents | head.'),
    ('Open Atticus Settings: open -a Atticus.', 'Open Atticus Settings: gtk-launch atticus.'),
)
WORKFLOW_SWAPS = (('open, URL scheme, osascript or hands label/menu path', 'xdg-open, URL scheme, gio or hands label/menu path'),)
AGENT_SWAPS = (('never cmd+l or the address bar', 'never ctrl+l or the address bar'),)

LINUX_DENIED = ('mkfs', 'fdisk', 'parted', 'wipefs', 'systemctl poweroff', 'systemctl reboot', 'systemctl suspend', 'loginctl terminate-session')
LINUX_UNSAFE = r'(^|[;&|(`\s])(mkfs(\.\w+)?|fdisk|parted|wipefs)\s|systemctl\s+(poweroff|reboot|suspend)|loginctl\s+terminate'
SAFE_TOOLS = ('xdg-open', 'gio open', 'localsearch', 'plocate', 'rg', 'curl', 'head', 'tail', 'grep', 'sort', 'wc', 'echo', 'sleep', 'date')
QUIET_MAC = ('open ', 'mdfind ', 'pbpaste')
QUIET_LINUX = ('xdg-open ', 'gio open ', 'localsearch ', 'plocate ', 'wl-paste', 'xclip -o')
LOOKUPS_LINUX = {'localsearch', 'plocate', 'rg', 'fd'}
RUNS_LINUX = ('.desktop', '.AppImage', '.run')
NEEDS = {'accessibility': 'Assistive technologies', 'screen': 'Screen sharing', 'automation': 'the Atticus browser extension',
         'calendar': 'Calendar', 'microphone': 'Microphone', 'page-js': 'page reading'}


def default_app(environ, home):
    """The Linux shell binary the brain's helper commands call (the macOS app path otherwise)."""
    return environ.get('ATTICUS_APP') or os.path.join(home, '.local/share/atticus/atticus-shell')


def app_dirs(home):
    return ('/usr/share/applications/', '/usr/local/share/applications/', os.path.join(home, '.local/share/applications') + '/',
            '/var/lib/flatpak/exports/share/applications/', os.path.join(home, '.local/share/flatpak/exports/share/applications') + '/',
            '/var/lib/snapd/desktop/applications/')


def swap(text, pairs):
    for mac, linux in pairs:
        text = text.replace(mac, linux)
    return text


# ---------------------------------------------------------------------------------------------------------------------
# Functions. Each REPLACED entry keeps the macOS function's name and signature.

def osa(script, *args, timeout=60):
    """AppleScript has no Linux runtime: every caller already treats '' as 'nothing came back'."""
    return ''


def _stub(name, task):
    def stub(*args, **kwargs):
        raise NotImplementedError(f'linux: {name} lands in {task}')
    stub.__name__ = name
    return stub


# name -> the task that replaces the stub. Remove a line when its real implementation lands in REPLACED.
STUBS = {
    'thumb': 'Task 24', 'preview_commands': 'Task 24', 'restore_focus': 'Task 24', 'request_screen': 'Task 24',
    'placement_window': 'Task 24', 'screens': 'Task 24', 'front': 'Task 24', '_place': 'Task 24', 'keep_behind': 'Task 24',
    'show': 'Task 24', 'installed_apps': 'Task 24', 'proc_of': 'Task 24', 'app_id': 'Task 24', 'app_running': 'Task 24',
    'quit_app': 'Task 24', 'notify': 'Task 24', 'look': 'Task 18',
    'background_tab': 'Task 25', 'remember_background_tab': 'Task 25', 'browser_terms': 'Task 25', 'browser_adapter': 'Task 25',
    'close_agent_window': 'Task 25', 'work_page': 'Task 25', 'page_document': 'Task 25', 'work_tab_page': 'Task 25',
    'show_tab': 'Task 25', 'allow_page_js': 'Task 25', 'agent_window_id': 'Task 25',
    'routine_job_path': 'Task 29', 'routine_install': 'Task 29', 'routine_remove': 'Task 29', 'routine_fire': 'Task 29',
    'mac_profile': 'Task 33', 'contacts': 'Task 33', 'playlist': 'Task 34', 'play': 'Task 34', 'queue': 'Task 34',
    'need': 'Task 36', 'automation_hint': 'Task 36',
}

REPLACED = {'osa': osa, **{name: _stub(name, task) for name, task in STUBS.items()}}

# Touch macOS words only to classify command text the model wrote; the same rules hold on Linux.
PORTABLE = {
    'risky': 'classifies command text (osascript/JXA rules stay correct and never fire on Linux commands)',
    'focus_command': 'classifies command text for focus changes',
    'shortcut_commands': 'filters learned commands by text; osascript ones never appear on Linux',
    'learn': 'stores verified commands; only inspects text',
    'replay_quit': 'parses a quit command from text',
    'label': 'turns command text into step labels',
    'action_kind': 'classifies interpreter commands by text',
    'hands_replayable': 'classifies hands/osascript commands by text',
    'report_crashes': 'reads ~/Library/Logs/DiagnosticReports, absent on Linux, so it reports nothing',
}
# Reach macOS only through osa() (inert on Linux) or through REPLACED helpers; their Linux branches land later.
VIA_OSA = {
    'fast': 'Task 24 adds Linux instant verbs; its osascript branch returns nothing on Linux',
    'web': 'Task 25 swaps the transport through browser_terms/browser_adapter/work_page',
    'relay': 'Task 15: pick/ask dialogs come from the box (ATTICUS_UI); osa() fallback is inert',
    'ask': 'Task 15: the typed pop-up is the box on Linux; osa() fallback is inert',
    'main': 'Task 24/27: notification and activate branches go through osa(); Linux uses notify()/show()',
}


def install(ns):
    """Rebind the Linux functions and constants in the atticus module namespace `ns`."""
    ns['SYSTEM'] = swap(ns['SYSTEM'], SYSTEM_SWAPS)
    ns['WORKFLOW'] = swap(ns['WORKFLOW'], WORKFLOW_SWAPS)
    ns['AGENT_SYSTEM'] = swap(ns['AGENT_SYSTEM'], AGENT_SWAPS)
    ns['DENIED'] = tuple(ns['DENIED']) + LINUX_DENIED
    ns['DENY'] = [f'Bash({c}:*)' for c in ns['DENIED']]
    ns['UNSAFE'] = re.compile(ns['UNSAFE'].pattern + '|' + LINUX_UNSAFE, ns['UNSAFE'].flags)
    me = ns['ME']
    ns['SAFE_OUT'] = [f'{c} *' for c in SAFE_TOOLS + (me,)] + ['wl-paste', 'xclip -o', 'date'] + [f'orca terminal {c} *' for c in ('list', 'read', 'send')]
    ns['QUIET'] = tuple(q for q in ns['QUIET'] if q not in QUIET_MAC) + QUIET_LINUX
    ns['LOOKUPS'] = (set(ns['LOOKUPS']) - {'mdfind'}) | LOOKUPS_LINUX
    ns['RUNS'] = tuple(ns['RUNS']) + RUNS_LINUX
    ns['APP_DIRS'] = app_dirs(ns['HOME'])
    ns['NEEDS'] = dict(NEEDS)
    ns['APP'] = default_app(os.environ, ns['HOME'])
    ns.update(REPLACED)
