"""Installs the Atticus browser extension files and the native messaging host manifests (build-linux.sh runs this).

The extension gets a fixed "key" (generated once, kept in Atticus's config folder) so Chrome gives it the same id on
every rebuild, and the host manifests can name exactly that id: no other extension may talk to Atticus.
usage: browser_setup.py --prefix P --home H --config C --python PY
"""
import argparse
import base64
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CHROMIUM_DIRS = ('.config/google-chrome', '.config/google-chrome-beta', '.config/chromium', '.config/BraveSoftware/Brave-Browser',
                 '.config/microsoft-edge', '.config/vivaldi')
FIREFOX_DIRS = ('.mozilla/native-messaging-hosts', '.librewolf/native-messaging-hosts')
NAME = 'app.atticus.web'


def public_key_der(config):
    os.makedirs(config, mode=0o700, exist_ok=True)
    key = os.path.join(config, 'extension-key.pem')
    if not os.path.exists(key):
        subprocess.run(['openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048', '-out', key], check=True, capture_output=True)
        os.chmod(key, 0o600)
    return subprocess.run(['openssl', 'pkey', '-in', key, '-pubout', '-outform', 'DER'], check=True, capture_output=True).stdout


def main(argv):
    a = argparse.ArgumentParser()
    a.add_argument('--prefix', required=True)
    a.add_argument('--home', required=True)
    a.add_argument('--config', required=True)
    a.add_argument('--python', default=sys.executable)
    o = a.parse_args(argv)
    sys.path.insert(0, os.path.join(ROOT, 'linux'))
    from atticus_shell.web_host import extension_id
    ext = os.path.join(o.prefix, 'browser-extension')
    shutil.rmtree(ext, ignore_errors=True)
    shutil.copytree(os.path.join(ROOT, 'linux/browser/extension'), ext)
    with open(os.path.join(ROOT, 'ui/web.js')) as f:
        web = f.read().strip().rstrip(';')
    with open(os.path.join(ext, 'web-inject.js'), 'w') as f:
        f.write('globalThis.__atticusWeb = (' + web + ');\n')
    der = public_key_der(o.config)
    with open(os.path.join(ext, 'manifest.json')) as f:
        manifest = json.load(f)
    manifest['key'] = base64.b64encode(der).decode()
    with open(os.path.join(ext, 'manifest.json'), 'w') as f:
        json.dump(manifest, f, indent=2)
    host = os.path.join(o.prefix, 'atticus-web-host')
    with open(host, 'w') as f:
        f.write(f'#!/bin/sh\n# Started by the browser for the Atticus extension (native messaging).\n'
                f'export PYTHONPATH="{o.prefix}${{PYTHONPATH:+:$PYTHONPATH}}"\nexec "{o.python}" -m atticus_shell.web_host "$@"\n')
    os.chmod(host, 0o755)
    base = {'name': NAME, 'description': 'Atticus browser bridge', 'path': host, 'type': 'stdio'}
    chrome = {**base, 'allowed_origins': [f'chrome-extension://{extension_id(der)}/']}
    firefox = {**base, 'allowed_extensions': ['atticus@atticus.app']}
    for d in CHROMIUM_DIRS:
        folder = os.path.join(o.home, d, 'NativeMessagingHosts')
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, NAME + '.json'), 'w') as f:
            json.dump(chrome, f, indent=2)
    for d in FIREFOX_DIRS:
        folder = os.path.join(o.home, d)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, NAME + '.json'), 'w') as f:
            json.dump(firefox, f, indent=2)
    print(f'Browser extension: {ext} (id {extension_id(der)})')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
