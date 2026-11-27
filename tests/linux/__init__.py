"""Linux shell tests. Pure-logic tests run on any host; GI-dependent ones skip without PyGObject."""
import os
import sys
import unittest

LINUX = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'linux')
if LINUX not in sys.path:
    sys.path.insert(0, LINUX)

try:
    import gi  # noqa: F401
    HAVE_GI = True
except ImportError:
    HAVE_GI = False


def needs_gi(test):
    """Skip a test or TestCase when PyGObject is not installed (macOS hosts, minimal CI)."""
    return unittest.skipUnless(HAVE_GI, 'needs PyGObject (run in the atticus-linux-test image)')(test)
