// Atticus bridge script for KWin 5.27 and 6. Loaded once per request with OP/ARGS filled in; answers over D-Bus.
const OP = __OP__;
const ARGS = __ARGS__;
const REPLY = [__SERVICE__, __PATH__];

function reply(value) {
    callDBus(REPLY[0], REPLY[1], "app.atticus.KwinReply", "Reply", JSON.stringify(value));
}
function windowsBottomFirst() {
    const list = workspace.windowList ? workspace.windowList() : workspace.clientList();
    return list.filter(w => w.normalWindow);
}
function active() {
    return workspace.activeWindow !== undefined ? workspace.activeWindow : workspace.activeClient;
}
function setActive(w) {
    if (workspace.activeWindow !== undefined) workspace.activeWindow = w; else workspace.activeClient = w;
}
function byId(id) {
    return windowsBottomFirst().find(w => String(w.internalId) === String(id)) || null;
}
function rect(g) {
    return [Math.round(g.x), Math.round(g.y), Math.round(g.width), Math.round(g.height)];
}

const ops = {
    windows: () => {
        const focus = active();
        return windowsBottomFirst().reverse().map(w => {
            const g = w.frameGeometry, d = w.desktops && w.desktops.length ? w.desktops[0] : null;
            return {id: String(w.internalId), app_id: w.desktopFileName || String(w.resourceClass || "").toLowerCase(), pid: w.pid || 0,
                title: w.caption || "", x: Math.round(g.x), y: Math.round(g.y), width: Math.round(g.width), height: Math.round(g.height),
                workspace: d ? String(d.id !== undefined ? d.id : d) : String(w.desktop || ""), minimized: !!w.minimized,
                fullscreen: !!w.fullScreen, focused: w === focus};
        });
    },
    outputs: () => {
        if (workspace.screens) {   // KWin 6: Output objects
            return workspace.screens.map(o => {
                const g = o.geometry, a = workspace.clientArea(KWin.MaximizeArea, o, workspace.currentDesktop);
                return {name: o.name, x: g.x, y: g.y, width: g.width, height: g.height, scale: o.devicePixelRatio || 1, area: rect(a)};
            });
        }
        const out = [];   // KWin 5: screen numbers, no connector names
        for (let i = 0; i < workspace.numScreens; i++) {
            const g = workspace.clientArea(KWin.ScreenArea !== undefined ? KWin.ScreenArea : 7, i, workspace.currentDesktop);
            const a = workspace.clientArea(KWin.MaximizeArea, i, workspace.currentDesktop);
            out.push({name: "screen-" + i, x: g.x, y: g.y, width: g.width, height: g.height, scale: 1, area: rect(a)});
        }
        return out;
    },
    pointer: () => ({x: workspace.cursorPos.x, y: workspace.cursorPos.y}),
    move_resize: (w, a) => {
        if (w.setMaximize) w.setMaximize(false, false);
        w.frameGeometry = {x: a.x, y: a.y, width: a.width, height: a.height};
        return {ok: true};
    },
    set_fullscreen: (w, a) => { w.fullScreen = !!a.on; return {ok: true}; },
    activate: w => { if (w.minimized) w.minimized = false; setActive(w); return {ok: true}; },
    lower: w => { w.keepBelow = true; w.keepBelow = false; return {ok: true}; },
    keep_above: (w, a) => { w.keepAbove = !!a.on; return {ok: true}; },
};
const NEEDS_WINDOW = ["move_resize", "set_fullscreen", "activate", "lower", "keep_above"];

(function () {
    try {
        if (!ops[OP]) return reply({ok: false, error: "unknown op " + OP});
        if (NEEDS_WINDOW.indexOf(OP) >= 0) {
            const w = byId(ARGS.id);
            return reply(w ? ops[OP](w, ARGS) : {ok: false});
        }
        reply(ops[OP](ARGS));
    } catch (e) {
        reply({ok: false, error: String(e)});
    }
})();
