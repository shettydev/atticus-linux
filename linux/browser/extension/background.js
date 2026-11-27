// Atticus browser bridge: one native messaging port to the Atticus host; each request names an op and gets one reply.
// ui/web.js (packaged as web-inject.js) reads and acts on a page exactly as on macOS, where AppleScript ran it.
const port = chrome.runtime.connectNative('app.atticus.web');
const ua = navigator.userAgent;
const browserName = /Edg\//.test(ua) ? 'Microsoft Edge' : /Firefox\//.test(ua) ? 'Firefox' : /Brave/.test(ua) ? 'Brave' : /Chrome\//.test(ua) ? 'Chrome' : 'Browser';
port.postMessage({hello: {browser: browserName, version: chrome.runtime.getManifest().version}});

async function script(tabId, func, args) {
  const [r] = await chrome.scripting.executeScript({target: {tabId}, func, args: args || []});
  return r ? r.result : null;
}

const ops = {
  tabs: async () => {
    const focused = await chrome.windows.getLastFocused();
    return (await chrome.tabs.query({})).map(t => ({id: t.id, windowId: t.windowId, active: t.active, url: t.url || '', title: t.title || '',
                                                    focused: !!focused && t.windowId === focused.id}));
  },
  run: async m => {
    await chrome.scripting.executeScript({target: {tabId: m.tab}, files: ['web-inject.js']});
    const [r] = await chrome.scripting.executeScript({target: {tabId: m.tab}, func: (...a) => globalThis.__atticusWeb(...a), args: m.args});
    return r ? r.result : null;
  },
  document: async m => script(m.tab, () => [location.href, String(performance.timeOrigin)]),
  open: async m => {
    if (m.window === 'agent') {   // the task's own window, behind the user's
      let w;
      try { w = await chrome.windows.create({url: m.url, focused: false, state: 'normal'}); }
      catch (e) { w = await chrome.windows.create({url: m.url}); }   // Firefox has no unfocused create
      return {window: w.id, tab: w.tabs[0].id};
    }
    const t = await chrome.tabs.create({windowId: typeof m.window === 'number' ? m.window : undefined, url: m.url, active: !!m.active});
    return {window: t.windowId, tab: t.id};
  },
  navigate: async m => {
    const t = await chrome.tabs.get(m.tab);
    if (m.expect && t.url !== m.expect) throw new Error('Atticus page changed');
    await chrome.tabs.update(m.tab, {url: m.url});
    return true;
  },
  tab: async m => { const t = await chrome.tabs.get(m.tab); return {url: t.url || '', title: t.title || '', window: t.windowId}; },
  window: async m => { const w = await chrome.windows.get(m.window); return {left: w.left, top: w.top, width: w.width, height: w.height}; },
  focus: async m => { if (m.tab) await chrome.tabs.update(m.tab, {active: true}); await chrome.windows.update(m.window, {focused: true}); return true; },
  close_window: async m => { await chrome.windows.remove(m.window); return true; },
};

port.onMessage.addListener(async m => {
  const op = ops[m.op];
  if (!op) return port.postMessage({id: m.id, ok: false, error: 'unknown op ' + m.op});
  try { port.postMessage({id: m.id, ok: true, value: await op(m)}); }
  catch (e) { port.postMessage({id: m.id, ok: false, error: String((e && e.message) || e)}); }
});
port.onDisconnect.addListener(() => {});
