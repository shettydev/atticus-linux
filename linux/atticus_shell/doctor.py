"""atticus-shell --doctor [--listen [seconds]]: what works and what to do, on one screen.

--listen records from the microphone Atticus uses and transcribes it with the same engine, so "the mic hears nothing",
"the engine is missing" and "the shortcut never fires" can be told apart.
"""
import json
import os
import shutil
import struct
import sys
import time

OK, BAD = '✓', '✗'
NAMES = {'gnome': 'GNOME', 'kde': 'KDE Plasma', 'hyprland': 'Hyprland', 'sway': 'Sway', 'x11': 'an X11 desktop', 'other': 'this desktop'}


def saved_shortcuts(path=None):
    from atticus_shell.shortcuts import status_path
    try:
        with open(path or status_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def report(env, running, shortcuts, bridge, mics, default_mic, engine, model, client, talk_label):
    """(lines, everything_ok). Each ✗ line says what to do."""
    lines, ok = [], True

    def line(good, text, fix=''):
        nonlocal ok
        ok = ok and good
        lines.append(f'{OK if good else BAD} {text}' + (f'\n    → {fix}' if fix and not good else ''))
    lines.append(f'  Desktop: {NAMES.get(env.compositor, env.compositor)} on {"Wayland" if env.session == "wayland" else env.session.upper()}'
                 + (f' (portals: {", ".join(sorted(env.portals))})' if env.portals else ''))
    line(running, 'Atticus is running' if running else 'Atticus is not running', 'Start it: systemctl --user restart atticus')
    if shortcuts is None:
        line(False, f'The {talk_label} shortcut was never set up',
             'Restart Atticus (systemctl --user restart atticus). Meanwhile, a system keyboard shortcut that runs '
             '~/.local/share/atticus/atticus-shell --trigger talk press works on any desktop.')
    else:
        talk = (shortcuts.get('bound') or {}).get('talk')
        route = shortcuts.get('route', '?')
        line(bool(talk), f'{talk_label} works ({route})' if talk else f'{talk_label} doesn’t work ({route})',
             shortcuts.get('error') or 'A system keyboard shortcut that runs ~/.local/share/atticus/atticus-shell --trigger talk press works on any desktop.')
    good, reason = bridge
    line(good, 'Desktop access (windows, screen, pointer)', reason)
    line(bool(mics), f'Microphone: {default_mic or mics[0].name}' if mics else 'No microphone found',
         'Plug one in, or check Sound in your system settings (pactl list sources short).')
    line(bool(engine), f'Speech engine: sherpa-onnx {engine}' if engine else 'Speech engine (sherpa-onnx) is not installed',
         'Run ./build-linux.sh again in the Atticus folder, then systemctl --user restart atticus.')
    line(bool(model), 'Speech model downloaded' if model else 'Speech model not downloaded',
         'Open Atticus setup (atticus-shell --raise) → Set up Linux access → Speech model → Download.')
    line(bool(client), f'AI: {client}' if client else 'No AI client found',
         'Install Claude Code (https://claude.com/claude-code) and sign in, or pick Devin or Pi in Settings.')
    return lines, ok


def peak(pcm):
    n = len(pcm) // 2
    if not n:
        return 0.0
    samples = struct.unpack(f'<{n}h', pcm[:n * 2])
    return min(1.0, max(abs(s) for s in samples) / 32767)


def listen_verdict(level, text):
    if level < 0.005:
        return f'{BAD} The microphone gave no sound. Check it isn’t muted and is the input in Sound settings.'
    if level < 0.05:
        return f'{BAD} The microphone is very quiet (peak {level:.0%}). Turn its input level up in Sound settings.'
    if not text.strip():
        return f'{BAD} Atticus heard sound but no words (peak {level:.0%}). Speak clearly in English, close to the mic.'
    return f'{OK} Atticus heard: “{text.strip()}”'


def collect():
    from atticus_shell import audio, ipc
    from atticus_shell.bridge_cli import current_bridge
    from atticus_shell.desktop.env import detect
    from atticus_shell.prefs import Prefs
    from atticus_shell.shortcuts import label
    from atticus_shell.speech import stt
    prefs = Prefs()
    env = detect()
    try:
        running = ipc.call('Ping') is not None
    except Exception:
        running = False
    try:
        b = current_bridge()
        bridge = (bool(getattr(b, 'available', True)), getattr(b, 'unavailable_reason', ''))
    except Exception as e:
        bridge = (False, str(e))
    mics = audio.microphones()
    chosen = audio.listen_mic(prefs.str('microphone') or 'automatic')
    try:
        import sherpa_onnx
        engine = getattr(sherpa_onnx, '__version__', 'installed')
    except ImportError:
        engine = None
    models = os.path.join(os.environ.get('ATTICUS_DIR') or os.path.expanduser('~/.config/atticus'), 'models')
    store = stt.ModelStore(models, stt.SHERPA_MODELS)
    model = store._final(stt.SHERPA_MODELS['builtin']) if store.installed('builtin') else None
    client = next(filter(None, (shutil.which(c) for c in ('claude', 'devin', 'pi'))), None)
    return dict(env=env, running=running, shortcuts=saved_shortcuts(), bridge=bridge, mics=mics,
                default_mic=chosen.name if chosen else '', engine=engine, model=model, client=client,
                talk_label=label(prefs.str('linuxHotkey') or 'CTRL+ALT+space'))


def listen(seconds, model_dir):
    """Record `seconds` from Atticus's microphone and transcribe it: (peak level, text)."""
    from atticus_shell import audio
    from atticus_shell.prefs import Prefs
    from atticus_shell.speech import stt
    engine = stt.SherpaStreaming(model_dir)
    pcm, final = bytearray(), []
    engine.start('en', lambda t: None, final.append)
    mic = audio.Mic(os.environ.get('ATTICUS_MIC_SOURCE') or None)   # a GStreamer source string, for testing with a recording
    dev = audio.listen_mic(Prefs().str('microphone') or 'automatic')

    def feed(chunk):
        pcm.extend(chunk)
        engine.feed(chunk)
    mic.start(dev.uid if dev else '', feed)
    time.sleep(seconds)
    mic.stop()
    engine.finish()
    end = time.time() + 5
    while not final and time.time() < end:
        time.sleep(0.05)
    return peak(bytes(pcm)), (final[0] if final else '')


def main(args):
    print('Atticus doctor')
    found = collect()
    lines, ok = report(**found)
    print('\n'.join(lines))
    if '--listen' in args:
        i = args.index('--listen')
        seconds = float(args[i + 1]) if len(args) > i + 1 and args[i + 1].replace('.', '', 1).isdigit() else 4
        if not (found['engine'] and found['model'] and (found['mics'] or os.environ.get('ATTICUS_MIC_SOURCE'))):
            print(f'{BAD} Can’t listen: fix the speech and microphone lines above first.')
            return 1
        print(f'  Listening for {seconds:g} seconds: say something…', flush=True)
        try:
            level, text = listen(seconds, found['model'])
        except Exception as e:
            print(f'{BAD} Listening failed: {e}')
            return 1
        verdict = listen_verdict(level, text)
        print(verdict)
        ok = ok and verdict.startswith(OK)
    else:
        print('  (atticus-shell --doctor --listen also tests your microphone and speech.)')
    return 0 if ok else 1
