"""RoutineWake: when a timer queues a routine, hand it to the agents supervisor (Atticus.swift:5783)."""
import json
import os
import subprocess
import threading


def drain(pop, send):
    for ident in pop():
        send({'action': 'routine', 'id': ident})


def brain_pop():
    from atticus_shell import box
    try:
        r = subprocess.run(box.py_command(['--routine-pop']), capture_output=True, text=True, timeout=10)
        ids = json.loads(r.stdout or '[]')
        return [i for i in ids if isinstance(i, str)]
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


class RoutineWake:
    def __init__(self, folder, send, post):
        import gi
        gi.require_version('Gio', '2.0')
        from gi.repository import Gio
        self.path, self.send, self.post = os.path.join(folder, 'routine-queue.jsonl'), send, post
        os.makedirs(folder, mode=0o700, exist_ok=True)
        if not os.path.exists(self.path):
            os.close(os.open(self.path, os.O_WRONLY | os.O_CREAT, 0o600))
        self.monitor = Gio.File.new_for_path(self.path).monitor_file(Gio.FileMonitorFlags.NONE, None)
        self.monitor.connect('changed', lambda *_: self.changed())
        self.busy = self.pending = False
        self.changed()

    def changed(self):
        self.pending = True
        if self.busy:
            return
        self.busy, self.pending = True, False

        def work():
            ids = brain_pop()

            def done():
                for i in ids:
                    self.send({'action': 'routine', 'id': i})
                self.busy = False
                if self.pending:
                    self.changed()
            self.post(done)
        threading.Thread(target=work, daemon=True).start()
