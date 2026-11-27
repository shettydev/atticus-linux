// GNOME Shell 45+ ESM extension exposing org.atticus.GnomeHelper on session D-Bus
// so Atticus has full window placement, focus, display geometry, and pointer tracking on GNOME Wayland.

import Gio from 'gi://Gio';
import Meta from 'gi://Meta';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import { Extension } from 'resource:///org/gnome/shell/extensions/extension.js';

const DBUS_IFACE = `
<node>
  <interface name="org.atticus.GnomeHelper">
    <method name="ListDisplays"><arg type="s" direction="out" name="json"/></method>
    <method name="ListWindows"><arg type="s" direction="out" name="json"/></method>
    <method name="PointerPosition"><arg type="s" direction="out" name="json"/></method>
    <method name="FocusWindow"><arg type="s" direction="in" name="id"/><arg type="s" direction="out" name="ok"/></method>
    <method name="MoveResizeWindow">
      <arg type="s" direction="in" name="id"/>
      <arg type="i" direction="in" name="x"/>
      <arg type="i" direction="in" name="y"/>
      <arg type="i" direction="in" name="w"/>
      <arg type="i" direction="in" name="h"/>
      <arg type="b" direction="in" name="fullscreen"/>
      <arg type="s" direction="out" name="ok"/>
    </method>
    <method name="CloseWindow"><arg type="s" direction="in" name="id"/><arg type="s" direction="out" name="ok"/></method>
  </interface>
</node>`;

export default class AtticusGnomeExtension extends Extension {
  enable() {
    this._dbus = Gio.DBusExportedObject.wrapJSObject(DBUS_IFACE, this);
    this._dbus.export(Gio.DBus.session, '/org/atticus/GnomeHelper');
  }

  disable() {
    if (this._dbus) {
      this._dbus.unexport();
      this._dbus = null;
    }
  }

  ListDisplays() {
    const monitors = Main.layoutManager.monitors || [];
    const primaryIdx = Main.layoutManager.primaryIndex;
    const out = monitors.map((m, i) => {
      const work = Main.layoutManager.getWorkAreaForMonitor(i);
      return {
        name: `Monitor-${i + 1}`,
        x: m.x, y: m.y, width: m.width, height: m.height,
        visible_x: work.x, visible_y: work.y, visible_width: work.width, visible_height: work.height,
        built_in: i === primaryIdx,
        primary: i === primaryIdx,
      };
    });
    return JSON.stringify(out);
  }

  ListWindows() {
    const actors = global.get_window_actors();
    const out = [];
    for (const actor of actors) {
      const w = actor.meta_window;
      if (!w || w.window_type !== Meta.WindowType.NORMAL) continue;
      const r = w.get_frame_rect();
      const id = String(w.get_id());
      const cls = w.get_wm_class() || '';
      out.push({
        id,
        numeric_id: Number(w.get_id()) || 0,
        pid: w.get_pid(),
        app: cls,
        wm_class: cls,
        title: w.get_title() || '',
        x: r.x, y: r.y, width: r.width, height: r.height,
        focused: w.has_focus(),
        minimized: w.minimized,
        fullscreen: w.is_fullscreen(),
        workspace: String(w.get_workspace()?.index() ?? 0),
      });
    }
    return JSON.stringify(out);
  }

  PointerPosition() {
    const [x, y] = global.get_pointer();
    return JSON.stringify({ x, y });
  }

  _findWindow(id) {
    return global.get_window_actors()
      .map(a => a.meta_window)
      .find(w => w && String(w.get_id()) === String(id));
  }

  FocusWindow(id) {
    const w = this._findWindow(id);
    if (!w) return 'not_found';
    w.activate(global.get_current_time());
    return 'ok';
  }

  MoveResizeWindow(id, x, y, width, height, fullscreen) {
    const w = this._findWindow(id);
    if (!w) return 'not_found';
    if (w.minimized) w.unminimize();
    if (fullscreen) {
      w.make_fullscreen();
    } else {
      if (w.is_fullscreen()) w.unmake_fullscreen();
      w.unmaximize(Meta.MaximizeFlags.BOTH);
      w.move_resize_frame(true, x, y, width, height);
    }
    return 'ok';
  }

  CloseWindow(id) {
    const w = this._findWindow(id);
    if (!w) return 'not_found';
    w.delete(global.get_current_time());
    return 'ok';
  }
}
