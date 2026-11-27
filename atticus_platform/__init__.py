"""Platform abstraction layer for Atticus (macOS and Linux)."""
import os
import sys


def is_linux():
    override = os.environ.get('ATTICUS_PLATFORM', '').strip().lower()
    if override:
        return override.startswith('linux')
    return sys.platform.startswith('linux')


IS_LINUX = is_linux()
