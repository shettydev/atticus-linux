import Gio from 'gi://Gio';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const iface = `<node><interface name="org.atticus.Desktop1">
    <method name="Snapshot"><arg type="s" direction="out"/></method>
    <method name="Place">
        <arg name="id" type="u" direction="in"/><arg name="monitor" type="i" direction="in"/>
        <arg name="mode" type="s" direction="in"/><arg type="s" direction="out"/>
    </method>
</interface></node>`;

function windows() {
    return global.display.get_tab_list(Meta.TabList.NORMAL_ALL, null);
}

function frame(rect) {
    return [rect.x, rect.y, rect.width, rect.height];
}

export default class AtticusDesktop extends Extension {
    enable() {
        this._dbus = Gio.DBusExportedObject.wrapJSObject(iface, this);
        this._dbus.export(Gio.DBus.session, '/org/atticus/Desktop1');
        Main.wm.addKeybinding('atticus-open-ask', this.getSettings(),
            Meta.KeyBindingFlags.IGNORE_AUTOREPEAT, Shell.ActionMode.NORMAL | Shell.ActionMode.OVERVIEW, () => {
                const app = Shell.AppSystem.get_default().lookup_app('org.atticus.Desktop.desktop');
                app?.activate();
            });
    }

    disable() {
        Main.wm.removeKeybinding('atticus-open-ask');
        this._dbus.unexport();
        this._dbus = null;
    }

    Snapshot() {
        const tracker = Shell.WindowTracker.get_default();
        const manager = global.backend.get_monitor_manager();
        const outputs = manager.get_monitors();
        const monitors = Main.layoutManager.monitors.map(m => {
            const group = outputs.filter(o => manager.get_monitor_for_connector(o.get_connector()) === m.index);
            return {index: m.index, primary: m.index === Main.layoutManager.primaryIndex,
                name: group[0]?.get_connector() ?? `Display ${m.index + 1}`,
                builtin: group.some(o => o.is_builtin())};
        });
        const rows = windows().map(w => {
            const app = tracker.get_window_app(w);
            return {id: w.get_stable_sequence(), app: app?.get_name(), desktop: app?.get_id(),
                wmclass: w.get_wm_class(), title: w.get_title(), frame: frame(w.get_frame_rect()),
                monitor: w.get_monitor(), fullscreen: w.is_fullscreen(), minimized: w.minimized};
        });
        return JSON.stringify({monitors, current: global.display.get_current_monitor(), windows: rows});
    }

    Place(id, monitor, mode) {
        const w = windows().find(item => item.get_stable_sequence() === id);
        if (!w || !Main.layoutManager.monitors[monitor])
            throw new Error('Window or display disappeared');
        if (!['left', 'right', 'center', 'full', 'fullscreen'].includes(mode))
            throw new Error('Unknown placement');

        const bounds = mode === 'fullscreen' ? Main.layoutManager.monitors[monitor] :
            w.get_work_area_for_monitor(monitor);
        let [x, y, width, height] = frame(bounds);
        if (mode === 'left' || mode === 'right') {
            const half = Math.floor(width / 2);
            if (mode === 'left') width = half;
            else { x += half; width -= half; }
        } else if (mode === 'center') {
            const old = w.get_frame_rect();
            const cw = Math.min(old.width, width), ch = Math.min(old.height, height);
            x += Math.floor((width - cw) / 2);
            y += Math.floor((height - ch) / 2);
            width = cw;
            height = ch;
        }

        if (w.minimized) w.unminimize();
        if (w.is_fullscreen() && mode !== 'fullscreen') w.unmake_fullscreen();
        // A positioned frame already selects its output. A separate monitor move
        // would send a competing configure to a Wayland client.
        if ((mode === 'full' || mode === 'fullscreen') && w.get_monitor() !== monitor)
            w.move_to_monitor(monitor);
        if (mode === 'fullscreen') w.make_fullscreen();
        else if (mode === 'full') w.maximize();
        else {
            if (w.get_maximize_flags()) w.unmaximize();
            w.move_resize_frame(true, x, y, width, height);
        }
        return JSON.stringify([x, y, width, height]);
    }
}
