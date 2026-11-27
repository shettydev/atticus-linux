// Atticus bridge for GNOME Shell 46+: one D-Bus method, Call(method, json) -> json, on app.atticus.ShellBridge.
// Wayland gives apps no way to move other windows, read the pointer or picture a covered window; the shell can.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

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
    const id = app ? app.get_id() || '' : '';
    return id ? id.replace(/\.desktop$/, '') : (w.get_wm_class() || '').toLowerCase();
}

function writeTexture(texture, scale, path, done) {
    const stream = Gio.File.new_for_path(path).replace(null, false, Gio.FileCreateFlags.REPLACE_DESTINATION, null);
    Shell.Screenshot.composite_to_stream(texture, 0, 0, -1, -1, scale, null, 0, 0, 1, stream, (_o, res) => {
        try { Shell.Screenshot.composite_to_stream_finish(res); stream.close(null); done({ok: true}); }
        catch (e) { done({ok: false, error: String(e)}); }
    });
}

const METHODS = {
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
    move_resize: a => {
        const w = byId(a.id); if (!w) return {ok: false};
        if (w.get_maximized()) w.unmaximize(Meta.MaximizeFlags.BOTH);
        w.move_resize_frame(true, a.x, a.y, a.width, a.height);
        return {ok: true};
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
const ASYNC = new Set(['screenshot_area', 'screenshot_window']);

export default class AtticusBridge extends Extension {
    enable() {
        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._dbus.export(Gio.DBus.session, '/app/atticus/ShellBridge');
        this._owner = Gio.bus_own_name_on_connection(Gio.DBus.session, 'app.atticus.ShellBridge', Gio.BusNameOwnerFlags.REPLACE, null, null);
        this._created = global.display.connect('window-created', (_d, w) => this._adopt(w));
        normalWindows().forEach(w => this._adopt(w));
    }

    disable() {
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
