// Atticus Linux MV3 Browser Bridge (Chrome / Brave / Edge / Chromium / Firefox).
// Connects to `com.atticus.browser_bridge` Native Messaging host (`atticus-browser-host`)
// and executes `ui/web.js` commands on tabs or dedicated background agent windows.

const api = typeof browser !== 'undefined' ? browser : chrome;
const HOST_NAME = 'com.atticus.browser_bridge';
let port = null;

function connect() {
  try {
    port = api.runtime.connectNative(HOST_NAME);
    port.onMessage.addListener(handleMessage);
    port.onDisconnect.addListener(() => {
      port = null;
      setTimeout(connect, 3000);
    });
  } catch (_) {
    setTimeout(connect, 5000);
  }
}

async function handleMessage(msg) {
  const id = msg?.id;
  const reply = payload => {
    if (port) port.postMessage({ id, ...payload });
  };
  try {
    switch (msg?.cmd) {
      case 'ping':
        return reply({ ok: true });

      case 'active_tab': {
        const tabs = await api.tabs.query({ active: true, lastFocusedWindow: true });
        const tab = tabs[0];
        if (!tab) return reply({ ok: false, error: 'No active tab' });
        return reply({ ok: true, window: tab.windowId, tab: tab.id, title: tab.title || '', url: tab.url || '' });
      }

      case 'tab_info': {
        const tab = await api.tabs.get(msg.tab);
        return reply({ ok: true, window: tab.windowId, tab: tab.id, title: tab.title || '', url: tab.url || '' });
      }

      case 'find_tab': {
        if (msg.window && msg.tab) {
          try {
            const tab = await api.tabs.get(msg.tab);
            return reply({ ok: true, window: tab.windowId, tab: tab.id, title: tab.title || '', url: tab.url || '' });
          } catch (_) {}
        }
        const words = String(msg.words || '').toLowerCase();
        const all = await api.tabs.query({});
        if (words) {
          const hit = all.find(t => (t.title || '').toLowerCase().includes(words) || (t.url || '').toLowerCase().includes(words));
          if (hit) return reply({ ok: true, window: hit.windowId, tab: hit.id, title: hit.title || '', url: hit.url || '' });
        }
        const active = (await api.tabs.query({ active: true, lastFocusedWindow: true }))[0] || all[0];
        if (!active) return reply({ ok: false, error: 'No tabs open' });
        return reply({ ok: true, window: active.windowId, tab: active.id, title: active.title || '', url: active.url || '' });
      }

      case 'open_page': {
        if (msg.agent) {
          if (msg.ownWindow) {
            try {
              const tab = await api.tabs.create({ windowId: msg.ownWindow, url: msg.url, active: true });
              return reply({ ok: true, window: tab.windowId, tab: tab.id, newWindow: false });
            } catch (_) {}
          }
          const win = await api.windows.create({ url: msg.url, focused: false });
          const tab = win.tabs?.[0];
          return reply({ ok: true, window: win.id, tab: tab?.id || 0, newWindow: true });
        }
        const wins = await api.windows.getAll({ populate: false });
        if (!wins.length) return reply({ ok: false, error: 'No browser window' });
        const tab = await api.tabs.create({ windowId: wins[0].id, url: msg.url, active: false });
        return reply({ ok: true, window: tab.windowId, tab: tab.id, newWindow: false });
      }

      case 'navigate_tab': {
        const tab = await api.tabs.get(msg.tab);
        if (msg.expectedUrl && tab.url !== msg.expectedUrl) {
          return reply({ ok: false, error: 'Atticus page changed' });
        }
        await api.tabs.update(msg.tab, { url: msg.url });
        return reply({ ok: true });
      }

      case 'close_window': {
        if (msg.window) await api.windows.remove(msg.window);
        return reply({ ok: true });
      }

      case 'show_tab': {
        if (msg.window) await api.windows.update(msg.window, { focused: true });
        if (msg.tab) await api.tabs.update(msg.tab, { active: true });
        return reply({ ok: true });
      }

      case 'exec_js': {
        const results = await api.scripting.executeScript({
          target: { tabId: msg.tab },
          world: 'MAIN',
          func: source => {
            // Evaluates Atticus's bundled `ui/web.js` expression in page context
            return (0, eval)(source);
          },
          args: [String(msg.code || '')],
        });
        const val = results?.[0]?.result;
        return reply({ ok: true, result: val === undefined || val === null ? '' : String(val) });
      }

      default:
        return reply({ ok: false, error: `Unknown command: ${msg?.cmd}` });
    }
  } catch (err) {
    return reply({ ok: false, error: String(err?.message || err) });
  }
}

connect();
