# Atticus for Linux

Atticus on Linux is the same assistant as on macOS: the same brain (`atticus.py`), the same pages (`ui/`), and the
same `~/.config/atticus/settings.json`. The native app is a Python + GTK 3 shell (`linux/atticus_shell/`) that
reaches your desktop through a small bridge per desktop.

Works on x86_64 and ARM64 (aarch64).

## What you need

- **A desktop session**: GNOME 46+ or KDE Plasma 5.27/6 (Wayland or X11), any X11 window manager, Hyprland or Sway.
  Ubuntu 24.04+, Fedora 40+, Arch and openSUSE Tumbleweed are covered by the package list.
- **Python 3.12+** and the GTK/WebKit/AT-SPI/GStreamer typelibs (`linux/packaging/deps.sh` installs them).
- **An AI client**: [Claude Code](https://claude.com/claude-code) signed in to a Claude plan (or a custom server),
  the Devin CLI, or Pi. Check with `claude --version`.
- About **400 MB** free for the speech model, downloaded on first use.

## Install

From a branch (for example this port's branch, before it's merged):

```bash
curl -fsSL https://raw.githubusercontent.com/shettydev/atticus-linux/claude/atticus-linux/install-linux.sh | ATTICUS_BRANCH=claude/atticus-linux bash
```

Or by hand, from a clone:

```bash
git clone -b claude/atticus-linux https://github.com/shettydev/atticus-linux.git ~/.atticus
cd ~/.atticus
linux/packaging/deps.sh          # system packages; asks for sudo once
./build-linux.sh                 # installs to ~/.local/share/atticus, starts atticus.service
```

`build-linux.sh` also:
- creates `~/.local/share/atticus-venv` (with access to the system's GTK bindings) and installs `sherpa-onnx` there for on-device speech;
- installs a udev rule so Atticus can click and type for you (`/dev/uinput`; sudo asks once). **Log out and back in once** afterwards;
- on GNOME, installs and turns on the **Atticus GNOME Shell extension**. On Wayland, GNOME loads it only after you **log out and back in once**;
- installs the browser extension files and its native messaging host (see *Browser* below);
- adds `~/.config/systemd/user/atticus.service`, so Atticus starts with your session.

Run `./build-linux.sh` again after any change you make, or to update by hand.

## First run

The setup window opens by itself. Its rows on Linux:

| Row | What it checks | Fix |
|---|---|---|
| Accessibility | Assistive technologies are on (apps share their buttons and text) | Turns them on (`toolkit-accessibility`) |
| Screen | The desktop bridge is loaded | GNOME: turns the extension on; then log out and in |
| Automation / input | Atticus can click and type | Run `./build-linux.sh` again, enter your password, log out and in |
| Microphone | A microphone exists | Pick it in Settings → Voice & Sound |
| Speech | The speech model is downloaded | Downloads it (about 300 MB) |
| Chrome | The browser extension is connected | Opens the extension folder (see below) |
| Calendar | Evolution Data Server is available | Opens Online Accounts |

**Shortcuts.** Talk: **Ctrl+Alt+Space** (hold to talk, or switch to press-to-toggle in Settings). Dictate: **Ctrl+Super+Space**.
- GNOME 48+, KDE and Hyprland ask you to confirm them in a system dialog the first time.
- X11 sessions grab them directly.
- GNOME 46/47 on Wayland (Ubuntu 24.04): GNOME has no shortcut portal before 48, so set Atticus to
  **Press to toggle** (Settings → General → Shortcut behavior) and add a custom shortcut in GNOME Settings → Keyboard →
  Custom Shortcuts: command `~/.local/share/atticus/atticus-shell --trigger talk press`, keys Ctrl+Alt+Space.
  Press once to start, once more to send. For dictation, add another with `--trigger dictate press`.
- Sway: add these lines to your config:

  ```
  bindsym --no-repeat Ctrl+Mod1+space exec ~/.local/share/atticus/atticus-shell --trigger talk down
  bindsym --release Ctrl+Mod1+space exec ~/.local/share/atticus/atticus-shell --trigger talk up
  ```

## Browser

Web work ("read this page", "find me a lamp on Amazon") goes through the Atticus extension in your own browser:

1. Open `chrome://extensions` (Chrome, Chromium, Brave, Edge or Vivaldi) and turn on **Developer mode**.
2. Click **Load unpacked** and pick `~/.local/share/atticus/browser-extension`.
3. Restart the browser once. The extension's id is fixed by a key in `~/.config/atticus`, so rebuilding keeps it working.

For Firefox, open `about:debugging` → *This Firefox* → **Load Temporary Add-on**, and pick `manifest.json` in that folder.
Firefox forgets temporary add-ons on restart.

## Try it: a checklist

| Try | Expect |
|---|---|
| Hold Ctrl+Alt+Space, say "what's two plus two", let go | Words show live; the answer appears in the box (and is read aloud if a voice is installed) |
| Type in the box instead (tray menu → Ask Atticus) | Same answer |
| "Open Firefox" / "Open gmail.com" | Opens instantly, with no AI |
| "Firefox on the left" (two screens: "…on my monitor") | The window moves; the box says so |
| "What's on my screen?" | Atticus pictures your screen and answers |
| Hold the shortcut while circling something with the mouse: "what is this?" | It answers about the circled spot |
| "Play Blinding Lights" (Spotify installed) | It starts playing in about a second |
| Ctrl+Super+Space in a text field, talk, let go | Your words land at the cursor |
| "Every weekday at 8, summarize my calendar" | `systemctl --user list-timers` shows an `atticus-routine-…` timer |
| "Find me USB-C hubs" | A background agent starts; the Agents window shows it |

## What's different from macOS (for now)

- **Drawing on screen**, **Meeting Copilot** and the **wake word** are not ported yet.
- The mini screen shows the app icon and title of the window an agent works in, not live frames.
- Clicks in apps that don't publish accessibility info come to the front and use your real pointer, which is put back afterwards.
- "Add to queue" in Spotify isn't available, since Spotify's Linux app doesn't share its queue.
- Calendar works with Evolution Data Server calendars (GNOME Online Accounts: Google, Nextcloud, Microsoft). KDE-only (Akonadi) calendars aren't reached yet.
- On KDE and Sway, giving back a copied item restores its main type only (for example, rich text comes back as plain text).
- Some Settings controls (Whisper downloads, the meetings archive, memory import) show "not on Linux yet".

Everything that's left is listed with its reasons in the port plan, `docs/superpowers/plans/2026-11-27-linux-port.md`.

## When something's off

- Logs: `journalctl --user -u atticus -f` and `~/.config/atticus/app.err`.
- Restart: `systemctl --user restart atticus`.
- Run in the foreground to watch it: `systemctl --user stop atticus; ~/.local/share/atticus/atticus-shell`.
- Ask the helper directly: `~/.local/share/atticus/atticus-shell --bridge screens`, `… --bridge windows`, `… --hands see firefox`.
- GNOME says *Log out and back in once to finish setup*: the Shell extension is installed but not loaded yet.

## Tests

On any machine with Docker (no desktop needed):

```bash
docker build -f linux/packaging/test.Dockerfile -t atticus-linux-test .
docker run --rm -v "$PWD":/src atticus-linux-test sh linux/packaging/gui-tests.sh       # the Linux suite (GTK, WebKit, AT-SPI under Xvfb)
docker run --rm -v "$PWD":/src atticus-linux-test sh linux/packaging/x11-live.sh        # live X11: window moves, key grabs, clipboard
docker build -f linux/packaging/gnome-test.Dockerfile -t atticus-gnome-test .
docker run --rm -v "$PWD":/src atticus-gnome-test sh linux/packaging/gnome-live.sh      # headless GNOME Shell with the extension
```

Add `--platform linux/amd64` (or `linux/arm64`) to test the other architecture. On a Linux desktop, `./check --all` runs
the host-safe suite, including `tests/linux/`.

## Uninstall

Run these in a terminal, from your desktop session. They're safe to run even if some parts were never set up
("not loaded" or "does not exist" messages are fine).

**1. Stop Atticus and everything it started**

```bash
systemctl --user disable --now atticus.service
find ~/.config/systemd/user -maxdepth 1 -name 'atticus-routine-*.timer' -exec sh -c 'systemctl --user disable --now "$(basename "$1")"' _ {} \;
pkill -f 'atticus_shell' || true
pkill -f '[.]local/share/atticus/atticus[.]py' || true
gnome-extensions disable atticus@atticus.app 2>/dev/null || true
```

**2. Remove the app**

```bash
rm -f ~/.config/systemd/user/atticus.service
find ~/.config/systemd/user -maxdepth 1 -name 'atticus-routine-*' -delete
systemctl --user daemon-reload
rm -rf ~/.local/share/atticus ~/.local/share/atticus-venv ~/.local/share/gnome-shell/extensions/atticus@atticus.app
rm -f ~/.local/share/applications/atticus.desktop
rm -rf "${XDG_CACHE_HOME:-$HOME/.cache}/atticus"
find ~/.config ~/.mozilla ~/.librewolf -name app.atticus.web.json -delete 2>/dev/null || true
```

The last line removes the files that let your browsers talk to Atticus.

**3. Remove the click-and-type permission** (asks for your password)

```bash
sudo rm -f /etc/udev/rules.d/60-atticus-uinput.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --name-match=uinput
```

**4. Remove your data and the source** (optional, and can't be undone)

This deletes your settings, saved rules, learned shortcuts, dictation history, chats, downloaded speech models and the
browser extension's key. Skip the first line if you might reinstall.

```bash
rm -rf ~/.config/atticus
rm -rf ~/.atticus        # the downloaded source; check first if you changed any code there
```

**5. By hand, if you set these up**

- In your browser, open `chrome://extensions` and remove **Atticus**. In Firefox, restarting drops a temporary add-on.
- Delete any custom shortcuts you added (GNOME Settings → Keyboard → Custom Shortcuts), or the `bindsym … atticus-shell` lines in your Sway config.
- Atticus turns on your desktop's accessibility support. If nothing else needs it (screen readers do), turn it off:
  `gsettings set org.gnome.desktop.interface toolkit-accessibility false`
- Log out and back in so GNOME unloads the extension and the input permission is dropped.

The system packages from `linux/packaging/deps.sh` (GTK, WebKitGTK, Tesseract and so on) are left installed, because
other programs may use them. `linux/packaging/deps.sh --print apt` (or `dnf`, `pacman`, `zypper`) lists them if you want to
review them. Claude Code, Devin and Pi are separate programs and are not touched.
