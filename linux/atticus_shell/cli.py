"""The helper CLI the brain calls ($ATTICUS_APP --look|--hands|--text|…), same stdout and exit codes as Atticus.swift.

Swift reference: --look 6344, --text 6438, --system-ui/--raise/--calendar/--hands/--step-screen/--guide-watch/
--user-app/--draw-target/--window-target/--target-app/--selftest/--type 9748-9845. Linux-only: --trigger, --settings, --bridge.
"""
import sys

from atticus_shell import ipc


def pending(flag, task):
    def handler(args):
        print(f'linux: {flag} lands in {task}', file=sys.stderr)
        return 1
    return handler


HANDLERS = {
    '--look': pending('--look', 'Task 18'),
    '--step-screen': pending('--step-screen', 'Task 18'),
    '--draw-target': pending('--draw-target', 'Task 18'),
    '--text': pending('--text', 'Task 19'),
    '--window-target': pending('--window-target', 'Task 19'),
    '--target-app': pending('--target-app', 'Task 19'),
    '--user-app': pending('--user-app', 'Task 19'),
    '--system-ui': pending('--system-ui', 'Task 19'),
    '--hands': pending('--hands', 'Task 22'),
    '--guide-watch': pending('--guide-watch', 'Task 22'),
    '--type': pending('--type', 'Task 23'),
    '--calendar': pending('--calendar', 'Task 32'),
    '--raise': ipc.cli_handler('Raise'),
    '--trigger': ipc.cli_handler('Trigger'),
    '--settings': ipc.cli_handler('OpenSettings'),
    '--bridge': pending('--bridge', 'Task 24'),
    '--selftest': lambda args: __import__('atticus_shell.speech.stt', fromlist=['selftest']).selftest(args),
}


def main(argv):
    if not argv:
        from atticus_shell import box   # GTK loads only for the app itself, never for a helper call
        return box.run_app()
    handler = HANDLERS.get(argv[0])
    if handler is None:
        print(f'atticus-shell: unknown option {argv[0]}', file=sys.stderr)
        return 2
    return handler(argv[1:])
