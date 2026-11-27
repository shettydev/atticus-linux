# Atticus for Linux: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Atticus on Linux with every feature of the macOS 2.0.2 app (talk box, instant commands, learning, screen reading, pointing, drawing, hands, web work, dictation, wake word, agents, routines, watch, Meeting Copilot, calendar, Spotify, Settings, onboarding, updater). Each one runs on Linux's own mechanisms, and wherever a desktop cannot do something, Atticus says so in plain words.

**Architecture:** The Python brain (`atticus.py`) stays one shared file. A new import seam (`brain_linux.py`) swaps its macOS-only functions and prompt lines for Linux ones. `Atticus.swift` is re-implemented as a Python + PyGObject shell (`linux/atticus_shell/`). The shell serves the same helper CLI contract the brain already calls (`$ATTICUS_APP --look|--hands|--text|--type|…`), hosts the unchanged `ui/*.html` pages in WebKitGTK (which offers the same `window.webkit.messageHandlers` bridge as WKWebView), and reaches the desktop through a per-compositor **bridge**: GNOME Shell extension, KWin script, Hyprland/Sway IPC, X11/EWMH.

**Tech Stack:** Python ≥ 3.12, PyGObject with GTK 3 + WebKit2GTK 4.1 + gtk-layer-shell + AyatanaAppIndicator3 + Atspi 2 + GStreamer (pipewiresrc) + EDataServer/ECal/EBook, python-evdev (uinput), python-xlib, sherpa-onnx (streaming STT + keyword spotting), whisper.cpp (optional STT), Piper (TTS, `spd-say` fallback), Tesseract (OCR), xdg-desktop-portal (GlobalShortcuts, Screenshot, ScreenCast), systemd --user, a Manifest V3 browser extension with a native messaging host.

**Spec:** No separate spec. This plan treats `README.md`, `docs/GUIDE.md` and the behaviour of `Atticus.swift` / `atticus.py` at commit `03260f7` as the spec. Whenever a task says "same as macOS", the Swift line it cites is the reference.

---

## Decisions (made here; change them before Task 1 if you disagree)

| # | Decision | Why |
|---|---|---|
| D1 | The shell is **Python + PyGObject on GTK 3 / WebKit2GTK 4.1**, not GTK 4, Electron or Tauri. | `ui/*.html` posts to `window.webkit.messageHandlers.{c,h,st,ob,meeting,agents,drawingMath}`, and WebKitGTK exposes that exact API, so the pages ship unmodified. GTK 4 removed keep-above, type hints and accept-focus, which a never-focus overlay needs on X11/XWayland. gtk-layer-shell is mature on GTK 3. Python matches the brain, so one interpreter and one test runner cover both. |
| D2 | **Same repo; macOS stays byte-identical in behaviour.** Linux code lives in `linux/` and `brain_linux.py`. `atticus.py` gets only a platform constant and one `install()` call. | Keeps upstream merges cheap. The ~60 existing tests that read `Atticus.swift` stay valid. |
| D3 | **Compositor bridge**: one Python interface, five backends. GNOME gets a bundled Shell extension (D-Bus), KDE a KWin script plus `org.kde.KWin.ScreenShot2`, then Hyprland (`hyprctl` + grim), Sway (`swaymsg` + grim), and X11 (EWMH + XComposite). Portals are the last fallback. | Wayland has no generic API for moving other apps' windows, reading the global pointer or snapshotting a window. Only the compositor can, and these backends give dialog-free screenshots and per-window mini screens. |
| D4 | Speech: **sherpa-onnx streaming Zipformer** gives live words (the default "Built-in" model, which replaces Apple speech). **whisper.cpp** stays the "Whisper" choice, exactly as in Settings today. The wake word uses **sherpa-onnx keyword spotting**, which takes any typed word. | On-device, streaming partials, custom wake words with no training, and audio never leaves the machine (same privacy promise). |
| D5 | Browser work goes through an **Atticus browser extension** (Chrome/Chromium/Brave/Edge/Firefox) plus a **native messaging host**, reusing `ui/web.js` as the injected script. | macOS uses AppleScript `execute javascript`, which has no Linux equivalent. CDP cannot attach to the user's signed-in default profile (Chrome ≥ 136), but an extension can. |
| D6 | Native actions try **AT-SPI first** (works behind, never moves the pointer). Next is **uinput**, an absolute pointer plus keyboard (foreground; the pointer is put back afterwards). XTest is used only on X11. | Mirrors "AX first; Quartz only when an app has no accessibility action" (`Atticus.swift:8225`). |
| D7 | **systemd --user** replaces launchd: `atticus.service` (Restart=on-failure) for login and keep-alive, `atticus-routine-<id>.timer` for routines. | Same semantics as `KeepAlive.SuccessfulExit=false` and launchd wake jobs. |
| D8 | Default shortcuts: talk **Ctrl+Alt+Space**; dictation **Ctrl+Super+Space** (there is no fn key on Linux); hand off **Ctrl+Alt+Return**; Meeting answer **Ctrl+Return**. On portal desktops the user confirms these in the system dialog. | `fn` never reaches userspace on most Linux keyboards. |
| D9 | Install path mirrors macOS: `install-linux.sh` → `~/.atticus` git checkout → `build-linux.sh` → `~/.local/share/atticus/` plus the systemd unit. Nix flake gains `x86_64-linux`/`aarch64-linux`. No Flatpak or Snap. | Sandboxed formats block AT-SPI, uinput, host CLIs (`claude`, `devin`, `pi`) and compositor D-Bus. |

**Support tiers:** Tier 1 = GNOME 47+ Wayland and KDE Plasma 6 Wayland (Ubuntu 24.04+, Fedora 41+). Tier 2 = X11 (any EWMH window manager), Hyprland, Sway. Tier 3 = any other Wayland compositor: portal-only, with window placement, mini screen and pointer trail turned off and labelled so.

## Global Constraints

- Python floor **3.12**. GI namespaces: `Gtk 3.0`, `WebKit2 4.1`, `GtkLayerShell 0.1`, `AyatanaAppIndicator3 0.1`, `Atspi 2.0`, `Gst 1.0`, `ECal 2.0`, `EBook 1.2`.
- Settings stay in `$ATTICUS_DIR/settings.json` (default `~/.config/atticus/settings.json`), mode `0600`, same keys as `Prefs.defaults` (`Atticus.swift:188`). Linux-only keys are prefixed `linux` (e.g. `linuxHotkey`, `linuxDictateHotkey`) and macOS never reads them.
- The brain's line protocol is unchanged: `· note`, `▸ step`, `✓ answer`, with U+2028 for in-answer breaks (`atticus.py:4658`).
- The Linux system prompt must stay **≤ 10,000 chars in source and ≤ 10,050 with the installed helper path** (GUIDE "Budget"; enforced by `tests/test_step_budget.py`).
- AI asks keep the median under **$0.002 and 6 s** (`atticus.py:4347`). Nothing that waits may call AI.
- Audio never leaves the machine. Screenshots and mini screens never go to telemetry. Telemetry sends no task text.
- Safe-mode deny list gains `mkfs`, `fdisk`, `parted`, `wipefs`, `systemctl poweroff|reboot|suspend`, `loginctl terminate-session` on top of `DENIED` (`atticus.py:680`).
- The helper CLI's stdout/exit codes match the Swift handlers at `Atticus.swift:6344` (`--look`), `6438` (`--text`), `9748–9845` (`--system-ui`, `--raise`, `--calendar`, `--hands`, `--step-screen`, `--guide-watch`, `--user-app`, `--draw-target`, `--window-target`, `--target-app`, `--selftest`, `--type`).
- `./check` stays "the one gate". On Linux it also runs `tests/linux/`. On macOS, `tests/linux/` tests needing GI skip themselves.
- UI copy in Linux-only Settings and onboarding rows goes through `ui/i18n.js` in all five languages (en, it, es, fr, de).

## Review Focus

1. **The bridge is missing, or installed but not yet loaded.** On GNOME Wayland, a freshly installed extension only loads after logging out and back in. Expected: Atticus starts, talk/answer works, and window moves, mini screens and the pointer trail reply "Log out and back in once to finish setup" instead of crashing or hanging. Pinned in Task 7 (`test_gnome_backend_unloaded_reports_relogin`).
2. **Hold-to-talk gets stuck.** X11 auto-repeat sends fake release/press pairs, and portal `Deactivated` can be lost when the session locks. Expected: releasing always sends exactly once, and a missing release ends listening on the 60 s toggle-review rule. Pinned in Task 11 (`test_autorepeat_pairs_do_not_release`, `test_lock_without_deactivated_ends_hold`).
3. **Text lands in the wrong place.** The focused field changes between capture and insert (dictation, `--type`). Expected: the same rule as macOS, "can't confirm → Saved to History", and never typing twice. Pinned in Task 23 (`test_focus_changed_saves_to_history`).
4. **Coordinates are off under fractional scaling or mixed-DPI monitors.** Drawing marks, clicks and gesture crops miss by the scale factor. Expected: every backend reports logical (layout) pixels plus a per-output scale, and pictures map back exactly. Pinned in Task 6 (`test_bridge_contract_logical_coordinates`) and Task 18 (`test_crop_maps_through_scale_1_5`).
5. **The clipboard borrow loses the user's clipboard on Wayland.** `wl-copy` forks, clipboard managers re-own the selection, and multi-MIME content gets flattened to text. Expected: every MIME type the user had is restored after the paste. Pinned in Task 21 (`test_borrow_restores_all_mime_types`).

---

## Feature parity matrix

Every user-visible feature, the Linux mechanism behind it, the task that delivers it, and the known gap.

| Feature (README/GUIDE) | macOS mechanism | Linux mechanism | Task | Gap on Linux |
|---|---|---|---|---|
| Hold/toggle shortcut, Type right away, Esc | Carbon hotkey, NSEvent monitors | Portal GlobalShortcuts; XGrabKey on X11; `atticus-shell --trigger` for Sway `bindsym --release` | 11 | Portal desktops: the user approves the binding once |
| Live words while talking | SpeechAnalyzer | sherpa-onnx streaming; whisper.cpp final pass when "Whisper" is chosen | 13 | none |
| Box overlay that never takes focus, follow pointer, minimize bar, mini screen | NSPanel + WKWebView | GTK3 window: layer-shell OVERLAY (KDE/wlroots) or XWayland keep-above + accept-focus=false (GNOME, X11) | 14, 15, 28 | none on Tier 1/2 |
| Answers read aloud, sounds | `say`, system sounds | Piper → `spd-say`; bundled CC0 `.oga` sounds via GStreamer | 16 | Voice names differ from `say` voices |
| Menu bar icon | NSStatusItem | StatusNotifierItem (AyatanaAppIndicator3) | 17 | Vanilla GNOME needs the AppIndicator extension; onboarding offers it |
| Instant commands, learned shortcuts, `when I say…` | brain + osascript | brain + `brain_linux` app/window functions | 3, 24 | none |
| Screen questions (`--look`), self-invisible pictures | ScreenCaptureKit | Bridge screenshot (GNOME ext, KWin ScreenShot2, grim, XGetImage); hides Atticus windows for one frame | 18 | Tier 3: portal dialog on first use |
| Window text (`--window`, `--text`), selection | AX + DOM + Vision OCR | AT-SPI Text/Document + extension DOM + Tesseract; PRIMARY selection | 19, 25 | Apps without a11y fall back to OCR |
| Point / circle while talking; Point at your screen | NSEvent mouse polling + TalkPen | Bridge pointer stream (GNOME ext / KWin / hyprctl / XQueryPointer); Sway: full-screen motion-capture surface while held | 20 | none |
| Arrange windows (`--place`, Spaces, laptop/monitor) | AX + System Events | Bridge `move_resize`/`fullscreen`/`move_to_workspace`; laptop = `eDP*`/`LVDS*` output | 6–9, 24 | Tier 3: unavailable, says so |
| Hands (see/click/menu/scroll/press/drag/type/type-after/wait) behind the user | AX + CGEvent postToPid | AT-SPI actions/EditableText; uinput fallback (foreground, pointer restored) | 21, 22 | Coordinate clicks in a11y-less apps come to the front |
| `--type`, Dictation insert, clipboard borrow | AX value / paste | AT-SPI insert → clipboard borrow + uinput Ctrl+V (Ctrl+Shift+V in terminals) | 21, 23, 26 | none |
| Web work in the signed-in browser, agent's own window, background tabs | AppleScript `execute javascript` | Extension + native messaging host running `ui/web.js` | 25 | The user installs the extension once (onboarding step) |
| Find files by contents | `mdfind` | `localsearch`/`tracker3` → `baloosearch6` → `plocate` + `rg` | 24 | none |
| Dictate anywhere (verbatim / clean up, lock, history, dictionary) | Swift + brain | Shell port of the dictation flow + unchanged brain cleanup | 26 | Default key is Ctrl+Super+Space, not fn |
| Wake word | Apple speech | sherpa-onnx KWS on `wakeWordName` | 35 | No orange mic dot (the tray icon shows a mic badge instead) |
| Background agents, chips, done card, Needs you, steer, named agents, chats | AgentPanels + brain | Same `agents.html` in GTK windows; brain unchanged | 27 | none |
| Mini screen of an agent's window | SCStream | Bridge window snapshots at 2 fps; fallback icon + title (existing no-permission path) | 28 | Tier 3 fallback only |
| Routines | launchd wake jobs | systemd user timers | 29 | none |
| Watch for me | `--text` polling | Same, through AT-SPI `--text` | 29 | none |
| Draw on screen, teach me, explain-and-draw, math | DrawingPanel + WKWebView | Layer-shell/X11 overlay with input region = marks only | 30 | none |
| Meeting Copilot (mic + system audio, Ctrl+Return, archive, recap) | SCStream audio + AVCapture | PipeWire mic + default sink monitor via GStreamer; call apps via `pactl -f json list source-outputs` | 31 | none |
| Calendar (list/add/move/delete, nudges, meeting context) | EventKit | Evolution Data Server (GNOME/GOA); CalDAV backend for KDE/others | 32 | none |
| Contacts, "About you" import | Contacts.app | EDS address book; `getent passwd` GECOS | 33 | none |
| Spotify play/queue | AppleScript + AX | MPRIS `OpenUri` + metadata verify; queue via AT-SPI on Spotify, Web API fallback | 34 | Queue needs a11y on, or a Spotify login |
| Quiet other audio while listening | CoreAudio | `wpctl`/`pactl` sink volume snapshot/restore | 12 | none |
| Hide when sharing | `NSWindow.sharingType` | Detect active portal ScreenCast (PipeWire `Video/Source` node from xdg-desktop-portal) → hide box | 18 | Hides rather than masks |
| Onboarding, permission help | macOS TCC panes | Linux checks: a11y bus, uinput access, portal shortcuts, bridge loaded, browser extension, calendar source | 36 | none |
| Settings with search, all pages | SettingsWindow | Same `settings.html`; macOS-only rows hidden, Linux rows added | 37 | none |
| Auto-update, Report Feedback, anonymous usage | Updater, GitHub draft, PostHog | Same flow; `os-release` + DMI product name instead of macOS/Mac model | 38 | none |
| Install, login start, Nix | build.sh, LaunchAgent, flake (darwin) | build-linux.sh, systemd unit, flake (linux) | 5, 39 | none |
| Local models / Claude / Devin / Pi | CLI clients | Same CLIs on Linux; Claude Code sandbox uses bubblewrap | 3, 5 | `bwrap` must be installed |

---

## File structure

```
atticus.py                         + LINUX constant, XDG cache path in the sh trampoline, one brain_linux.install(globals()) call
brain_linux.py                     Linux replacements for macOS-only brain functions + Linux prompt lines (one responsibility: the seam)
linux/atticus_shell/
  __main__.py                      entry: no args → GUI app; --flags → cli.main
  cli.py                           helper CLI contract (the 15 flags), same stdout/exit codes as Swift
  prefs.py                         settings.json load/set/migrate, DEFAULTS mirrored from Swift Prefs
  ipc.py                           D-Bus service app.atticus.Shell: Raise, Trigger, Ping; single instance
  desktop/env.py                   DesktopEnv detection
  desktop/bridge.py                Bridge protocol + dataclasses (Output, Window, Frame)
  desktop/x11.py | gnome.py | kwin.py | hyprland.py | sway.py | portal.py   backends
  desktop/gnome-extension/atticus@atticus.app/{metadata.json,extension.js}
  desktop/kwin/atticus.js
  hotkeys.py                       HoldMachine (pure) + PortalShortcuts / X11Grab / ExternalTrigger sources
  audio.py                         Mic (GStreamer), devices, Ducker, SinkMonitor
  speech/stt.py                    SttEngine protocol, SherpaStreaming, WhisperCpp, ModelStore
  speech/wake.py                   WakeWord (sherpa KWS)
  speech/tts.py                    Speaker (Piper, spd-say), sounds
  web_bridge.py                    WebKit view factory, message handlers, call_js, page errors
  windows.py                       overlay window factory (layer-shell / X11 hints / input region)
  box.py                           the ask box (port of class App)
  pen.py                           TalkPen trail + PointerView
  screen.py                        look(), step_screen(), sharing_active(), OCR
  a11y.py                          AT-SPI map/capture/actions (port of enum Hands)
  input.py                         Uinput/XTest injector, ClipboardBorrow
  selection.py                     TextSelection capture (AT-SPI + PRIMARY)
  dictation.py                     dictation pill + flow
  agents.py                        AgentPanels, ShelfPanel, done cards, MiniScreen
  drawing.py                       ScreenDrawing, callouts, pointAt/unpoint, guide-watch
  meeting.py                       MeetingAudio, MeetingCopilot window
  calendar.py                      CalendarTool: EdsCalendar, CaldavCalendar
  tray.py, onboarding.py, settings.py, permissions.py, updater.py, notify.py, apps.py
linux/browser/extension/{manifest.json,background.js}   MV3; injects ui/web.js
linux/browser/host.py              native messaging host ↔ unix socket $XDG_RUNTIME_DIR/atticus-web.sock
linux/packaging/{atticus.service,atticus.desktop,60-atticus-uinput.rules,routine.timer.in,routine.service.in,deps.sh}
build-linux.sh, install-linux.sh
ui/sounds/{start,release,done,stop}.oga   CC0
tests/linux/__init__.py, tests/linux/fakes.py (FakeBridge, FakeAtspi, FakeBus, FakeRun), tests/linux/test_*.py
```

---

## Milestone 0: Foundation

### Task 1: Desktop detection

**Files:** Create `linux/atticus_shell/desktop/env.py`, `tests/linux/__init__.py`, `tests/linux/test_env.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) class DesktopEnv: session: str  # 'wayland'|'x11'|'tty'`, `compositor: str  # 'gnome'|'kde'|'hyprland'|'sway'|'x11'|'other'`, `portals: frozenset[str]`; `detect(environ=os.environ, probe=probe_portals) -> DesktopEnv`; `probe_portals() -> set[str]` (reads `org.freedesktop.portal.Desktop` interface names over D-Bus).

- [ ] **Step 1: Write failing tests**
```python
def test_gnome_wayland(): assert detect({'XDG_SESSION_TYPE':'wayland','XDG_CURRENT_DESKTOP':'ubuntu:GNOME'}, lambda: set()).compositor == 'gnome'
def test_hyprland_by_signature(): assert detect({'XDG_SESSION_TYPE':'wayland','HYPRLAND_INSTANCE_SIGNATURE':'x'}, set).compositor == 'hyprland'
def test_sway_by_socket(): assert detect({'XDG_SESSION_TYPE':'wayland','SWAYSOCK':'/run/s'}, set).compositor == 'sway'
def test_kde(): assert detect({'XDG_SESSION_TYPE':'wayland','XDG_CURRENT_DESKTOP':'KDE'}, set).compositor == 'kde'
def test_gnome_on_x11_uses_x11(): assert detect({'XDG_SESSION_TYPE':'x11','XDG_CURRENT_DESKTOP':'GNOME'}, set).compositor == 'x11'
def test_unknown_wayland_is_other(): assert detect({'XDG_SESSION_TYPE':'wayland','XDG_CURRENT_DESKTOP':'niri'}, set).compositor == 'other'
```
`tests/linux/__init__.py` puts `linux/` on `sys.path` and exposes `needs_gi(test)`, which skips the test when `gi` is missing.
- [ ] **Step 2:** `python3 -m unittest tests.linux.test_env -v` → FAIL (module missing)
- [ ] **Step 3:** Implement `detect`. Wayland is also inferred from `WAYLAND_DISPLAY` when `XDG_SESSION_TYPE` is unset.
- [ ] **Step 4:** Re-run → PASS
- [ ] **Step 5:** `git commit -m "linux: detect session and compositor"`

### Task 2: Prefs parity

**Files:** Create `linux/atticus_shell/prefs.py`, `tests/linux/test_prefs.py`

**Interfaces:**
- Produces: `DEFAULTS: dict`, `LINUX_DEFAULTS = {'linuxHotkey': 'CTRL+ALT+space', 'linuxDictateHotkey': 'CTRL+LOGO+space', 'linuxHandoffHotkey': 'CTRL+ALT+Return', 'linuxMeetingHotkey': 'CTRL+Return', 'hotkeyLabel': 'Ctrl+Alt+Space', 'dictateHotkeyLabel': 'Ctrl+Super+Space'}`; `class Prefs: path: str; d: dict; load() -> None; set(k, v) -> bool; bool(k) -> bool; str(k) -> str; num(k) -> float; ui_language -> str`.

- [ ] **Step 1: Write failing tests**
  - `test_defaults_cover_swift_keys`: parse the `Prefs.defaults` literal in `Atticus.swift` (keys only, regex `"(\w+)":`) and assert `set(swift_keys) <= set(DEFAULTS)`.
  - `test_routing_migration`: `{'model':'sonnet'}` → `routing == 'capable'`; `{'model':'haiku'}` → `'balanced'`; `{'model':'haiku','escalate':False}` → `'cheapest'` (same as `Atticus.swift:205-208`).
  - `test_old_box_size_reset`: saved `boxW: 460` loads as `DEFAULTS['boxW']`.
  - `test_set_writes_0600_and_rolls_back_on_error`: an unwritable dir → `set()` returns `False` and the old value stays.
  - `test_linux_keys_present`: every `LINUX_DEFAULTS` key appears in `Prefs().d`.
- [ ] **Step 2:** Run → FAIL
- [ ] **Step 3:** Implement. Retired keys are still readable (`Atticus.swift:197`); atomic write via temp file + `os.replace`.
- [ ] **Step 4:** Run → PASS
- [ ] **Step 5:** Commit `linux: settings.json parity with macOS Prefs`

### Task 3: Brain platform seam + Linux prompt

**Files:** Modify `atticus.py` (line 2 trampoline cache path; after imports: `LINUX = sys.platform.startswith('linux')`; just before `def run_main` at `atticus.py:9646`: `if LINUX: import brain_linux; brain_linux.install(globals())`). Create `brain_linux.py`, `tests/linux/test_brain_seam.py`, `tests/test_linux_seam_ratchet.py`.

**Interfaces:**
- Produces: `brain_linux.install(ns: dict) -> None`. It rebinds the names in `brain_linux.REPLACED: tuple[str, ...]` and rewrites the constants `SYSTEM`, `SAFE_OUT`, `DENIED`/`DENY`, `RUNS`, `APP_DIRS`, `NEEDS`, `SHOT`, `APP` (default `~/.local/share/atticus/atticus-shell`). For each function name in `REPLACED`, brain_linux defines the same name and signature. This task stubs each one by raising `NotImplementedError('linux: <name> lands in Task N')`; later tasks fill them in.
- `REPLACED` (audit list; the ratchet test keeps it complete): `osa, osa_quote, front, screens, place, _place, place_many, keep_behind, background_tab, remember_background_tab, show, play, queue, _spotify_control, _spotify_wait_control, _spotify_verified, on_spotify, installed_apps, app_named, folder_named, proc_of, app_id, app_running, quit_app, app_up, launches_program, finder_alias, browser_terms, browser_adapter, agent_window, close_agent_window, work_page, running_app, menu_bar_icons, menu_bar_click, routine_install, routine_repair, routine_remove, notify, mac_profile, contacts, need, automation_hint`.

- [ ] **Step 1: Write failing tests**
  - `test_linux_prompt_has_no_mac_tools`: after `install(ns)` on a copy of `vars(atticus)`, `ns['SYSTEM']` contains none of `osascript`, `AppleScript`, `mdfind`, `defaults or`, `open -a`, `Mac helper`, and does contain `localsearch` and `xdg-open`.
  - `test_linux_prompt_budget`: `len(ns['SYSTEM']) <= 10000` and `len(ns['SYSTEM'].replace(shlex.quote(atticus.ME), shlex.quote('/home/u/.local/share/atticus/atticus.py'))) <= 10050`.
  - `test_deny_list_adds_linux_disk_tools`: `{'mkfs','fdisk','parted','wipefs'} <= set(ns['DENIED'])`.
  - `test_install_rebinds_every_replaced_name`: every name in `REPLACED` is the `brain_linux` object after install.
  - `tests/test_linux_seam_ratchet.py::test_mac_call_sites_are_replaced`: parse `atticus.py` with `ast` and find every function whose body contains a string literal matching `osascript|tell application|System Events|mdfind|launchctl|LaunchAgents|pbpaste|qlmanage|sips`. Assert each is in `REPLACED` or in an explicit `PORTABLE_ON_LINUX` allowlist with a one-line reason. (This ratchet runs on every platform.)
  - `test_mac_unchanged`: with `sys.platform` patched to `darwin`, importing `atticus` leaves `SYSTEM` equal to the module's original string.
- [ ] **Step 2:** Run → FAIL
- [ ] **Step 3:** Implement the seam and the stubs. Write the Linux `SYSTEM` by editing a copy of the macOS one line by line (Mac helper → Linux desktop helper; `mdfind` → `localsearch`/`rg`; "shell, defaults or AppleScript" → "shell, gsettings/kwriteconfig6"; `open -a Atticus` → `atticus-shell --settings`). Trampoline cache dir: `~/Library/Caches/Atticus` on darwin, `${XDG_CACHE_HOME:-~/.cache}/atticus` otherwise.
- [ ] **Step 4:** `python3 -m unittest tests.linux.test_brain_seam tests.test_linux_seam_ratchet tests.test_step_budget -v` → PASS; `./check --all` on macOS still PASS
- [ ] **Step 5:** Commit `brain: Linux seam with prompt and deny list`

### Task 4: Helper CLI contract skeleton

**Files:** Create `linux/atticus_shell/__main__.py`, `linux/atticus_shell/cli.py`, `tests/linux/test_cli_contract.py`

**Interfaces:**
- Produces: `cli.main(argv: list[str]) -> int`; `cli.HANDLERS: dict[str, Callable[[list[str]], int]]` keyed `--look --text --system-ui --raise --calendar --hands --step-screen --guide-watch --user-app --draw-target --window-target --target-app --selftest --type --trigger --settings --bridge`. `--bridge <method> '<json>'` (Linux-only) runs one `Bridge` method and prints its JSON result, so the brain can reach the bridge from its own process. `--trigger <talk|dictate|handoff|meeting> <down|up>` and `--settings` are Linux-only, for Sway bindings and the Settings prompt line.
- Consumes: nothing yet. Handlers return `1` with stderr `linux: <flag> lands in Task N` until filled in.

- [ ] **Step 1:** `test_every_brain_call_has_a_handler`: regex `\[APP, '(--[a-z-]+)'` over `atticus.py` → each flag is in `HANDLERS`. `test_unknown_flag_exits_2`. `test_no_args_starts_gui` (patch `box.run_app`, assert called).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement the dispatcher. **Step 4:** PASS. **Step 5:** Commit `linux: helper CLI contract skeleton`

### Task 5: Build, install, service, CI

**Files:** Create `build-linux.sh`, `install-linux.sh`, `linux/packaging/{atticus.service,atticus.desktop,60-atticus-uinput.rules,deps.sh}`. Modify `check` (add `tests/linux/test_*.py` to `SAFE` on Linux; `bash -n` the new scripts), `.github/workflows/checks.yml` (install the GI packages via `deps.sh --ci`, then `./check --all`). Create `tests/linux/test_packaging.py`.

**Interfaces:**
- Produces: install layout `~/.local/share/atticus/{atticus.py,brain_linux.py,atticus_shell/,ui/,atticus-shell}`, where `atticus-shell` is an `sh` launcher exec'ing `python3 -m atticus_shell` with `ATTICUS_RESOURCES_DIR` set; unit `~/.config/systemd/user/atticus.service`; `deps.sh` maps apt/dnf/pacman/zypper package names for every GI namespace in Global Constraints plus `tesseract`, `wl-clipboard`, `xclip`, `bubblewrap`, `pipewire-utils`.
- `build-linux.sh` mirrors `build.sh`: validate `VERSION`; stage into `mktemp -d`; swap atomically and restore the previous build on failure; write `VERSION`/commit/source dir into `atticus-shell` env; install the udev rule with `sudo` only if `/dev/uinput` is not writable (print why first); `systemctl --user daemon-reload && systemctl --user enable --now atticus.service` unless `ATTICUS_NO_RESTART`. GNOME: copy the extension to `~/.local/share/gnome-shell/extensions/` and `gnome-extensions enable`. KDE: `.desktop` gets `X-KDE-DBUS-Restricted-Interfaces=org.kde.KWin.ScreenShot2`.

- [ ] **Step 1:** Tests: `test_service_restarts_only_on_failure` (unit has `Restart=on-failure`, `WantedBy=graphical-session.target`); `test_build_rejects_bad_version` (run with a temp `VERSION=2.x` → exit 1, message `Invalid VERSION`); `test_deps_cover_every_gi_namespace`; `test_udev_rule_grants_uaccess` (`KERNEL=="uinput", TAG+="uaccess"`).
- [ ] **Step 2:** FAIL → **Step 3:** implement → **Step 4:** PASS, plus CI green on `ubuntu-latest`
- [ ] **Step 5:** Commit `linux: build, install, systemd service, CI`

### Task 6: Bridge interface + X11 backend

**Files:** Create `linux/atticus_shell/desktop/bridge.py`, `desktop/x11.py`, `desktop/portal.py`, `tests/linux/fakes.py`, `tests/linux/test_bridge_contract.py`, `tests/linux/test_x11.py`

**Interfaces:**
- Produces:
```python
@dataclass class Output:  name: str; x: int; y: int; width: int; height: int; scale: float; builtin: bool; work_area: tuple[int,int,int,int]
@dataclass class Window:  id: str; app_id: str; pid: int; title: str; x: int; y: int; width: int; height: int; output: str; workspace: str; minimized: bool; fullscreen: bool; focused: bool
class Bridge(Protocol):
    available: bool; unavailable_reason: str          # e.g. "Log out and back in once to finish setup."
    def outputs(self) -> list[Output]
    def windows(self) -> list[Window]                  # stacking order, top first
    def focused(self) -> Window | None
    def pointer(self) -> tuple[float, float]           # logical layout coordinates
    def move_resize(self, win_id: str, x: int, y: int, w: int, h: int) -> bool
    def set_fullscreen(self, win_id: str, on: bool) -> bool
    def activate(self, win_id: str) -> bool
    def lower(self, win_id: str) -> bool
    def screenshot_output(self, name: str, path: str) -> bool    # PNG, physical pixels
    def screenshot_window(self, win_id: str, path: str) -> bool  # works covered where the backend can
    def keep_above(self, win_id: str, on: bool) -> bool
def bridge_for(env: DesktopEnv) -> Bridge
```
- `fakes.FakeBridge(outputs, windows)` records calls; it is used by every later shell test.
- `builtin` is true for connector names matching `^(eDP|LVDS|DSI)`. This drives `--place … laptop|monitor`.

- [ ] **Step 1:** `test_bridge_contract_logical_coordinates`: a parametrized contract suite every backend must pass against its fake transport. Output at scale 1.5 → `pointer()` and `Window` geometry are logical; `screenshot_output` image size = logical × scale. Also `test_builtin_detection`, `test_x11_move_uses_net_moveresize`, `test_x11_screenshot_covered_window_via_xcomposite`.
- [ ] **Step 2–4:** FAIL → implement X11 with python-xlib (EWMH `_NET_CLIENT_LIST_STACKING`, `_NET_MOVERESIZE_WINDOW`, `_NET_WM_STATE_FULLSCREEN`, XRandR outputs, `XQueryPointer`, XComposite `NameWindowPixmap`) and the portal backend (Screenshot only, `available=False` for windows) → PASS
- [ ] **Step 5:** Commit `linux: compositor bridge + X11 backend`

### Task 7: GNOME Shell extension + backend

**Files:** Create `desktop/gnome-extension/atticus@atticus.app/{metadata.json,extension.js}`, `desktop/gnome.py`, `tests/linux/test_gnome.py`

**Interfaces:**
- The extension exports D-Bus `app.atticus.ShellBridge` at `/app/atticus/ShellBridge` with methods mirroring `Bridge` (JSON strings in and out), plus `PointerStream(hz) -> handle` emitting a `Pointer(x, y, t)` signal and `StopPointer(handle)`. Windows whose `wm_class == 'atticus'` get `make_above()`, sticky and skip-taskbar applied by the extension. `metadata.json` `shell-version`: `["47","48","49","50"]`.
- `gnome.GnomeBridge(bus)` implements `Bridge`. When the D-Bus name is absent but the extension directory exists → `available=False` with `unavailable_reason = "Log out and back in once to finish setup."`

- [ ] **Step 1:** `test_gnome_backend_unloaded_reports_relogin`, the shared contract suite over `FakeBus`, and `test_atticus_windows_kept_above` (the extension source contains `make_above` for wm_class `atticus`). CI: `gnome-shell --headless --wayland --virtual-monitor 1280x800` smoke job that enables the extension and calls `Outputs`.
- [ ] **Step 2–4:** FAIL → implement (`global.get_window_actors()`, `Meta.Window.move_resize_frame`, `Shell.Screenshot` for outputs and windows, `global.get_pointer()` on a `GLib.timeout_add`) → PASS
- [ ] **Step 5:** Commit `linux: GNOME Shell bridge extension`

### Task 8: KWin backend

**Files:** Create `desktop/kwin/atticus.js`, `desktop/kwin.py`, `tests/linux/test_kwin.py`

**Interfaces:** `KwinBridge(bus)` implements `Bridge`. It loads `atticus.js` via `org.kde.KWin /Scripting loadScript` once per session; the script answers over `callDBus` to `app.atticus.Shell` (Task 10). Screenshots use `org.kde.KWin.ScreenShot2.CaptureScreen/CaptureWindow` (fd → raw ARGB → PNG). Pointer comes from `workspace.cursorPos`.

- [ ] **Step 1:** Contract suite over `FakeBus`; `test_screenshot2_unauthorized_reports_desktop_file` (error `NoAuthorized` → `unavailable_reason` names the `.desktop` key).
- [ ] **Step 2–5:** FAIL → implement → PASS → commit `linux: KWin bridge`

### Task 9: Hyprland + Sway backends

**Files:** Create `desktop/hyprland.py`, `desktop/sway.py`, `tests/linux/test_wlroots.py`

**Interfaces:** `HyprlandBridge(run=subprocess.run)` uses `hyprctl -j clients|monitors|cursorpos|activewindow` and `hyprctl dispatch movewindowpixel/resizewindowpixel/fullscreen/focuswindow`. `SwayBridge(run)` uses `swaymsg -t get_tree|get_outputs` and `[con_id=N] move position / resize set / fullscreen`. Sway `pointer()` raises `PointerUnavailable`; the pen in Task 20 then switches to its capture surface. Screenshots come from `grim -o <output>`, and windows from `grim -g "x,y wxh"` (visible part only, flagged `covered_ok=False`).

- [ ] **Step 1:** Contract suite with recorded `hyprctl`/`swaymsg` JSON fixtures in `tests/linux/fixtures/`; `test_sway_pointer_unavailable`.
- [ ] **Step 2–5:** FAIL → implement → PASS → commit `linux: Hyprland and Sway bridges`

---

## Milestone 1: The talk loop (hold, speak, see the answer)

### Task 10: Single-instance D-Bus service

**Files:** Create `linux/atticus_shell/ipc.py`, `tests/linux/test_ipc.py`

**Interfaces:** Bus name `app.atticus.Shell`, object `/app/atticus/Shell`, methods `Raise(user: b)`, `Trigger(which: s, edge: s)`, `Ping() -> s` (version), `HideOverlays(ms: u)` (hide every Atticus window for `ms`, then restore; used by `--look`), `BridgeEvent(json: s)` (KWin script callbacks). `ipc.serve(app) -> bool`: returns False if the name is already owned, in which case `__main__` calls `Raise` on the running copy and exits 0. `cli --raise [user]` calls `Raise` (replaces `DistributedNotificationCenter`, `Atticus.swift:9751`).

- [ ] Tests: `test_second_instance_raises_first`, `test_trigger_routes_to_hotkeys`, `test_raise_user_flag`. FAIL → implement with `Gio.bus_own_name` → PASS → commit `linux: single-instance D-Bus service`

### Task 11: Hotkeys

**Files:** Create `linux/atticus_shell/hotkeys.py`, `tests/linux/test_hotkeys.py`

**Interfaces:**
- `class HoldMachine` (pure): `__init__(mode: str  # 'hold'|'toggle', now=time.monotonic)`, `down() -> list[str]`, `up() -> list[str]`, `tick() -> list[str]`, `session_locked() -> list[str]`. It emits events from `{'start','send','cancel','review','stop_voice'}`; the same rules as the GUIDE "How the shortcut and the box work" (a toggle reaches review after 60 s).
- Sources: `PortalShortcuts(bus, bindings: dict[str,str], on_edge)` (`CreateSession` → `BindShortcuts` with `preferred_trigger` from prefs, `Activated`/`Deactivated` → `on_edge(name, 'down'|'up')`); `X11Grab(display, bindings, on_edge)` (XGrabKey + `XkbSetDetectableAutoRepeat`); `ExternalTrigger` (fed by `ipc.Trigger`). `pick_source(env) -> str`: portal if `'GlobalShortcuts' in env.portals`, x11 if session x11, else external, and onboarding prints the Sway `bindsym` lines.
- [ ] **Step 1:** `test_autorepeat_pairs_do_not_release` (down, up+down within 30 ms → no `send`); `test_hold_sends_once_on_release`; `test_toggle_review_after_60s`; `test_lock_without_deactivated_ends_hold` (`session_locked()` → `cancel` if nothing was heard, `review` if words exist); `test_portal_binds_all_four` (talk, dictate, handoff, meeting).
- [ ] **Step 2–5:** FAIL → implement → PASS → commit `linux: global shortcuts (portal, X11, external)`

### Task 12: Audio in, devices, ducking

**Files:** Create `linux/atticus_shell/audio.py`, `tests/linux/test_audio.py`

**Interfaces:** `microphones() -> list[MicrophoneDevice(uid, name)]` (from `pactl -f json list sources`, monitors excluded); `choose_mic(preferred: str, devices, default_uid) -> MicrophoneDevice | None` (same rule as `chooseMic`, `Atticus.swift:286`); `class Mic: start(uid, on_pcm: Callable[[bytes], None], rate=16000) / stop()` via GStreamer `pipewiresrc|pulsesrc ! audioconvert ! audioresample ! appsink`; `class Ducker: duck(level: float) / restore()` (snapshot of sink volumes via `wpctl`, restore exactly); `class SinkMonitor` (default sink `.monitor`, for Task 31).
- [ ] Tests: `test_choose_mic_falls_back_to_default`, `test_monitors_not_listed`, `test_ducker_restores_snapshot_even_if_user_changed_other_sink`. FAIL → implement → PASS → commit

### Task 13: Speech to text

**Files:** Create `linux/atticus_shell/speech/stt.py`, `tests/linux/test_stt.py`

**Interfaces:** `class SttEngine(Protocol): start(lang: str, on_partial: Callable[[str], None], on_final: Callable[[str], None]) -> None; feed(pcm: bytes) -> None; finish() -> None; cancel() -> None`. `SherpaStreaming(model_dir)` and `WhisperCpp(binary, model_path)`. Whisper reuses the macOS logic: live partials by re-running on a growing window, plus a final pass, with `transcriptKey`/`stitchTranscript` ported 1:1 (`Atticus.swift:884-899`). `ModelStore(root=$ATTICUS_DIR/models)`: `ensure(name) -> Path` with resumable download and sha256 check. Settings "Speech model" values: `builtin` (sherpa) | `whisper-turbo` | `whisper-large-v3` | `whisper-small`.
- [ ] Tests: `test_stitch_transcript_matches_swift` (cases copied from `tests/test_voice_stream.py`), `test_final_confirms_before_tools` (no `on_final` until `finish()`), `test_model_sha_mismatch_deletes_partial`. `--selftest` (Task 4 handler) runs the stitch cases. FAIL → implement → PASS → commit

### Task 14: Web bridge + overlay windows

**Files:** Create `linux/atticus_shell/web_bridge.py`, `linux/atticus_shell/windows.py`, `tests/linux/test_web_bridge.py`

**Interfaces:**
- `make_view(page: str, handlers: dict[str, Callable[[object], None]]) -> WebKit2.WebView`. It registers each name via `UserContentManager.register_script_message_handler`, adds the `atticusError` page-error handler (`Atticus.swift:957`), loads `ui/<page>.html` from `ATTICUS_RESOURCES_DIR`, and gives a transparent background. `call_js(view, fn: str, args: list) -> None` uses the same JSON encoding as `callJS` (`Atticus.swift:950`).
- `overlay(kind: str  # 'box'|'pill'|'chip'|'drawing'|'pen'|'card', output: Output, focusable: bool) -> Gtk.Window`. On layer-shell compositors: `GtkLayerShell` layer OVERLAY, keyboard mode ON_DEMAND only when `focusable`. On X11/XWayland: `set_keep_above`, `set_accept_focus(focusable)`, type hint NOTIFICATION/UTILITY, skip taskbar/pager, wm_class `atticus`. `set_input_region(win, rects: list[tuple[int,int,int,int]]) -> None` (an empty list means click-through everywhere).
- GNOME Wayland: `__main__` sets `GDK_BACKEND=x11` for the overlay process.
- [ ] Tests (`needs_gi`, run under `xvfb-run` and `weston --backend=headless` in CI): `test_message_handler_round_trip` (a page posts `{"x":1}` → handler receives a dict); `test_overlay_not_focusable` (`get_accept_focus() is False`); `test_input_region_empty_passes_clicks`; `test_every_ui_page_loads_without_page_errors` (all `ui/*.html`). FAIL → implement → PASS → commit

### Task 15: The ask box

**Files:** Create `linux/atticus_shell/box.py`, `tests/linux/test_box.py`

**Interfaces:** `class Box` ports `class App` (`Atticus.swift:3000-5265`): `listen()`, `send(text, request: dict)`, `cancel()`, `on_line(line: str)` (`·`/`▸`/`✓` protocol), `minimize()`, `restore()`, `place(output: Output)` (corner, follow pointer, `boxWidth` rule `Atticus.swift:2968`), typing mode (first key stops listening; Return sends; Esc cancels; ↑ recalls). It handles every `c`/`h` message `ui.html` posts. The brain runs via `py(args, extra) -> subprocess.Popen` with env `ATTICUS_APP`, `ATTICUS_RESOURCES_DIR`, PATH + `~/.local/bin`. `run_app() -> int` boots Gtk, prefs, ipc, hotkeys, tray and box.
- [ ] **Step 1:** `test_every_ui_message_kind_handled`: extract `postMessage({kind:'…'` / `type:` values posted to `c` and `h` from `ui/ui.html` and assert each has a handler (the box keeps a `HANDLED` set). Also `test_line_protocol_u2028_breaks`, `test_hotkey_during_answer_stops_it`, `test_esc_cancels_without_send`, `test_box_stays_inside_work_area_on_resize`.
- [ ] **Step 2–4:** FAIL → implement → PASS. Manual: hold Ctrl+Alt+Space, say "what is two plus two", see "4".
- [ ] **Step 5:** Commit `linux: ask box end to end`

### Task 16: Read aloud and sounds

**Files:** Create `linux/atticus_shell/speech/tts.py`, `ui/sounds/{start,release,done,stop}.oga`, `tests/linux/test_tts.py`

**Interfaces:** `class Speaker: say(text: str, rate_wpm: float) -> None; stop() -> None; voices() -> list[str]`. Piper when `$ATTICUS_DIR/voices/*.onnx` exists, else `spd-say -r` (rate mapped from `voiceRate` wpm), else silent with a one-time "Install a voice in Settings" note. `spoken()`, `SpeechSentences` and `readAloudText` are ported verbatim from `Atticus.swift:714-778`. `play_sound(name: str, volume: float)` uses GStreamer `playbin`.
- [ ] Tests: `test_spoken_matches_swift_cases` (reuse the `tests/test_spoken.py` cases), `test_stop_kills_playback_process`, `test_rate_mapping_196_wpm`. FAIL → implement → PASS → commit

### Task 17: Tray

**Files:** Create `linux/atticus_shell/tray.py`, `tests/linux/test_tray.py`

**Interfaces:** `Tray(app)` builds an AyatanaAppIndicator3 menu with the same items as `MenuBar` (`Atticus.swift:6653`): Ask, Pause/Resume, Settings…, Agents…, Report Feedback…, Leave a tip, Quit. Icon states: idle, listening (mic badge, which stands in for the orange dot), paused. `Tray.available` is False when no StatusNotifierWatcher is on the bus, and onboarding then offers the AppIndicator extension.
- [ ] Tests: `test_menu_matches_mac_items`, `test_no_watcher_reports_unavailable`. FAIL → implement → PASS → commit

---

## Milestone 2: Eyes

### Task 18: Screens and pictures

**Files:** Create `linux/atticus_shell/screen.py`; fill `cli` `--look`, `--step-screen`, `--draw-target`. Create `tests/linux/test_screen.py`.

**Interfaces:** `look(index: int, path: str, window_id: str | None, pointer: str | None) -> int` with the same args/stdout as `Atticus.swift:6344`. It hides every Atticus overlay for one frame (D-Bus `HideOverlays(120)` from Task 10, so it works from the separate `--look` process), captures through `bridge.screenshot_output`, then labels and scales to the brain's expected size. `step_screen() -> list[int]` returns a fingerprint with the same length/semantics as `Hands.fingerprint(screen:true)`. `sharing_active() -> bool` scans `pw-dump` for a `Video/Source` node owned by `xdg-desktop-portal`. Box hides when `hideWhenSharing`.
- [ ] Tests: `test_crop_maps_through_scale_1_5`, `test_look_hides_overlays_then_restores`, `test_sharing_detected_from_pw_dump_fixture`, `test_draw_target_json_shape_matches_swift` (keys from `Atticus.swift:9783-9802`). FAIL → implement → PASS → commit

### Task 19: AT-SPI reader, selection, OCR

**Files:** Create `linux/atticus_shell/a11y.py` (reader half), `linux/atticus_shell/selection.py`; fill `cli` `--text`, `--window-target`, `--target-app`, `--user-app`, `--system-ui`. Create `tests/linux/test_a11y_read.py`, `tests/linux/test_selection.py`.

**Interfaces:**
- `a11y.capture(app: str) -> Map`: the same `Map` fields as `Hands.Map` (`pid, app, window, windowFrame, nodes, texts, page, focused, scroll, windowID, focusIdentity`). Nodes come from an `Atspi` tree walk (roles → the macOS role names the brain already parses), capped like Swift. `window_text(app, words) -> str` with `--text` output parity. `user_app() -> str` = the focused window's app name, excluding Atticus.
- `ocr(png_path) -> str` via `tesseract … tsv`, used for `--window --ocr`.
- `selection.capture(enabled: bool, allow_empty: bool) -> TextSelection | None`: AT-SPI `Text.get_selection` + caret, falling back to the PRIMARY selection (`wl-paste --primary` / `xclip -o -selection primary`). Same fields as `struct TextSelection` (`Atticus.swift:1051`).
- `ensure_a11y_on() -> bool` sets `org.gnome.desktop.interface toolkit-accessibility true` (or `org.a11y.Status IsEnabled` on the a11y bus) so Chromium/Electron apps expose trees.
- [ ] Tests over `FakeAtspi` trees recorded from GNOME Text Editor, Firefox and Chromium (fixtures as JSON): `test_map_fields_match_swift`, `test_text_paging_offsets`, `test_selection_prefers_atspi_over_primary`, `test_user_app_excludes_atticus`. FAIL → implement → PASS → commit

### Task 20: Pointer trail and Point at your screen

**Files:** Create `linux/atticus_shell/pen.py`, `tests/linux/test_pen.py`

**Interfaces:** `class TalkPen: start(output) / stop() -> list[tuple[float,float,float]]` (samples for the brain's `read_gesture`, max `MAX_SAMPLES`). Sources: `bridge.pointer` polled at 60 Hz, the GNOME `PointerStream` signal, or on Sway a full-screen transparent layer surface that captures motion only while the hotkey is held. `class PointerView` is the crosshair "Point at your screen" mode, with marks drawn in an overlay whose input region covers the box and cards only.
- [ ] Tests: `test_samples_feed_read_gesture` (feed a recorded circle → `atticus.read_gesture(...).kind == 'loop'`), `test_sway_uses_capture_surface`, `test_point_mode_clicks_reach_box`. FAIL → implement → PASS → commit

---

## Milestone 3: Hands

### Task 21: Synthetic input + clipboard borrow

**Files:** Create `linux/atticus_shell/input.py`, `tests/linux/test_input.py`

**Interfaces:** `class Injector(Protocol): click(x, y, button='left', count=1) -> bool; drag(a, b) -> bool; scroll(dx, dy) -> bool; key(combo: str, times=1) -> bool; type_text(text: str) -> bool`. `UinputInjector` uses an absolute device spanning the logical layout from `bridge.outputs()` and restores the pointer afterwards. `XTestInjector` is for X11. `class ClipboardBorrow: __enter__ snapshots every offered MIME type (wl-paste -l / xclip -t TARGETS); put(text); __exit__ restores all types` (port of `borrowPasteboard`, `Atticus.swift:1041`). `paste_combo(app_id) -> str` returns `ctrl+shift+v` for known terminals, else `ctrl+v`.
- [ ] Tests: `test_borrow_restores_all_mime_types` (fake clipboard with `image/png` + `text/plain`), `test_absolute_mapping_two_outputs_mixed_scale`, `test_terminal_paste_combo`, `test_pointer_restored_after_click`. FAIL → implement (python-evdev `UInput`) → PASS → commit

### Task 22: Hands verbs

**Files:** Extend `linux/atticus_shell/a11y.py` (actions half); fill `cli --hands`; `--guide-watch`. Create `tests/linux/test_hands.py`.

**Interfaces:** `a11y.cli(args: list[str]) -> int` mirrors `Hands.cli` (`Atticus.swift:9398`) for `see|click|menu|scroll|scroll-to|press|drag|type|type-after|wait`, with the same receipts, errors ("Use see|click|…"), risk labels and `ATTICUS_HANDS_STATE` map pinning. Action order per verb: AT-SPI `Action.do_action` / `EditableText.insert_text` / `Component.scroll_to` first; `Injector` only when the node has no action, in which case the window comes to the front first and focus is restored after. `guide_watch(target: DrawingWindow, cancel: str) -> bool` polls the map fingerprint (`Atticus.swift:9777`).
- [ ] Tests: port the host-safe receipt cases from `tests/test_hands.py`, running them through the Python `cli` with `FakeAtspi`; `test_risky_click_requires_authorize`, `test_menu_path_via_atspi_menubar`, `test_type_after_exact_anchor`. FAIL → implement → PASS → commit

### Task 23: `--type` and text insertion

**Files:** Fill `cli --type`. Create `linux/atticus_shell/insert.py`, `tests/linux/test_insert.py`.

**Interfaces:** `insert(text: str, selection: TextSelection, mapped: Map | None) -> str` returns `'typed' | 'pasted' | 'saved'`. It follows the same destination checks as `Atticus.swift:9836-9845`: require a verified empty caret, re-capture and compare `focusIdentity`, try AT-SPI insert then ClipboardBorrow + paste, verify the text landed, else `'saved'`. Same stdout `not_done: …` messages.
- [ ] Tests: `test_focus_changed_saves_to_history`, `test_never_replaces_selected_text`, `test_unverified_insert_not_retried`. FAIL → implement → PASS → commit

### Task 24: Brain OS functions (apps, windows, files, notify, instant commands)

**Files:** Modify `brain_linux.py`; create `linux/atticus_shell/apps.py`, `linux/atticus_shell/notify.py`; tests `tests/linux/test_brain_apps.py`, `tests/linux/test_brain_place.py`, `tests/linux/test_brain_fast.py`.

**Interfaces:**
- `apps.installed() -> dict[str, DesktopApp(id, name, exec, wm_class, icon)]` from XDG `applications/` dirs, including Flatpak and Snap exports, cached 60 s like `_APPS_CACHE`. `apps.launch(id, uris=()) -> bool` (`gio launch`). `apps.quit(id) -> bool` (bridge close → SIGTERM after 3 s).
- In `brain_linux`: `installed_apps, app_named, app_id, proc_of, app_running, quit_app, app_up` over `apps`; `front, screens, place, _place, place_many, keep_behind, show` over `$ATTICUS_APP` bridge calls (the Task 4 `--bridge <method> <json>` flag), with the same receipts the `PLACED` regex accepts (`atticus.py:1600`). The `--bridge` CLI flag comes from Task 4; `notify` over `notify.send(title, body, actions)`.
- File search line in the Linux prompt: `localsearch search -f "<words>"`, falling back to `baloosearch6`, then `plocate -i` + `rg -l`, with `SAFE_OUT` updated to match.
- `fast()` Linux verbs: open/quit/switch app, open site, volume (`wpctl`), dark mode (`gsettings color-scheme` / `plasma-apply-colorscheme`), lock (`loginctl lock-session`), Do Not Disturb (`gsettings show-banners`). Same `fast()` contract (exit 1 if not instant).
- [ ] Tests: `test_place_receipt_matches_PLACED` (FakeBridge two outputs → "Firefox window -> left of monitor"), `test_laptop_means_builtin_output`, `test_open_spotify_is_instant_no_ai`, `test_flatpak_app_discovered`, `test_learned_shortcut_replays_on_linux` (`learn` then `replay` with FakeBridge). FAIL → implement → PASS → commit

---

## Milestone 4: Web

### Task 25: Browser extension + native host

**Files:** Create `linux/browser/extension/{manifest.json,background.js}`, `linux/browser/host.py`, `linux/packaging/app.atticus.web.json` (native messaging manifest for Chrome, Chromium, Brave, Edge and Firefox paths). Modify `brain_linux.py` (`browser_terms`, `browser_adapter` → `'extension'`, `agent_window`, `close_agent_window`, `work_page`, `background_tab`, `remember_background_tab`, `running_app`). Tests `tests/linux/test_web_host.py`.

**Interfaces:** Host ↔ brain: newline JSON over `$XDG_RUNTIME_DIR/atticus-web.sock` (mode 0600). Requests: `{"op":"tabs"}`, `{"op":"run","tab":id,"args":[cmd,n,text]}` (executes `ui/web.js` with the same `(src)(cmd, n, text)` call as the AppleScript path), `{"op":"open","url":…,"window":"agent"|"user","active":false}`, `{"op":"close_window","id":…}`. Replies are `{"ok":true,"value":…}` or `{"ok":false,"error":…}`. The extension uses `chrome.scripting.executeScript` with `world: 'ISOLATED'` and `tabs`/`windows` APIs. The agent's own window is created `focused:false` and closed when the task ends.
- [ ] Tests: `test_host_rejects_non_owner_socket`, `test_run_passes_web_js_args`, `test_agent_window_closed_on_task_end`, `test_no_extension_says_install_once` (the brain's reply names Settings → Browser). Existing `tests/test_page_ready.py` cases run against the `'extension'` adapter with a fake host. FAIL → implement → PASS → commit

---

## Milestone 5: Dictation

### Task 26: Dictate anywhere

**Files:** Create `linux/atticus_shell/dictation.py`, `tests/linux/test_dictation_shell.py`

**Interfaces:** `class Dictation: down() / up() / double_tap_lock() / cancel()` drives the pill overlay (sound wave, orange lock ring, green check, "Nothing selected to insert — saved to History"). The `DictationData` archive (`Atticus.swift:23`) is ported to the same JSON file and fields, so history and stats carry over. Cleanup calls the unchanged brain (`atticus.py --dictate-cleanup` / `--dictate-cleanup-wait`), and words land through `insert.insert` (Task 23). Highlighted text + Clean up tidies the selection in place.
- [ ] Tests: `test_double_tap_locks_then_press_inserts`, `test_verbatim_skips_ai`, `test_history_off_keeps_words_in_pill`, `test_archive_json_compatible_with_swift` (field names parsed from `struct Entry`/`Archive`). FAIL → implement → PASS → commit

---

## Milestone 6: Agents, routines, watch

### Task 27: Agent panels

**Files:** Create `linux/atticus_shell/agents.py` (panels half), `tests/linux/test_agents_shell.py`

**Interfaces:** `AgentPanels` ports `Atticus.swift:5288-5782`: chips on the pointer's output, hover → live steps, done card shown once typing stops (idle = no key events for 1.5 s, via the hotkey source's key stream on X11 or AT-SPI keystroke listener), "Needs you" choices, permission asks on the chip. During `sharing_active()` or a meeting, a notification is sent instead. The handoff shortcut (`linuxHandoffHotkey`) moves the running ask to an agent. Handles every message `ui/agents.html` posts.
- [ ] Tests: `test_every_agents_message_handled` (same extraction method as Task 15), `test_done_card_waits_for_typing_pause`, `test_sharing_sends_notification_instead`. FAIL → implement → PASS → commit

### Task 28: Mini screen

**Files:** Extend `agents.py` (`class MiniScreen`), box hook. Create `tests/linux/test_miniscreen.py`.

**Interfaces:** `MiniScreen.follow(win_id: str, fps=2) -> None; stop()`: snapshots via `bridge.screenshot_window` into the card. When the bridge can't (Tier 3, or Sway with a covered window), it falls back to the existing icon + title card. Stops streaming when the task ends, and is never saved or sent.
- [ ] Tests: `test_fallback_icon_title_when_unavailable`, `test_stops_on_task_end`, `test_frames_never_written_outside_tmp`. FAIL → implement → PASS → commit

### Task 29: Routines and watch

**Files:** Modify `brain_linux.py` (`routine_install`, `routine_repair`, `routine_remove`). Create `linux/packaging/{routine.timer.in,routine.service.in}`, `tests/linux/test_routines_linux.py`.

**Interfaces:** `routine_install(row)` writes `~/.config/systemd/user/atticus-routine-<id>.{timer,service}`: `OnCalendar=*-*-* HH:MM:00` (daily), `Mon..Fri *-*-* HH:MM:00` (weekdays), or `OnActiveSec`/`OnUnitActiveSec=<secs>` (interval); `Persistent=false`, so a restart never repeats a run. Then `systemctl --user daemon-reload && enable --now`. The service runs `atticus.py --routine fire <id>` (existing `routine_fire`). `routine_repair` re-creates missing units from `routines()`. Watch needs no change beyond `--text` (Task 19).
- [ ] Tests: `test_weekdays_oncalendar`, `test_interval_units`, `test_remove_disables_and_deletes`, `test_paused_skips_fire` (existing `routine_fire` rule), `test_watch_uses_shell_text`. FAIL → implement → PASS → commit

---

## Milestone 7: Draw on screen

### Task 30: Screen drawing, pointing, teach-me

**Files:** Create `linux/atticus_shell/drawing.py`, `tests/linux/test_drawing_shell.py`

**Interfaces:** `ScreenDrawing.show(request: dict) -> None` takes the same `DrawingRequest` JSON as `Atticus.swift:1320` and renders shapes/callouts with Cairo in an overlay per output. `set_input_region` = union of mark rects, so clicks pass through everywhere else. Math labels render in a hidden WebKit view of `ui/drawing-math.html` (handlers `drawingMath`, `agents`). The caption card sits in the opposite corner, and the box moves out of the way. `point_at(line: str, box: Rect | None, hold=6.0)` / `unpoint()` port `Atticus.swift:6468-6541`. Clicking a mark selects it for the next ask.
- [ ] Tests: port the geometry cases from `tests/test_drawalign.py` and `tests/test_drawing_objects.py` to the Cairo renderer; `test_input_region_equals_marks`, `test_box_moves_out_and_back`, `test_click_mark_attaches_to_next_ask`. FAIL → implement → PASS → commit

---

## Milestone 8: Meeting Copilot

### Task 31: Meetings

**Files:** Create `linux/atticus_shell/meeting.py`, `tests/linux/test_meeting_shell.py`

**Interfaces:** `MeetingAudio(mic_uid, sink_monitor)` produces two labelled streams (You / Them) into two `SttEngine` instances. `call_apps_on_mic() -> set[str]` from `pactl -f json list source-outputs` `application.process.binary` ∈ {zoom, teams, slack, discord, chrome/chromium/firefox with a `meet.google.com` tab via the web host}. `MeetingCopilot` window hosts `ui/meeting.html` (handler `meeting`): drag, per-output remembered frames, minimized pill, Ctrl+Return answer shortcut, plain Return only when `selection.capture` shows no focused editable. The archive, recap and history are unchanged brain code (`meeting_*`, `atticus.py:8635-8960`).
- [ ] Tests: `test_every_meeting_message_handled`, `test_return_goes_to_app_when_typing`, `test_audio_never_written` (no file opened with an audio extension during a session), `test_frames_clamped_after_output_removed`. FAIL → implement → PASS → commit

---

## Milestone 9: Calendar, contacts, Spotify, wake word

### Task 32: Calendar

**Files:** Create `linux/atticus_shell/calendar.py`; fill `cli --calendar`; nudges (`checkCalendar`). Create `tests/linux/test_calendar_linux.py`.

**Interfaces:** `CalendarTool.run(data: dict) -> dict`: same JSON in/out as `Atticus.swift:7401-7476` (list/add/move/delete; move/delete need the Allow card, add is immediate). Backends: `EdsCalendar` (ECal over all enabled sources, which covers GNOME Online Accounts, Google and Nextcloud) and `CaldavCalendar(url, user, secret)` with the secret stored via libsecret under the attribute `atticus-caldav`. `pick_backend(prefs)` uses EDS if any calendar source exists, else CalDAV if configured, else `{"error":"No calendar connected. Settings → Calendar."}`. `current_meeting_event(now)` feeds Meeting Copilot context.
- [ ] Tests: run the existing `tests/test_calendar.py` JSON cases against `CalendarTool` with a fake ECal; `test_no_backend_error_names_settings`, `test_move_requires_allow`. FAIL → implement → PASS → commit

### Task 33: Contacts and About-you import

**Files:** Modify `brain_linux.py` (`contacts`, `mac_profile`). Create `tests/linux/test_contacts_linux.py`.

**Interfaces:** `contacts(query)` → EBook search over EDS address books (`atticus.py:8126` output shape), falling back to vCards in `~/.local/share/contacts`. `mac_profile()` → `{name}` from `getent passwd $USER` GECOS plus the EDS "self" contact when one is marked. The name is kept for upstream compatibility.
- [ ] Tests: `test_contact_matches_shape_unchanged`, `test_gecos_name_parsed`. FAIL → implement → PASS → commit

### Task 34: Spotify

**Files:** Modify `brain_linux.py` (`play`, `queue`, `on_spotify`, `_spotify_*`). Create `tests/linux/test_spotify_linux.py`.

**Interfaces:** `play(name, anything, credited)` keeps `catalog_item`/`spotify_item` lookup unchanged, then calls MPRIS `org.mpris.MediaPlayer2.spotify` `OpenUri('spotify:track:<id>')` (launching Spotify behind if it isn't running) and verifies via `Metadata['xesam:title']` with the existing `heard()` matcher and the `PLAYED` record. `queue(name)` runs `hands` on Spotify's AT-SPI tree (search → context menu "Add to queue") and verifies the queue panel text. If AT-SPI is unavailable and `linuxSpotifyToken` is set, it uses the Web API `POST /me/player/queue`. Otherwise it replies "Turn on accessibility or sign in to Spotify in Settings to queue songs."
- [ ] Tests: run the `tests/test_spotify.py` lookup cases unchanged; `test_play_verifies_title_via_mpris`, `test_queue_falls_back_to_web_api`, `test_queue_neither_says_how`. FAIL → implement → PASS → commit

### Task 35: Wake word

**Files:** Create `linux/atticus_shell/speech/wake.py`, `tests/linux/test_wake.py`

**Interfaces:** `WakeWord(word: str, on_wake: Callable[[], None])` runs sherpa-onnx KWS with keywords built from `wakeWordName` (optional "hey " prefix variant), `start(mic) / stop()`. Re-listen after each answer, the 90 s fade rule and "hotkey always takes over" are ported from the macOS flow. The tray icon shows the mic badge while it listens.
- [ ] Tests: `test_custom_word_tokenized`, `test_hotkey_preempts_wake_session`, `test_idle_chat_fades_after_90s_unless_moved`. FAIL → implement → PASS → commit

---

## Milestone 10: Setup, Settings, updates, ship

### Task 36: Onboarding and permission help

**Files:** Create `linux/atticus_shell/onboarding.py`, `linux/atticus_shell/permissions.py`. Modify `ui/onboarding.html` (Linux step variants behind `platform === 'linux'`, injected by `make_view`) and `ui/i18n.js`. Create `tests/linux/test_onboarding_linux.py`.

**Interfaces:** `permissions.status() -> dict[str, str]` with keys `a11y | input | shortcuts | bridge | screen | browser | calendar | mic | tray`, values `ok | missing | needs_relogin | unsupported`. One "Fix" action per key: enable toolkit-accessibility; install the udev rule (shows the exact `sudo` command); bind portal shortcuts; enable the GNOME extension or load the KWin script; take a test screenshot; open the extension store page; open GNOME Online Accounts or the CalDAV form; test the mic; offer the AppIndicator extension. The AI step is unchanged (Claude Code / Devin / Pi / custom server; `detectLocalServers` ported). Handles every `ob` message.
- [ ] Tests: `test_every_ob_message_handled`, `test_status_needs_relogin_after_extension_install`, `test_linux_strings_in_all_five_languages` (reuse the `tests/test_i18n.py` checker). FAIL → implement → PASS → commit

### Task 37: Settings window

**Files:** Create `linux/atticus_shell/settings.py`. Modify `ui/settings.html` (rows tagged `data-os="mac"` are hidden on Linux; new rows: Browser extension, Calendar source, Spotify sign-in, Desktop integration status, Voice download) and `ui/i18n.js`. Create `tests/linux/test_settings_linux.py`.

**Interfaces:** `SettingsWindow` handles every `st` message (`Atticus.swift:6850-7254`), reusing `Prefs.set`. Shortcut recording on Linux opens the portal `ConfigureShortcuts` (or captures keys on X11 / prints `bindsym` on Sway). Search works unchanged.
- [ ] Tests: `test_every_st_message_handled`, `test_mac_only_rows_hidden_on_linux` (list: Press 🌐 key, Login Items, System Settings links), `test_settings_search_finds_linux_rows`. FAIL → implement → PASS → commit

### Task 38: Updater, feedback, telemetry

**Files:** Create `linux/atticus_shell/updater.py`. Modify `brain_linux.py` (feedback system-info). Create `tests/linux/test_updater_linux.py`.

**Interfaces:** `Updater.check() -> str | None`, `Updater.apply_when_idle()`: `git -C ~/.atticus pull --ff-only` then `ATTICUS_NO_RESTART=1 build-linux.sh`, then `systemctl --user restart atticus` once idle (same rules as `enum Updater`, `Atticus.swift:6553`; Nix installs show the `nix profile upgrade` hint instead). Feedback/telemetry fields: `os = PRETTY_NAME from /etc/os-release`, `model = /sys/class/dmi/id/product_name`, `desktop = env.compositor + session`. Never paths or text.
- [ ] Tests: `test_dirty_checkout_skips_update`, `test_nix_install_shows_hint`, `test_feedback_fields_no_paths`. FAIL → implement → PASS → commit

### Task 39: Packaging, docs, parity sign-off

**Files:** Modify `flake.nix` (add `x86_64-linux`/`aarch64-linux` packages wrapping `build-linux.sh` with the GI deps via `wrapGAppsHook3`), `.github/workflows/nix.yml` (ubuntu matrix), `README.md` (Linux install line `curl -fsSL …/install-linux.sh | bash`, support tiers), and a new `docs/LINUX.md` (per-desktop setup, gaps from the parity matrix, uninstall: `systemctl --user disable --now atticus`, remove `~/.local/share/atticus`, `~/.atticus`, `~/.config/atticus`, the extension and the udev rule). Create `tests/linux/test_parity.py`.

- [ ] **Step 1:** `test_parity_matrix_rows_have_tasks_and_tests`: parse this plan's parity table and assert every Task number exists in `tests/linux/` via a `# parity: <Feature>` comment in at least one test.
- [ ] **Step 2:** Manual acceptance on a GNOME 48 Wayland VM, a KDE Plasma 6 Wayland VM and an X11 (XFCE) VM. Run every row of the README "Just ask" table and record pass/fail in `docs/LINUX.md`.
- [ ] **Step 3:** `nix flake check --all-systems`, `./check --all` on Linux and macOS → PASS
- [ ] **Step 4:** Commit `linux: packaging, docs, parity sign-off`

---

## Execution order and parallelism

M0 runs strictly in order (Tasks 1→9; 7–9 can run in parallel after 6). M1 (10→17) is the first user-visible milestone: talking works on all tiers. After M1, Milestones 2–4 depend only on M0/M1 and on Task 21 for 22–23. M5–M9 can run in parallel tracks once M3 lands. M10 comes last. Rough size: about 14–16k lines of new Python/JS, in line with the Swift it replaces.
