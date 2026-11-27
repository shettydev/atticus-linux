// Atticus bridge for GNOME Shell 46+: one D-Bus method, Call(method, json) -> json, on app.atticus.ShellBridge.
// Wayland gives apps no way to move other windows, read the pointer or picture a covered window; the shell can.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const IFACE = `<node><interface name="app.atticus.ShellBridge">
  <method name="Call"><arg type="s" name="method" direction="in"/><arg type="s" name="args" direction="in"/>
    <arg type="s" name="result" direction="out"/></method>
</interface></node>`;

function normalWindows() {   // top of the stack first
    return global.get_window_actors().map(a => a.get_meta_window())
        .filter(w => w && !w.is_override_redirect() && w.get_window_type() === Meta.WindowType.NORMAL).reverse();
}

function byId(id) {
    return normalWindows().find(w => String(w.get_id()) === String(id)) || null;
}

function appId(w) {
    const app = Shell.WindowTracker.get_default().get_window_app(w);
    let id = app ? app.get_id() || '' : '';
    if (id.startsWith('window:')) id = '';   // the shell's stand-in for an app with no launcher entry: its window class names it
    return id ? id.replace(/\.desktop$/, '') : (w.get_wm_class() || '').toLowerCase();
}

function unmaximize(w) {   // true when the window was maximized
    if (!w.maximized_horizontally && !w.maximized_vertically) return false;
    if (w.get_maximized) w.unmaximize(Meta.MaximizeFlags.BOTH);   // GNOME 46-48
    else w.unmaximize();                                          // GNOME 49+ dropped get_maximized() and the flags
    return true;
}

// Un-maximizing finishes when the app answers, and on GNOME 50 that restores the old position over the requested one:
// apply the move again until it holds for two checks. The caller reads the frame back, so a refused size still shows.
function holdFrame(w, a, done) {
    let tries = 0, held = 0;
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 50, () => {
        const r = w.get_frame_rect();
        const there = r.x === a.x && r.y === a.y && r.width === a.width && r.height === a.height;
        held = there ? held + 1 : 0;
        if (held >= 2 || ++tries >= 20) { done({ok: true}); return GLib.SOURCE_REMOVE; }
        if (!there) w.move_resize_frame(true, a.x, a.y, a.width, a.height);
        return GLib.SOURCE_CONTINUE;
    });
}

function writeTexture(texture, scale, path, done) {
    const stream = Gio.File.new_for_path(path).replace(null, false, Gio.FileCreateFlags.REPLACE_DESTINATION, null);
    Shell.Screenshot.composite_to_stream(texture, 0, 0, -1, -1, scale, null, 0, 0, 1, stream, (_o, res) => {
        try { Shell.Screenshot.composite_to_stream_finish(res); stream.close(null); done({ok: true}); }
        catch (e) { done({ok: false, error: String(e)}); }
    });
}

// Atticus's shortcuts, grabbed here because Wayland apps can't. A press sends Trigger(name, 'down') to Atticus; GNOME reports
// no release for an accelerator, so the held modifiers are watched and their release sends 'up' (hold to talk).
const MOD_MASKS = {'<Control>': [Clutter.ModifierType.CONTROL_MASK], '<Alt>': [Clutter.ModifierType.MOD1_MASK],
    '<Shift>': [Clutter.ModifierType.SHIFT_MASK], '<Super>': [Clutter.ModifierType.SUPER_MASK, Clutter.ModifierType.MOD4_MASK]};
const grabs = new Map();   // action -> {name, mods: [[mask, ...], ...]}
let held = null, watch = 0;

function trigger(name, edge) {
    Gio.DBus.session.call('app.atticus.Shell', '/app/atticus/Shell', 'app.atticus.Shell', 'Trigger',
        new GLib.Variant('(ss)', [name, edge]), null, Gio.DBusCallFlags.NO_AUTO_START, 2000, null, null);
}

function release() {
    if (watch) GLib.source_remove(watch);
    watch = 0;
    if (held) trigger(held.name, 'up');
    held = null;
}

function activated(action) {
    const g = grabs.get(action);
    if (!g || held) return;   // a held key repeats: one press is one 'down'
    held = g;
    trigger(g.name, 'down');
    if (!g.mods.length) return release();
    watch = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 30, () => {
        const mods = global.get_pointer()[2];
        if (g.mods.every(alts => alts.some(m => mods & m))) return GLib.SOURCE_CONTINUE;
        watch = 0;
        release();
        return GLib.SOURCE_REMOVE;
    });
}

function ungrabAll() {
    release();
    for (const action of grabs.keys()) {
        global.display.ungrab_accelerator(action);
        Main.wm.allowKeybinding(Meta.external_binding_name_for_action(action), Shell.ActionMode.NONE);
    }
    grabs.clear();
}

const METHODS = {
    bind_shortcuts: a => {   // {shortcuts: {talk: '<Control><Alt>space', ...}} -> {bound: {talk: true, ...}}; replaces earlier grabs
        ungrabAll();
        const bound = {};
        const flags = Meta.KeyBindingFlags.IGNORE_AUTOREPEAT ?? Meta.KeyBindingFlags.NONE;
        for (const [name, accel] of Object.entries(a.shortcuts || {})) {
            const action = global.display.grab_accelerator(accel, flags);
            bound[name] = action !== Meta.KeyBindingAction.NONE;
            if (!bound[name]) continue;
            Main.wm.allowKeybinding(Meta.external_binding_name_for_action(action), Shell.ActionMode.ALL);
            grabs.set(action, {name, mods: (accel.match(/<\w+>/g) || []).map(m => MOD_MASKS[m]).filter(Boolean)});
        }
        return {ok: true, bound};
    },
    test_keys: (a, done) => {   // tests only (gnome-live.sh): press keys on a virtual keyboard, hold, release
        if (GLib.getenv('ATTICUS_EXTENSION_TEST') !== '1') return done({ok: false, error: 'test only'});
        const dev = Clutter.get_default_backend().get_default_seat().create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
        const keys = a.keys.map(k => Clutter[`KEY_${k}`]);
        keys.forEach(k => dev.notify_keyval(GLib.get_monotonic_time(), k, Clutter.KeyState.PRESSED));
        GLib.timeout_add(GLib.PRIORITY_DEFAULT, a.hold || 300, () => {
            [...keys].reverse().forEach(k => dev.notify_keyval(GLib.get_monotonic_time(), k, Clutter.KeyState.RELEASED));
            done({ok: true});
            return GLib.SOURCE_REMOVE;
        });
    },
    windows: () => {
        const focus = global.display.get_focus_window();
        return normalWindows().map(w => {
            const r = w.get_frame_rect();
            return {id: String(w.get_id()), app_id: appId(w), pid: w.get_pid(), title: w.get_title() || '',
                x: r.x, y: r.y, width: r.width, height: r.height, workspace: w.get_workspace() ? w.get_workspace().index() : -1,
                minimized: w.minimized, fullscreen: w.is_fullscreen(), focused: w === focus};
        });
    },
    pointer: () => { const [x, y] = global.get_pointer(); return {x, y}; },
    work_areas: () => {
        const ws = global.workspace_manager.get_active_workspace(), out = [];
        for (let i = 0; i < global.display.get_n_monitors(); i++) {
            const g = global.display.get_monitor_geometry(i), a = ws.get_work_area_for_monitor(i);
            out.push({x: g.x, y: g.y, width: g.width, height: g.height, area: [a.x, a.y, a.width, a.height]});
        }
        return out;
    },
    move_resize: (a, done) => {
        const w = byId(a.id); if (!w) return done({ok: false});
        const wasMaximized = unmaximize(w);   // a maximized window ignores moves
        w.move_resize_frame(true, a.x, a.y, a.width, a.height);
        if (wasMaximized) holdFrame(w, a, done); else done({ok: true});
    },
    set_fullscreen: a => { const w = byId(a.id); if (!w) return {ok: false}; a.on ? w.make_fullscreen() : w.unmake_fullscreen(); return {ok: true}; },
    activate: a => { const w = byId(a.id); if (!w) return {ok: false}; w.activate(global.get_current_time()); return {ok: true}; },
    lower: a => { const w = byId(a.id); if (!w) return {ok: false}; w.lower(); return {ok: true}; },
    keep_above: a => { const w = byId(a.id); if (!w) return {ok: false}; a.on ? w.make_above() : w.unmake_above(); return {ok: true}; },
    screenshot_area: (a, done) => {
        const shot = new Shell.Screenshot();
        const stream = Gio.File.new_for_path(a.path).replace(null, false, Gio.FileCreateFlags.REPLACE_DESTINATION, null);
        shot.screenshot_area(a.x, a.y, a.width, a.height, stream, (o, res) => {
            try { o.screenshot_area_finish(res); stream.close(null); done({ok: true}); }
            catch (e) { done({ok: false, error: String(e)}); }
        });
    },
    screenshot_window: (a, done) => {   // the window actor paints offscreen, so covered windows work too
        const w = byId(a.id); if (!w) return done({ok: false});
        const actor = w.get_compositor_private(), content = actor && actor.paint_to_content(null);
        if (!content) return done({ok: false});
        writeTexture(content.get_texture(), global.display.get_monitor_scale(w.get_monitor()), a.path, done);
    },
};
const ASYNC = new Set(['move_resize', 'screenshot_area', 'screenshot_window', 'test_keys']);

export default class AtticusBridge extends Extension {
    enable() {
        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._dbus.export(Gio.DBus.session, '/app/atticus/ShellBridge');
        this._owner = Gio.bus_own_name_on_connection(Gio.DBus.session, 'app.atticus.ShellBridge', Gio.BusNameOwnerFlags.REPLACE, null, null);
        this._created = global.display.connect('window-created', (_d, w) => this._adopt(w));
        normalWindows().forEach(w => this._adopt(w));
        this._accel = global.display.connect('accelerator-activated', (_d, action) => activated(action));
    }

    disable() {
        ungrabAll();
        global.display.disconnect(this._accel);
        global.display.disconnect(this._created);
        Gio.bus_unown_name(this._owner);
        this._dbus.unexport();
        this._dbus = null;
    }

    _adopt(w) {   // Atticus's own overlays: above everything, on every workspace (wm_class "atticus")
        if ((w.get_wm_class() || '').toLowerCase() !== 'atticus') return;
        GLib.idle_add(GLib.PRIORITY_DEFAULT, () => { w.make_above(); w.stick(); return GLib.SOURCE_REMOVE; });
    }

    CallAsync([method, args], invocation) {
        const fn = METHODS[method];
        const reply = r => invocation.return_value(new GLib.Variant('(s)', [JSON.stringify(r)]));
        if (!fn) return reply({ok: false, error: `unknown method ${method}`});
        try {
            const parsed = JSON.parse(args || '{}');
            if (ASYNC.has(method)) fn(parsed, reply); else reply(fn(parsed));
        } catch (e) {
            reply({ok: false, error: String(e)});
        }
    }
}
