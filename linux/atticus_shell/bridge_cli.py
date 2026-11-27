"""--look, --step-screen, --draw-target and --bridge: the brain's view of the desktop through this desktop's bridge."""
import json
import os
import sys

from atticus_shell.desktop import ids


def current_bridge():
    from atticus_shell.desktop.bridge import bridge_for
    from atticus_shell.desktop.env import detect
    return bridge_for(detect())


def _win_json(w):
    return {'id': ids.public_id(w.id), 'app': w.app_id, 'pid': w.pid, 'title': w.title, 'frame': [w.x, w.y, w.width, w.height],
            'output': w.output, 'workspace': w.workspace, 'minimized': w.minimized, 'fullscreen': w.fullscreen, 'focused': w.focused}


def bridge_command(args, bridge=None):
    """--bridge screens [visible] | windows | focused | pointer | move_resize ID X Y W H | set_fullscreen ID 1|0 |
    activate ID | lower ID | keep_above ID 1|0  ->  JSON on stdout; ids are the brain's integer ids."""
    if not args:
        print(bridge_command.__doc__.strip(), file=sys.stderr)
        return 2
    b = bridge or current_bridge()
    verb, rest = args[0], args[1:]
    if verb == 'screens':
        from atticus_shell.screen import screens_rows
        print(json.dumps(screens_rows(b, visible=rest[:1] == ['visible'])))
        return 0
    if verb == 'windows':
        print(json.dumps([_win_json(w) for w in b.windows()]))
        return 0
    if verb == 'focused':
        w = b.focused()
        print(json.dumps(_win_json(w) if w else None))
        return 0
    if verb == 'pointer':
        try:
            print(json.dumps(list(b.pointer())))
        except Exception as e:
            print(json.dumps({'error': str(e)}))
            return 1
        return 0
    if verb in ('move_resize', 'set_fullscreen', 'activate', 'lower', 'keep_above') and rest:
        target = ids.resolve(rest[0], [w.id for w in b.windows()])
        if target is None:
            print(json.dumps({'ok': False, 'error': 'no such window'}))
            return 1
        try:
            values = [int(float(v)) for v in rest[1:]]
        except ValueError:
            return 2
        if verb == 'move_resize' and len(values) == 4:
            ok = b.move_resize(target, *values)
        elif verb in ('set_fullscreen', 'keep_above') and len(values) == 1:
            ok = getattr(b, verb)(target, bool(values[0]))
        elif verb in ('activate', 'lower') and not values:
            ok = getattr(b, verb)(target)
        else:
            return 2
        print(json.dumps({'ok': bool(ok)}))
        return 0 if ok else 1
    return 2


def look_command(args):
    from atticus_shell import screen
    return screen.look(args, current_bridge())


def step_screen_command(args):
    from atticus_shell import screen
    pixels = screen.step_screen(current_bridge())
    if not pixels:
        return 1
    print(json.dumps(pixels))
    return 0


def draw_target_command(args):
    from atticus_shell import screen
    target = screen.draw_target(current_bridge(), os.environ.get('ATTICUS_SCREEN', ''))
    if target:
        print(json.dumps(target))
    return 0
