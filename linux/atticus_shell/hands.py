"""--hands: native actions, accessibility first (Hands.cli, Atticus.swift:9500), the mouse and keyboard only as fallback.

Verbs: see [app] | menu <app> "<A > B>" | click <target> [near <label>] [double] [right] [front] | click <spot> <spot>… |
scroll down|up|left|right [pages|all] [target] | scroll-to "<text>" | press "<keys>" [times] | drag <from> <to> |
type <field> "<text>" [submit] [append] | type-after "<exact anchor>" "<text>" | wait [seconds].
Targets: a number from the last `see`, a label, a role ("text field"), or a spot "x,y" / "<screen>:x,y" (picture pixels).
Every risky action is described first (the `checked` dict) so the brain can show the Allow card: ATTICUS_HANDS_SESSION=1
prints it and waits for "y"; ATTICUS_HANDS_RECEIPT=1 takes an earlier approval on stdin and checks it still matches.
Failures print on stdout and exit 1, like the macOS app.
"""
import json
import os
import re
import tempfile
import time

from atticus_shell import a11y as A

RISKY = re.compile(r'(?i)\b(buy|purchase|pay|payment|checkout|order|send|delete|remove|trash|submit|publish|post|sign.?out|log.?out|'
                   r'unsubscribe|subscribe|transfer|bid|erase|withdraw|donate|authorize|grant access|allow access|logout|book|reserve|merge|'
                   r'deploy|discard|wipe|format|apply(?! filters| changes| settings| coupon| code| promo| discount| theme| style| formatting)|confirm)\b')
GENERIC_OK = re.compile(r'(?i)^\s*(ok|yes|continue|allow|accept|agree|complete|finish|grant)\s*$')
PRESS = ('click', 'press', 'activate', 'jump', 'toggle', 'open')
SPOT = re.compile(r'^\s*(?:(\d+):)?(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*$')


class Failure(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def _num(s, lo, hi):
    try:
        v = float(s)
    except (TypeError, ValueError):
        return False
    return lo <= v <= hi


def spots(args):
    rest = args[1:]
    return rest if args[:1] == ['click'] and 2 <= len(rest) <= 40 and all(SPOT.match(r) for r in rest) else []


def validate(args):
    if not args:
        raise Failure('Use see|click|scroll|scroll-to|press|drag|type|wait.')
    verb, rest = args[0], args[1:]
    if any(r.startswith('--') for r in rest):
        raise Failure('Hands accepts no flags.')
    if verb == 'menu':
        parts = rest[1].split('>') if len(rest) == 2 else []
        if len(rest) != 2 or not rest[0] or len(parts) < 2 or not all(p.strip() for p in parts):
            raise Failure('Use menu app "Menu > Item".')
    elif verb == 'see':
        if len(rest) > 1:
            raise Failure('Use see [app].')
    elif verb == 'click':
        if len(spots(args)) > 1:
            return
        if not rest or len(rest) > 6:
            raise Failure('Use click target [near label] [double] [right] [front], or click spot spot ... for several picture spots.')
    elif verb == 'scroll':
        if not (1 <= len(rest) <= 3) or rest[0] not in ('down', 'up', 'left', 'right') or \
                (len(rest) == 3 and rest[1] != 'all' and not _num(rest[1], 0.1, 50)):
            raise Failure('Use scroll down|up|left|right [pages|all] [target].')
    elif verb == 'scroll-to':
        if len(rest) != 1:
            raise Failure('Use scroll-to text.')
    elif verb == 'press':
        if not (1 <= len(rest) <= 2):
            raise Failure('Use press keys [times].')
        from atticus_shell.input import parse_combo
        try:
            parse_combo(rest[0])
        except ValueError as e:
            raise Failure(str(e))
        if len(rest) == 2 and not (rest[1].isdigit() and 1 <= int(rest[1]) <= 50):
            raise Failure('Use press keys [times 1-50].')
    elif verb == 'drag':
        if len(rest) != 2:
            raise Failure('Use drag from to.')
    elif verb == 'type':
        if not (2 <= len(rest) <= 4) or not all(r in ('submit', 'append') for r in rest[2:]):
            raise Failure('Use type target text [submit] [append].')
    elif verb == 'type-after':
        if len(rest) != 2 or not rest[0]:
            raise Failure('Use type-after anchor text.')
    elif verb == 'wait':
        if len(rest) > 1 or (rest and not (_num(rest[0], 0.0001, 30))):
            raise Failure('Use wait [seconds, max 30].')
    else:
        raise Failure(f'Unknown hands verb: {verb}')


def click_options(rest):
    near, double, right, front, i = '', False, False, False, 1
    while i < len(rest):
        word = rest[i]
        if word == 'near' and i + 1 < len(rest) and not near:
            near, i = rest[i + 1], i + 1
        elif word in ('double', 'right', 'front'):
            if {'double': double, 'right': right, 'front': front}[word]:
                raise Failure(f'Duplicate {word}.')
            double, right, front = double or word == 'double', right or word == 'right', front or word == 'front'
        else:
            raise Failure(f'Unknown click option: {word}')
        i += 1
    return near, double, right, front


def risky_label(label, m=None):
    if label.strip().lower() == 'cancel':
        return False
    if RISKY.search(label):
        return True
    if m is None:
        return bool(GENERIC_OK.match(label))
    context = ' '.join([m.window] + m.texts)
    return bool(GENERIC_OK.match(label)) and bool(RISKY.search(context))


def default_state():
    return os.environ.get('ATTICUS_HANDS_STATE') or os.path.join(os.environ.get('XDG_RUNTIME_DIR') or tempfile.gettempdir(), 'atticus-hands.json')


class Hands:
    def __init__(self, apps, bridge, injector, state=None, safe=True, env=None, sleep=time.sleep, outputs=None):
        self.apps, self.bridge, self.inj = apps, bridge, injector
        self.state, self.safe, self.env, self.sleep = state or default_state(), safe, env if env is not None else os.environ, sleep
        self.outputs = outputs or bridge.outputs

    # state
    def save(self, m):
        tmp = self.state + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(m.as_dict(), f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.state)

    def load(self):
        try:
            with open(self.state) as f:
                return A.Map.from_dict(json.load(f))
        except (OSError, ValueError, TypeError):
            return None

    # apps
    def app(self, name=None, pid=None):
        apps = self.apps()
        if pid:
            hit = next((a for a in apps if a.pid == pid), None)
            if hit:
                return hit
        if name:
            hit = A._find(apps, name)
            if not hit:
                raise Failure(f'{name} has no accessible window. Open it, or turn on accessibility for it, then run see again.')
            return hit[0]
        front = self.bridge.focused()
        hit = next((a for a in apps if front and a.pid == front.pid), None)
        if not hit:
            raise Failure('No front app to act in. Name the app: see <app>.')
        return hit

    def window_of(self, pid):
        return next((w for w in self.bridge.windows() if w.pid == pid), None)

    # targets
    def point(self, target):
        m = SPOT.match(target)
        if not m:
            return None
        screen, x, y = m.group(1), float(m.group(2)), float(m.group(3))
        if screen:
            outs = self.outputs()
            o = outs[max(0, min(int(screen) - 1, len(outs) - 1))] if outs else None
            if o is None:
                raise Failure('No screen for that picture spot.')
            pw = 1280
            x, y = o.x + x * o.width / pw, o.y + y * o.width / pw
        return x, y

    def resolve(self, target, m, near=''):
        p = self.point(target)
        if p is not None:
            return A.Node(0, 'point', f'Unidentified screen point {target}', '', [p[0], p[1], 0, 0], []), None
        if target.isdigit():
            node = next((n for n in m.nodes if n.id == int(target)), None)
            if node is None:
                raise Failure(f'No control [{target}] in the last map. Run see again.')
            return node, m.accs.get(node.id)
        t = target.strip().lower()
        hits = ([n for n in m.nodes if n.label.lower() == t] or [n for n in m.nodes if t and t in n.label.lower()]
                or [n for n in m.nodes if n.short_role.lower() == t])
        if not hits:
            raise Failure(f'No control named {A.quote(target)}. Run see and use a number or an exact label.')
        if near and len(hits) > 1:
            anchors = [n for n in m.nodes if near.lower() in n.label.lower()]
            if anchors:
                ax, ay = anchors[0].frame[0], anchors[0].frame[1]
                hits.sort(key=lambda n: (n.frame[0] - ax) ** 2 + (n.frame[1] - ay) ** 2)
        return hits[0], m.accs.get(hits[0].id)

    def targets(self, args):
        if len(spots(args)) > 1:
            return spots(args)
        verb = args[0]
        if verb in ('click', 'type', 'type-after'):
            return [args[1]]
        if verb == 'drag':
            return [args[1], args[2]]
        if verb == 'scroll':
            if len(args) == 4:
                return [args[3]]
            if len(args) == 3 and args[2] != 'all' and not _num(args[2], -1e9, 1e9):
                return [args[2]]
        return []

    def checked(self, args, m):
        labels, at, secure, verb = [], [], False, args[0]
        if verb in ('click', 'drag', 'type'):
            near = click_options(args[1:])[0] if verb == 'click' and len(spots(args)) < 2 else ''
            for t in self.targets(args):
                n, _ = self.resolve(t, m, near)
                labels.append(n.label)
                at.append(str(n.frame))
                secure = secure or n.secret
        elif verb == 'type-after':
            labels = [args[1]]
        elif verb == 'press':
            labels, at = [m.focused, args[1]], [m.focusIdentity]
        risk = any(l.startswith('Unidentified screen point ') or risky_label(l, m) or (verb == 'drag' and re.search(r'(?i)\b(bin|recycle)\b', l))
                   for l in labels) or (verb == 'type' and 'submit' in args[3:])
        return {'labels': labels, 'secure': secure, 'risky': bool(risk), 'at': at, 'pid': m.pid, 'args': args, 'windowID': m.windowID,
                'windowFrame': m.windowFrame, 'window': A.mask(m.window), 'app': m.app}

    # running
    def run(self, args, stdin, stdout):
        try:
            for line in self._run(args, stdin, stdout):
                print(line, file=stdout)
            return 0
        except Failure as e:
            print(e.message, file=stdout)
            return 1

    def _run(self, args, stdin, stdout):
        validate(args)
        verb = args[0]
        if verb == 'see':
            m = A.capture_app(self.app(args[1] if len(args) > 1 else None))
            self.save(m)
            return [m.description]
        if verb == 'menu':
            app = self.app(args[1])
            item, label = self.menu_item(app, args[2])
            if self.safe and (risky_label(args[2]) or risky_label(label)):
                raise Failure("This menu action needs the user's Allow card. Use the normal hands click path.")
            if not item.do_action(next((a for a in PRESS if a in item.action_names()), '')):
                raise Failure(f'Could not run {A.quote(label)}.')
            return [f'Clicked {A.quote(label)}']
        old = self.load()
        app = self.app(old.app if old else None, old.pid if old else None)
        before = A.capture_app(app)
        check = self.checked(args, before)
        if self.env.get('ATTICUS_HANDS_SESSION') == '1':
            print(json.dumps(check), file=stdout, flush=True)
            if stdin.readline().strip() != 'y':
                raise Failure('Action cancelled.')
        else:
            receipt = None
            if self.env.get('ATTICUS_HANDS_RECEIPT') == '1':
                try:
                    receipt = json.loads(stdin.read() or 'null')
                except ValueError:
                    receipt = None
            if receipt is not None:
                if receipt != check:
                    raise Failure('The screen changed while checking permission. Run see again.')
            elif (check['risky'] and self.safe) or (verb == 'type' and check['secure']):
                raise Failure('Use atticus.py --hands so the user can allow this action.')
        if old and any(t.isdigit() for t in self.targets(args)) and old.signature != before.signature:
            self.save(before)
            raise Failure('The screen changed. Run see again.')
        result = getattr(self, 'do_' + verb.replace('-', '_'))(args, before, app)
        self.sleep(0.15)
        after = A.capture_app(app)
        self.save(after)
        return result + [after.description] if verb != 'wait' else [after.description]

    def _front(self, pid):
        w = self.window_of(pid)
        if w is not None:
            self.bridge.activate(w.id)

    def _mouse(self, spot, pid, button='left', count=1):
        if self.inj is None:
            raise Failure('not_done: This app needs a real click and Atticus has no input access. Settings → Desktop integration shows the one-time fix.')
        self._front(pid)
        self.inj.click(spot[0], spot[1], button=button, count=count)

    def do_click(self, args, m, app):
        several = spots(args)
        if len(several) > 1:
            for s in several:
                self._mouse(self.point(s), app.pid)
            return [f'Clicked {len(several)} spots']
        near, double, right, front = click_options(args[1:])
        node, acc = self.resolve(args[1], m, near)
        spot = (node.frame[0] + node.frame[2] / 2, node.frame[1] + node.frame[3] / 2)
        action = next((a for a in PRESS if acc is not None and a in acc.action_names()), None)
        if action and node.id != 0 and not (double or right or front) and acc.do_action(action):
            return [f'Clicked {A.quote(node.label)}']
        self._mouse(spot, app.pid, 'right' if right else 'left', 2 if double else 1)
        return [f'Clicked {A.quote(node.label)}']

    def menu_item(self, app, path):
        parts = [p.strip().lower() for p in path.split('>')]
        level = [w for w in app.windows]
        found = None
        for depth, want in enumerate(parts):
            found = None
            queue = list(level)
            while queue:
                acc = queue.pop(0)
                if acc.role in ('menu', 'menu item', 'check menu item', 'radio menu item') and (acc.name or '').strip().lower() == want:
                    found = acc
                    break
                queue.extend(acc.children())
            if found is None:
                raise Failure(f'No menu item {A.quote(path.split(">")[depth].strip())} in {app.name}.')
            level = [found]
        return found, A.mask(found.name or parts[-1])

    def do_scroll(self, args, m, app):
        direction = args[1]
        amount = args[2] if len(args) > 2 else '1'
        pages = 50 if amount == 'all' else float(amount) if _num(amount, 0.1, 50) else 1
        target = args[3] if len(args) > 3 else (amount if len(args) > 2 and amount != 'all' and not _num(amount, -1e9, 1e9) else '')
        if target:
            node, _ = self.resolve(target, m)
            spot = (node.frame[0] + node.frame[2] / 2, node.frame[1] + node.frame[3] / 2)
        else:
            x, y, w, h = m.windowFrame
            spot = (x + w / 2, y + h / 2)
        if self.inj is None:
            raise Failure('not_done: Scrolling needs input access. Settings → Desktop integration shows the one-time fix.')
        steps = max(1, round(pages * 5))
        if hasattr(self.inj, 'move'):
            self.inj.move(*spot)
        dx = {'left': -steps, 'right': steps}.get(direction, 0)
        dy = {'up': steps, 'down': -steps}.get(direction, 0)
        self.inj.scroll(dx, dy)
        return [f'Scrolled {direction}']

    def do_scroll_to(self, args, m, app):
        want = args[1].lower()
        for n in m.nodes:
            if want in n.label.lower() or want in n.value.lower():
                acc = m.accs.get(n.id)
                if acc is not None and acc.scroll_to():
                    return [f'Scrolled to {A.quote(args[1])}']
        raise Failure(f'not_done: {A.quote(args[1])} is not in this window. Read the window text to find it.')

    def do_press(self, args, m, app):
        if self.inj is None:
            raise Failure('not_done: Keys need input access. Settings → Desktop integration shows the one-time fix.')
        times = int(args[2]) if len(args) > 2 else 1
        self._front(app.pid)
        self.inj.key(args[1], times)
        return [f'Pressed {A.quote(args[1])} in {app.name}']

    def do_drag(self, args, m, app):
        a, _ = self.resolve(args[1], m)
        b, _ = self.resolve(args[2], m)
        if self.inj is None:
            raise Failure('not_done: Dragging needs input access.')
        self._front(app.pid)
        center = lambda n: (n.frame[0] + n.frame[2] / 2, n.frame[1] + n.frame[3] / 2)
        self.inj.drag(center(a), center(b))
        return [f'Dragged {A.quote(a.label)} to {A.quote(b.label)}']

    def do_type(self, args, m, app):
        node, acc = self.resolve(args[1], m)
        if acc is None or not acc.editable:
            raise Failure('No editable field.')
        old = A._content(acc) or ''
        new = old + args[2] if 'append' in args[3:] else args[2]
        acc.set_text(new)
        receipt = 'verified' if (A._content(acc) or '') == new else 'unverified'
        result = f'{receipt}: Typed into {A.quote(node.label)}.'
        if 'submit' in args[3:]:
            if receipt != 'verified':
                return ['unverified: Typing dispatched; Return not_done because text or intended field focus could not be confirmed. Do not repeat typing.']
            acc.grab_focus()
            self._front(app.pid)
            if self.inj is None:
                raise Failure('not_done: Return needs input access.')
            self.inj.key('return')
            self.sleep(0.3)
            sent = (A._content(acc) or '') != new
            return ['verified: Submitted from ' + A.quote(node.label) + '.' if sent else
                    'unverified: Return dispatched to the intended field. Read the destination before any further action; do not repeat typing or Return.']
        if receipt == 'unverified':
            result += ' Readback unavailable or different; do not repeat typing.'
        return [result]

    def do_type_after(self, args, m, app):
        anchor, text = args[1], args[2]
        for n in m.nodes:
            acc = m.accs.get(n.id)
            if acc is None or not acc.editable:
                continue
            content = A._content(acc) or ''
            if content.count(anchor) == 1:
                position = content.index(anchor) + len(anchor)
                acc.insert_text(position, text)
                ok = (A._content(acc) or '').count(anchor + text) == 1
                return [('verified' if ok else 'unverified') + f': Typed after {A.quote(anchor)}.']
        raise Failure('not_done: The exact anchor was not found once in an editable field. Read the window text and use its exact words.')

    def do_wait(self, args, m, app):
        self.sleep(float(args[1]) if len(args) > 1 else 1)
        return []


def cli(args):
    import sys
    from atticus_shell import input as I
    from atticus_shell.bridge_cli import current_bridge
    from atticus_shell.desktop.env import detect
    from atticus_shell.prefs import Prefs
    env = detect()
    bridge = current_bridge()
    try:
        injector = I.injector_for(env, bridge)
    except Exception:
        injector = None
    hands = Hands(A.atspi_apps, bridge, injector, safe=Prefs().bool('safe'))
    return hands.run(args, sys.stdin, sys.stdout)


def guide_watch(args, bridge=None, sleep=time.sleep, now=time.monotonic, limit=90):
    """--guide-watch '<target window JSON>' <cancel file>: 'changed' once the window's pixels change, 'stopped' on cancel or timeout."""
    from atticus_shell import screen
    from atticus_shell.desktop import ids
    if len(args) != 2:
        return 2
    try:
        target = json.loads(args[0])
    except ValueError:
        return 1
    bridge = bridge or __import__('atticus_shell.bridge_cli', fromlist=['x']).current_bridge()
    wid = ids.resolve(str(target.get('windowID', '')), [w.id for w in bridge.windows()])
    if wid is None:
        return 1

    def sample():
        fd, path = tempfile.mkstemp(suffix='.png')
        os.close(fd)
        try:
            if not bridge.screenshot_window(wid, path):
                return None
            import cairo
            small = screen._scaled(cairo.ImageSurface.create_from_png(path), 64, 40)
            data = bytes(small.get_data())
            return [data[i + 1] for i in range(0, len(data), 4)]
        finally:
            os.unlink(path)
    first, end = sample(), now() + limit
    while now() < end:
        if os.path.exists(args[1]):
            print('stopped')
            return 0
        sleep(0.4)
        current = sample()
        if first is not None and current is not None and sum(abs(a - b) for a, b in zip(first, current)) / len(first) > 4:
            print('changed')
            return 0
    print('stopped')
    return 0
