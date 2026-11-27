"""Installed-package smoke test. Run only in a disposable GNOME session with two displays."""
import os
import subprocess
import sys
import time
from pathlib import Path

APP = 'org.atticus.PlacementFixture'


def fixture():
    import gi
    gi.require_version('Gtk', '4.0')
    from gi.repository import Gtk
    app = Gtk.Application(application_id=APP)

    def activate(application):
        for title in ('Original document', 'Original document copy'):
            window = Gtk.ApplicationWindow(application=application, title=title)
            window.set_default_size(420, 320)
            window.present()

    app.connect('activate', activate)
    return app.run([])


def smoke():
    if os.environ.get('ATTICUS_TEST_GNOME') != '1':
        raise SystemExit('Set ATTICUS_TEST_GNOME=1 inside the disposable GNOME test session.')
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from linux_desktop import Desktop
    desktop = Desktop()
    with subprocess.Popen([sys.executable, __file__, '--fixture']) as process:
        try:
            for _ in range(100):
                snapshot = desktop.call('Snapshot')
                rows = [w for w in snapshot['windows'] if w['wmclass'] == APP]
                if len(rows) == 2 and all(w['frame'][2:] == [420, 320] for w in rows):
                    break
                time.sleep(.1)
            else:
                raise AssertionError(f'Fixture failed to map both windows: {snapshot}')
            assert len(snapshot['monitors']) == 2, snapshot
            copy = next(w for w in rows if w['title'].endswith('copy'))
            for mode, screen in [('left', 'display 2'), ('right', 'primary'), ('full', 'primary'),
                                 ('center', 'display 2'), ('fullscreen', 'display 2'), ('left', 'primary')]:
                subprocess.run(['atticus', '--place', APP, mode, screen, 'Original document'], check=True)
                state = desktop.call('Snapshot')
                other = next(w for w in state['windows'] if w['id'] == copy['id'])
                target = next(w for w in state['windows'] if w['title'] == 'Original document')
                assert other['frame'] == copy['frame'], other
                assert target['monitor'] == (1 if screen == 'display 2' else 0), target
                assert target['fullscreen'] == (mode == 'fullscreen'), target
                x, y, width, height = target['frame']
                if mode == 'right':
                    assert (x, width) == (640, 640), target
                if mode == 'left':
                    assert (x, width) == ((1280, 512) if screen == 'display 2' else (0, 640)), target
                if mode == 'fullscreen':
                    assert (x, y, width, height) == (1280, 0, 1024, 768), target
            rejected = subprocess.run(['atticus', '--place', APP, 'left', 'primary', 'Original'],
                                      text=True, capture_output=True)
            assert rejected.returncode == 1 and 'Multiple' in rejected.stdout, rejected
            subprocess.run(['atticus', '--place', APP, 'left', 'primary', 'Original document',
                            '+', APP, 'right', 'display 2', 'Original document copy'], check=True)
            rows = desktop.call('Snapshot')['windows']
            paired = {w['title']: w['frame'] for w in rows if w['wmclass'] == APP}
            assert paired['Original document'][0] == 0, paired
            assert paired['Original document copy'] == [1792, 0, 512, 768], paired
        finally:
            process.terminate()


if __name__ == '__main__':
    if sys.argv[1:] == ['--fixture']:
        sys.exit(fixture())
    smoke()
