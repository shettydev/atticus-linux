"""The browser bridge: a native messaging host the Atticus extension starts, relaying to a private unix socket.

Extension <-> host: Chrome/Firefox native messaging frames (4-byte little-endian length + UTF-8 JSON) on stdin/stdout.
Brain <-> host: one JSON request line, one JSON reply line, on $XDG_RUNTIME_DIR/atticus-web.sock (0600, owner checked).
"""
import base64
import hashlib
import json
import os
import socket
import struct
import sys
import tempfile
import threading

MAX = 64 << 20


class HostError(Exception):
    pass


def socket_path():
    return os.path.join(os.environ.get('XDG_RUNTIME_DIR') or tempfile.gettempdir(), 'atticus-web.sock')


def read_frame(stream):
    head = stream.read(4)
    if len(head) < 4:
        return None
    (n,) = struct.unpack('<I', head)
    if n > MAX:
        raise HostError('frame too large')
    body = b''
    while len(body) < n:
        chunk = stream.read(n - len(body))
        if not chunk:
            return None
        body += chunk
    return json.loads(body.decode('utf-8'))


def write_frame(stream, obj):
    data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
    stream.write(struct.pack('<I', len(data)) + data)
    stream.flush()


def extension_id(public_key_der):
    """Chrome's id for an unpacked extension with this manifest "key": sha256's first 32 hex digits, 0-f mapped to a-p."""
    return ''.join(chr(ord('a') + int(c, 16)) for c in hashlib.sha256(public_key_der).hexdigest()[:32])


class Relay:
    def __init__(self, path, ext_in, ext_out):
        self.path, self.ext_in, self.ext_out = path, ext_in, ext_out
        self.hello, self.replies, self.next = None, {}, 0
        self.lock, self.cond = threading.Lock(), threading.Condition()
        self.closed = False

    def _read_extension(self):
        while True:
            try:
                msg = read_frame(self.ext_in)
            except (HostError, ValueError, OSError):
                msg = None
            if msg is None:
                with self.cond:
                    self.closed = True
                    self.cond.notify_all()
                return
            with self.cond:
                if 'hello' in msg:
                    self.hello = msg['hello']
                elif 'id' in msg:
                    self.replies[msg['id']] = msg
                self.cond.notify_all()

    def request(self, req, timeout=20):
        if req.get('op') == 'hello':
            with self.cond:
                self.cond.wait_for(lambda: self.hello is not None or self.closed, timeout=2)
                return {'ok': self.hello is not None, 'value': self.hello}
        with self.lock:
            self.next += 1
            ident = self.next
            write_frame(self.ext_out, {**req, 'id': ident})
        with self.cond:
            if not self.cond.wait_for(lambda: ident in self.replies or self.closed, timeout=timeout):
                return {'ok': False, 'error': 'The browser did not answer.'}
            return self.replies.pop(ident, {'ok': False, 'error': 'The browser closed.'})

    def _client(self, conn):
        with conn:
            data = b''
            while not data.endswith(b'\n') and len(data) < MAX:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk
            try:
                req = json.loads(data.decode('utf-8'))
                reply = self.request(req, timeout=float(req.pop('timeout', 20)) if isinstance(req, dict) else 20)
            except (ValueError, AttributeError) as e:
                reply = {'ok': False, 'error': f'bad request: {e}'}
            conn.sendall((json.dumps(reply, ensure_ascii=False) + '\n').encode('utf-8'))

    def serve(self):
        threading.Thread(target=self._read_extension, daemon=True).start()
        try:
            os.unlink(self.path)
        except OSError:
            pass
        old = os.umask(0o177)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(self.path)
        finally:
            os.umask(old)
        os.chmod(self.path, 0o600)
        server.listen(8)
        server.settimeout(0.5)
        try:
            while not self.closed:
                try:
                    conn, _ = server.accept()
                except socket.timeout:
                    continue
                threading.Thread(target=self._client, args=(conn,), daemon=True).start()
        finally:
            server.close()
            try:
                os.unlink(self.path)
            except OSError:
                pass


def call(op, path=None, timeout=20, **args):
    """One request to the running browser bridge; HostError when it isn't there or refuses."""
    path = path or socket_path()
    try:
        st = os.stat(path)
    except OSError:
        raise HostError('The Atticus browser extension is not connected.')
    if st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise HostError('The browser bridge socket is not private; refusing to use it.')
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout + 2)
    try:
        s.connect(path)
        s.sendall((json.dumps({'op': op, 'timeout': timeout, **args}) + '\n').encode('utf-8'))
        data = b''
        while not data.endswith(b'\n'):
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
    except OSError as e:
        raise HostError(f'The browser bridge did not answer: {e}')
    finally:
        s.close()
    try:
        reply = json.loads(data.decode('utf-8'))
    except ValueError:
        raise HostError('The browser bridge sent a bad reply.')
    if not reply.get('ok'):
        raise HostError(reply.get('error') or 'The browser refused.')
    return reply.get('value')


def main():
    """Started by the browser (native messaging); runs while the extension stays connected."""
    Relay(socket_path(), sys.stdin.buffer, sys.stdout.buffer).serve()
    return 0


if __name__ == '__main__':
    sys.exit(main())
