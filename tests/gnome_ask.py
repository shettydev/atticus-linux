"""Exercise a typed request and inspect its answer in the installed WebKitGTK page."""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if os.environ.get('ATTICUS_TEST_GNOME') != '1':
    raise SystemExit('Run in the disposable GNOME smoke session.')

sys.path.insert(0, '/usr/lib/atticus')
from linux_app import Application, GLib


def check_overlay(requests):
    app = Application(application_id='org.atticus.TestAsk')
    errors = []
    index = 0
    submitted = False
    deadline = time.monotonic() + 30

    def poll():
        window = app.get_active_window()
        if time.monotonic() > deadline:
            errors.append('Timed out waiting for the installed ask box')
            app.quit()
            return False
        if not window:
            return True
        script = """(() => {
            if (!window.ui || document.activeElement?.id !== 'ask') return '';
            return document.querySelector('.turn:last-child .a')?.textContent || 'ready';
        })()"""

        def read(web, result):
            nonlocal index, submitted
            try:
                value = web.evaluate_javascript_finish(result).to_string()
                question, receipt = requests[index]
                if value and not submitted:
                    submitted = True
                    import json
                    window.js(f"""document.getElementById('ask').value = {json.dumps(question)};
                        document.getElementById('ask').dispatchEvent(new KeyboardEvent('keydown',
                            {{key:'Enter', bubbles:true}}))""")
                elif value == receipt:
                    index += 1
                    if index == len(requests):
                        app.quit()
                        return
                    submitted = False
                elif value and value != 'ready' and not receipt.startswith(value):
                    raise AssertionError(value)
            except Exception as error:
                errors.append(str(error))
                app.quit()
                return
            GLib.timeout_add(100, poll)

        window.web.evaluate_javascript(script, -1, None, None, None, read)
        return False

    GLib.timeout_add(100, poll)
    app.run([])
    assert index == len(requests) and not errors, errors


with tempfile.TemporaryDirectory() as directory:
    data = Path(directory)
    apps = data / 'applications'
    apps.mkdir()
    marker = data / 'opened'
    (apps / 'org.atticus.TestLauncher.desktop').write_text(
        '[Desktop Entry]\nType=Application\nName=Atticus Test Launcher\n'
        f'Exec=/usr/bin/touch {marker}\nTerminal=false\n')
    os.environ['XDG_DATA_HOME'] = directory
    from linux_desktop import Desktop
    desktop = Desktop()
    fixture_id = 'org.atticus.PlacementFixture'
    with subprocess.Popen([sys.executable, str(Path(__file__).with_name('gnome_placement.py')), '--fixture']) as fixture:
        try:
            for _ in range(100):
                snapshot = desktop.call('Snapshot')
                if len([w for w in snapshot['windows'] if w['wmclass'] == fixture_id and w['frame'][2:] == [420, 320]]) == 2:
                    break
                time.sleep(.1)
            else:
                raise AssertionError('Placement fixture did not start')
            display = next(m['name'] for m in snapshot['monitors'] if m['index'] == snapshot['current'])
            check_overlay([('open Atticus Test Launcher', 'Launched Atticus Test Launcher.'),
                           (f'move {fixture_id} to the left', f'{fixture_id} window -> left of {display}')])
            assert any(w['wmclass'] == fixture_id and w['frame'][2] == 640 for w in desktop.call('Snapshot')['windows'])
        finally:
            fixture.terminate()
    for attempt in range(50):
        if marker.exists():
            break
        time.sleep(.1)
    assert marker.exists(), 'The app did not launch'
    rejected = subprocess.run(['atticus', '--ask', 'move Firefox to the left'], capture_output=True, text=True)
    assert rejected.returncode == 1, rejected
