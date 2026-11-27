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
    '--look': lambda a: __import__('atticus_shell.bridge_cli', fromlist=['x']).look_command(a),
    '--step-screen': lambda a: __import__('atticus_shell.bridge_cli', fromlist=['x']).step_screen_command(a),
    '--draw-target': lambda a: __import__('atticus_shell.bridge_cli', fromlist=['x']).draw_target_command(a),
    '--text': lambda a: __import__('atticus_shell.a11y', fromlist=['x']).text_command(a),
    '--window-target': lambda a: __import__('atticus_shell.a11y', fromlist=['x']).window_target_command(a),
    '--target-app': lambda a: __import__('atticus_shell.a11y', fromlist=['x']).target_app_command(a),
    '--user-app': lambda a: __import__('atticus_shell.a11y', fromlist=['x']).user_app_command(a),
    '--system-ui': lambda a: __import__('atticus_shell.a11y', fromlist=['x']).system_ui_command(a),
    '--hands': lambda a: __import__('atticus_shell.hands', fromlist=['x']).cli(a),
    '--guide-watch': lambda a: __import__('atticus_shell.hands', fromlist=['x']).guide_watch(a),
    '--type': lambda a: __import__('atticus_shell.insert', fromlist=['x']).type_command(a),
    '--calendar': lambda a: __import__('atticus_shell.calendar', fromlist=['x']).calendar_command(a),
    '--raise': ipc.cli_handler('Raise'),
    '--trigger': ipc.cli_handler('Trigger'),
    '--settings': ipc.cli_handler('OpenSettings'),
    '--bridge': lambda a: __import__('atticus_shell.bridge_cli', fromlist=['x']).bridge_command(a),
    '--selftest': lambda args: __import__('atticus_shell.speech.stt', fromlist=['selftest']).selftest(args),
}


def main(argv):
    if not argv:
        import importlib
        return importlib.import_module('atticus_shell.box').run_app()   # GTK loads only for the app itself, never for a helper call
    handler = HANDLERS.get(argv[0])
    if handler is None:
        print(f'atticus-shell: unknown option {argv[0]}', file=sys.stderr)
        return 2
    return handler(argv[1:])
