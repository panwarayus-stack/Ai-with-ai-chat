/* global Terminal, FitAddon */
const $ = selector => document.querySelector(selector);
const state = { csrf: '', workspace: '', sessions: [], maxSessions: 8, notices: [], selected: null, ws: null, attached: false, reconnectTimer: null, reconnectDelay: 500, intentionalClose: false, history: [], historyIndex: -1, draft: '' };
const term = new Terminal({
  cursorBlink: true, fontFamily: '"SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace',
  fontSize: 13, lineHeight: 1.25, scrollback: 10000, convertEol: false, allowProposedApi: false,
  theme: { background: '#141413', foreground: '#e8e6df', cursor: '#e3ae83', cursorAccent: '#141413', selectionBackground: '#4a4035', black: '#141413', red: '#db8d83', green: '#91bd93', yellow: '#d3b574', blue: '#8da9c4', magenta: '#b49ac4', cyan: '#8bbcb5', white: '#e8e6df', brightBlack: '#77756d', brightRed: '#eaa99f', brightGreen: '#acd0ad', brightYellow: '#e2c98f', brightBlue: '#a5bed5', brightMagenta: '#cbb3d8', brightCyan: '#a6d0ca', brightWhite: '#f5f3ec' },
});
const fitAddon = new FitAddon.FitAddon();
term.loadAddon(fitAddon);
term.open($('#terminal'));
term.onData(data => sendInput(data));
term.onResize(({ cols, rows }) => {
  $('#terminal-size').textContent = `${cols} × ${rows}`;
  if (state.attached) send({ type: 'resize', cols, rows });
});
new ResizeObserver(() => requestAnimationFrame(fit)).observe($('#terminal'));

function fit() {
  if (!$('#terminal').clientWidth || !$('#terminal').clientHeight) return;
  try { fitAddon.fit(); } catch { /* hidden during mobile layout */ }
}
function send(message) {
  if (state.ws?.readyState === WebSocket.OPEN) { state.ws.send(JSON.stringify(message)); return true; }
  return false;
}
function sendInput(data) {
  if (!state.selected || !state.attached || !send({ type: 'input', data })) toast('Terminal is not connected', true);
}
async function api(url, options = {}) {
  const headers = { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers };
  if (options.method && options.method !== 'GET') headers['X-CSRF-Token'] = state.csrf;
  const response = await fetch(url, { ...options, headers, credentials: 'same-origin' });
  if (response.status === 401) { location.replace('/login'); throw new Error('Sign in required'); }
  const body = await response.json();
  if (!response.ok) {
    const error = new Error(body.error || 'Request failed');
    error.code = body.code; error.status = response.status;
    throw error;
  }
  return body;
}
let toastTimer;
function toast(message, error = false) {
  const element = $('#toast');
  element.textContent = message;
  element.classList.toggle('error', error);
  element.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { element.hidden = true; }, 4500);
}
function setConnection(status, label) {
  $('#connection-dot').className = `status-dot ${status}`;
  $('#connection-label').textContent = label;
}
function currentSession() { return state.sessions.find(session => session.id === state.selected); }
function renderSessions() {
  const list = $('#session-list');
  list.replaceChildren();
  $('#session-count').textContent = `${state.sessions.length}/${state.maxSessions}`;
  if (!state.sessions.length) {
    const empty = document.createElement('p'); empty.className = 'session-list-empty'; empty.textContent = 'No sessions yet.'; list.append(empty);
  }
  for (const session of state.sessions) {
    const button = document.createElement('button');
    button.className = `session-item${session.id === state.selected ? ' active' : ''}`;
    button.setAttribute('aria-current', session.id === state.selected ? 'true' : 'false');
    const title = document.createElement('span'); title.className = 'session-item-title';
    const dot = document.createElement('span'); dot.className = `status-dot ${session.status === 'running' ? 'running' : session.status === 'exited' ? 'error' : ''}`;
    const name = document.createElement('span'); name.className = 'session-item-name'; name.textContent = session.name;
    title.append(dot, name);
    const meta = document.createElement('span'); meta.className = 'session-item-meta'; meta.textContent = `${session.status} · ${session.cwd.split('/').pop() || '/'}`;
    button.append(title, meta);
    button.addEventListener('click', () => { selectSession(session.id); closeSidebar(); });
    list.append(button);
  }
  renderToolbar();
}
function renderToolbar() {
  const session = currentSession();
  $('#current-session-name').textContent = session?.name || 'No session selected';
  $('#current-session-status').textContent = session?.status || '';
  $('#current-session-status').className = `session-state ${session?.status || ''}`;
  $('#current-cwd').textContent = session?.cwd || '—';
  $('#stop-session').disabled = !session || session.status !== 'running';
  $('#restart-session').disabled = !session || session.status === 'running';
  $('#close-session').disabled = !session;
  $('#download-transcript').disabled = !session;
  $('#prompt').disabled = !session || session.status !== 'running';
  $('#send-prompt').disabled = $('#prompt').disabled;
  $('#empty-state').hidden = Boolean(session);
  const capped = atSessionLimit();
  const capMessage = capped ? `Session limit reached (${state.sessions.length}/${state.maxSessions}). Close a session first.` : '';
  for (const selector of ['#new-session-top', '#new-session-sidebar', '#new-session-empty']) {
    $(selector).title = capMessage;
  }
}
function atSessionLimit() { return state.sessions.length >= state.maxSessions; }
function renderNotices() {
  const bar = $('#notice-bar');
  bar.replaceChildren();
  if (!state.notices.length) { bar.hidden = true; return; }
  for (const notice of state.notices) {
    const row = document.createElement('div');
    row.className = `notice notice-${notice.level || 'info'}`;
    const text = document.createElement('span');
    text.textContent = notice.text;
    const dismiss = document.createElement('button');
    dismiss.type = 'button'; dismiss.className = 'notice-dismiss'; dismiss.textContent = '×';
    dismiss.setAttribute('aria-label', 'Dismiss notice');
    dismiss.addEventListener('click', () => {
      state.notices = state.notices.filter(item => item !== notice);
      renderNotices();
    });
    row.append(text, dismiss);
    bar.append(row);
  }
  bar.hidden = false;
}
async function refreshState() {
  const data = await api('/api/state');
  state.workspace = data.workspace;
  state.sessions = data.sessions;
  state.maxSessions = Number(data.maxSessions) || state.maxSessions;
  state.notices = Array.isArray(data.notices) ? data.notices : [];
  $('#header-cwd').textContent = data.workspace;
  $('#workspace-label').textContent = data.workspace;
  renderSessions();
  renderNotices();
}
function loadHistory() {
  try { state.history = JSON.parse(localStorage.getItem(`ocw-history-${state.selected}`) || '[]').slice(-100); } catch { state.history = []; }
  state.historyIndex = -1;
  $('#prompt').value = sessionStorage.getItem(`ocw-draft-${state.selected}`) || '';
}
function saveHistory() {
  try { localStorage.setItem(`ocw-history-${state.selected}`, JSON.stringify(state.history.slice(-100))); } catch { /* private browsing / quota */ }
}
function selectSession(id) {
  if (state.selected === id && state.ws?.readyState === WebSocket.OPEN) return;
  state.intentionalClose = true;
  state.attached = false;
  state.ws?.close(1000, 'Switching session');
  clearTimeout(state.reconnectTimer);
  state.selected = id;
  try { localStorage.setItem('ocw-selected', id); } catch { /* optional */ }
  term.reset();
  loadHistory();
  renderSessions();
  state.reconnectDelay = 500;
  connect();
}
function connect() {
  if (!state.selected) return;
  clearTimeout(state.reconnectTimer);
  state.intentionalClose = false;
  state.attached = false;
  setConnection('connecting', 'Connecting');
  $('#overlay-message').textContent = 'Connecting to persistent session…';
  $('#terminal-overlay').hidden = false;
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${protocol}//${location.host}/ws`);
  state.ws = ws;
  ws.addEventListener('open', () => {
    // Attach must be the first frame; fit()/resize is sent after attached.
    ws.send(JSON.stringify({ type: 'attach', sessionId: state.selected, csrf: state.csrf, cols: term.cols, rows: term.rows }));
  });
  ws.addEventListener('message', event => {
    let message;
    try { message = JSON.parse(event.data); } catch { return; }
    if (message.type === 'replay') { term.reset(); term.write(message.data); }
    else if (message.type === 'output') term.write(message.data);
    else if (message.type === 'attached') {
      state.attached = true;
      state.reconnectDelay = 500;
      setConnection('connected', 'Connected');
      $('#terminal-overlay').hidden = true;
      const index = state.sessions.findIndex(session => session.id === message.session.id);
      if (index >= 0) state.sessions[index] = message.session;
      renderSessions();
      fit();
    } else if (message.type === 'sessions') { state.sessions = message.sessions; renderSessions(); }
    else if (message.type === 'error') toast(message.error, true);
  });
  ws.addEventListener('close', event => {
    if (state.ws !== ws) return;
    state.attached = false;
    setConnection('error', 'Disconnected');
    $('#prompt').disabled = true; $('#send-prompt').disabled = true;
    if (state.intentionalClose) return;
    if (event.code === 1008) {
      $('#overlay-message').textContent = 'Connection rejected. Sign in again or restart this session.';
      $('#terminal-overlay').hidden = false;
      refreshState().catch(() => {});
      return;
    }
    $('#overlay-message').textContent = 'Reconnecting… your OpenCode session is still running on the server.';
    $('#terminal-overlay').hidden = false;
    state.reconnectTimer = setTimeout(connect, state.reconnectDelay);
    state.reconnectDelay = Math.min(state.reconnectDelay * 2, 10000);
  });
  ws.addEventListener('error', () => setConnection('error', 'Connection error'));
}
function openNewSession() {
  if (atSessionLimit()) {
    toast(`Session limit reached (${state.sessions.length}/${state.maxSessions}). Close a session with × first.`, true);
    return;
  }
  $('#new-session-error').hidden = true;
  $('#new-session-dialog').showModal();
  $('#session-name').focus();
}
function confirmAction(title, message, action) {
  return new Promise(resolve => {
    const dialog = $('#confirm-dialog');
    $('#confirm-title').textContent = title;
    $('#confirm-message').textContent = message;
    $('#confirm-action').textContent = action;
    dialog.returnValue = 'cancel';
    dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), { once: true });
    dialog.showModal();
  });
}
async function sessionAction(action) {
  const session = currentSession();
  if (!session) return;
  const messages = {
    stop: ['Stop OpenCode?', 'The running task may be interrupted. Terminal output is kept; you can restart this session.', 'Stop session'],
    restart: ['Restart OpenCode?', 'A new OpenCode TUI opens in the same directory. Use /sessions inside OpenCode to resume a native conversation.', 'Restart'],
    remove: ['Close this session?', 'Stops OpenCode and removes this web session and its terminal transcript. Native OpenCode conversation history is not deleted.', 'Close session'],
  };
  if (!await confirmAction(...messages[action])) return;
  try {
    if (action === 'remove') {
      await api(`/api/sessions/${session.id}`, { method: 'DELETE' });
      state.intentionalClose = true; state.ws?.close(1000); state.selected = null; term.reset();
      try { localStorage.removeItem('ocw-selected'); } catch { /* optional */ }
    } else await api(`/api/sessions/${session.id}/${action}`, { method: 'POST' });
    await refreshState();
    if (state.selected) { state.intentionalClose = true; state.ws?.close(1000); connect(); }
    toast(action === 'remove' ? 'Session closed' : action === 'stop' ? 'Session stopped' : 'Session restarted');
  } catch (error) { toast(error.message, true); }
}
function closeSidebar() { $('#sidebar').classList.remove('open'); $('#sidebar-backdrop').hidden = true; $('#sidebar-toggle').setAttribute('aria-expanded', 'false'); }

$('#new-session-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = $('#create-session-submit');
  button.disabled = true;
  $('#new-session-error').hidden = true;
  try {
    const { session } = await api('/api/sessions', { method: 'POST', body: JSON.stringify({ name: $('#session-name').value, cwd: $('#session-directory').value, cols: term.cols, rows: term.rows }) });
    $('#new-session-dialog').close();
    $('#new-session-form').reset(); $('#session-directory').value = '.';
    await refreshState(); selectSession(session.id); closeSidebar();
  } catch (error) { $('#new-session-error').textContent = error.message; $('#new-session-error').hidden = false; }
  finally { button.disabled = false; }
});
$('#composer').addEventListener('submit', event => {
  event.preventDefault();
  const text = $('#prompt').value.trim();
  if (!text) return;
  if (!state.attached || state.ws?.readyState !== WebSocket.OPEN) return toast('Terminal is reconnecting', true);
  // Bracketed paste preserves multiline prompts in the real TUI; CR submits it.
  sendInput(`\x1b[200~${text}\x1b[201~`);
  setTimeout(() => sendInput('\r'), 80);
  state.history.push(text); state.history = state.history.slice(-100); saveHistory();
  state.historyIndex = -1; $('#prompt').value = ''; sessionStorage.removeItem(`ocw-draft-${state.selected}`);
});
$('#prompt').addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); $('#composer').requestSubmit(); return; }
  const input = event.currentTarget;
  if (event.key === 'ArrowUp' && (input.selectionStart === 0 || !input.value)) {
    event.preventDefault();
    if (!state.history.length) return;
    if (state.historyIndex === -1) { state.draft = input.value; state.historyIndex = state.history.length; }
    state.historyIndex = Math.max(0, state.historyIndex - 1); input.value = state.history[state.historyIndex];
  } else if (event.key === 'ArrowDown' && input.selectionStart === input.value.length && state.historyIndex !== -1) {
    event.preventDefault(); state.historyIndex++;
    if (state.historyIndex >= state.history.length) { state.historyIndex = -1; input.value = state.draft; }
    else input.value = state.history[state.historyIndex];
  }
});
$('#prompt').addEventListener('input', event => { if (state.selected) sessionStorage.setItem(`ocw-draft-${state.selected}`, event.target.value); });
$('#clear-history').addEventListener('click', () => { state.history = []; state.historyIndex = -1; saveHistory(); toast('Prompt history cleared on this device'); });
document.querySelectorAll('[data-key]').forEach(button => button.addEventListener('click', () => {
  const keys = { esc: '\x1b', tab: '\t', 'ctrl-c': '\x03', left: '\x1b[D', up: '\x1b[A', down: '\x1b[B', right: '\x1b[C' };
  sendInput(keys[button.dataset.key]); term.focus();
}));
$('#focus-terminal').addEventListener('click', () => term.focus());
$('#reconnect-now').addEventListener('click', () => { state.reconnectDelay = 500; connect(); });
$('#stop-session').addEventListener('click', () => sessionAction('stop'));
$('#restart-session').addEventListener('click', () => sessionAction('restart'));
$('#close-session').addEventListener('click', () => sessionAction('remove'));
$('#download-transcript').addEventListener('click', () => { if (state.selected) location.href = `/api/sessions/${state.selected}/transcript`; });
['#new-session-top', '#new-session-sidebar', '#new-session-empty'].forEach(selector => $(selector).addEventListener('click', openNewSession));
document.querySelectorAll('[data-close-dialog]').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
$('#sidebar-toggle').addEventListener('click', () => {
  const open = $('#sidebar').classList.toggle('open'); $('#sidebar-backdrop').hidden = !open; $('#sidebar-toggle').setAttribute('aria-expanded', String(open));
});
$('#sidebar-backdrop').addEventListener('click', closeSidebar);
$('#logout').addEventListener('click', async () => {
  try { await api('/api/logout', { method: 'POST' }); } catch { /* still leave the page */ }
  state.intentionalClose = true; state.ws?.close(1000); location.replace('/login');
});
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) {
    api('/api/me').then(data => { state.csrf = data.csrf; }).catch(() => {});
    if (state.selected && (!state.ws || state.ws.readyState === WebSocket.CLOSED)) connect();
    fit();
  }
});
window.addEventListener('online', () => { if (state.selected && (!state.ws || state.ws.readyState === WebSocket.CLOSED)) connect(); });

(async () => {
  try {
    const me = await api('/api/me'); state.csrf = me.csrf;
    await refreshState();
    let saved; try { saved = localStorage.getItem('ocw-selected'); } catch { /* optional */ }
    const initial = state.sessions.find(session => session.id === saved) || state.sessions[0];
    if (initial) selectSession(initial.id);
    else { setConnection('connected', 'Signed in'); fit(); }
  } catch (error) { toast(error.message, true); }
})();
