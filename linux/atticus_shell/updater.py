"""Updates from the source checkout build-linux.sh came from (~/.atticus by default), like macOS's Updater."""
import os
import subprocess

NIX_HINT = 'Installed with Nix: update with `nix profile upgrade atticus`, then run `atticus`.'


def _run(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, timeout=600, **kw)


class Updater:
    def __init__(self, source=None, run=_run):
        self.source = source or os.environ.get('ATTICUS_SOURCE') or os.path.expanduser('~/.atticus')
        self.run = run

    def git(self, *args):
        return self.run(['git', '-C', self.source, *args])

    def check(self):
        """'nix' | 'local changes' | 'available' | 'current' | 'offline'."""
        if self.source.startswith('/nix/store/'):
            return 'nix'
        if (self.git('status', '--porcelain').stdout or '').strip():
            return 'local changes'   # someone is working in this checkout: never overwrite it
        if self.git('fetch', '-q').returncode:
            return 'offline'
        local = (self.git('rev-parse', 'HEAD').stdout or '').strip()
        remote = (self.git('rev-parse', '@{u}').stdout or '').strip()
        return 'available' if local and remote and local != remote else 'current'

    def apply(self):
        if self.check() != 'available':
            return False
        if self.git('pull', '-q', '--ff-only').returncode:
            return False
        env = {**os.environ, 'ATTICUS_NO_RESTART': '1'}
        if self.run(['bash', os.path.join(self.source, 'build-linux.sh')], env=env).returncode:
            return False
        self.run(['systemctl', '--user', 'restart', 'atticus.service'])
        return True


def system_info(os_release=None, product=None, desktop=''):
    """What a feedback report may say about this computer: distro, model and desktop, never paths or names."""
    if os_release is None:
        try:
            with open('/etc/os-release') as f:
                os_release = f.read()
        except OSError:
            os_release = ''
    pretty = next((l.split('=', 1)[1].strip().strip('"') for l in os_release.splitlines() if l.startswith('PRETTY_NAME=')), 'Linux')
    if product is None:
        try:
            with open('/sys/class/dmi/id/product_name') as f:
                product = f.read().strip()
        except OSError:
            product = ''
    return {'os': pretty, 'model': product.replace('/', ' '), 'desktop': desktop}
