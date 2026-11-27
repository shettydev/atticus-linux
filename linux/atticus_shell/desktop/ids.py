"""Window ids the brain can hold as integers (it does int(windowID)): X11/Hyprland hex and GNOME/Sway numbers map to
their value; anything else (KWin's UUIDs) to a stable 31-bit checksum. resolve() maps back against the live windows."""
import re
import zlib


def public_id(bridge_id):
    s = str(bridge_id)
    if re.fullmatch(r'0x[0-9a-fA-F]+', s):
        return str(int(s, 16))
    if s.isdigit():
        return s
    return str(zlib.crc32(s.encode()) & 0x7fffffff)


def resolve(public, bridge_ids):
    return next((b for b in bridge_ids if public_id(b) == str(public)), None)
