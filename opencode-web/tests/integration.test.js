import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { once } from 'node:events';
import WebSocket from 'ws';
import { createApp } from '../server.js';
import { loadConfig, ROOT } from '../config.js';

const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

async function start(config) {
  const instance = await createApp({ config });
  instance.server.listen(0, '127.0.0.1');
  await once(instance.server, 'listening');
  const origin = `http://127.0.0.1:${instance.server.address().port}`;
  return { ...instance, origin };
}
async function request(instance, url, { method = 'GET', body, cookie, csrf, origin } = {}) {
  const headers = { Origin: origin || instance.origin };
  if (body) headers['Content-Type'] = 'application/json';
  if (cookie) headers.Cookie = cookie;
  if (csrf) headers['X-CSRF-Token'] = csrf;
  const response = await fetch(instance.origin + url, { method, headers, body: body ? JSON.stringify(body) : undefined });
  return { status: response.status, body: await response.json(), cookie: response.headers.get('set-cookie')?.split(';')[0] };
}
function attach(instance, cookie, csrf, sessionId) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(`${instance.origin.replace('http:', 'ws:')}/ws`, { headers: { Cookie: cookie, Origin: instance.origin } });
    const messages = [];
    let output = '';
    let attached = false;
    const timeout = setTimeout(() => { ws.close(); reject(new Error(`Timed out waiting for terminal output. Received: ${output.slice(-1000)}`)); }, 30_000);
    ws.on('open', () => ws.send(JSON.stringify({ type: 'attach', sessionId, csrf, cols: 100, rows: 30 })));
    ws.on('message', raw => {
      const message = JSON.parse(raw.toString());
      messages.push(message);
      if (message.type === 'attached') attached = true;
      if (message.type === 'replay' || message.type === 'output') output += message.data;
      // tmux paints an empty screen first; wait for the real OpenCode TUI.
      if (attached && /Ask anything|OpenCode|opencode/i.test(output)) { clearTimeout(timeout); resolve({ ws, messages, output }); }
    });
    ws.on('error', error => { clearTimeout(timeout); reject(error); });
  });
}

test('real OpenCode + tmux: auth, streaming, reconnect, web restart, stop', { timeout: 120_000 }, async () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'ocw-integration-'));
  const workspace = path.join(temp, 'workspace');
  fs.mkdirSync(workspace);
  const config = loadConfig({
    ...process.env, WEB_PASSWORD: 'integration-password-123', WEB_USERNAME: 'owner',
    WORKSPACE_DIR: workspace, DATA_DIR: path.join(temp, 'data'), PORT: '0',
    PUBLIC_URL: '', COOKIE_SECURE: 'false', COOKIE_SAME_SITE: 'strict', FRAME_ANCESTORS: "'none'",
    TMUX_BIN: process.env.TMUX_BIN || path.join(ROOT, '.tools/bin/tmux'),
  });
  let instance = await start(config);
  let sessionId;
  try {
    assert.equal((await request(instance, '/api/sessions')).status, 401);
    assert.equal((await request(instance, '/api/login', { method: 'POST', body: { username: 'owner', password: 'wrong' } })).status, 401);
    const login = await request(instance, '/api/login', { method: 'POST', body: { username: 'owner', password: config.password } });
    assert.equal(login.status, 200);
    const cookie = login.cookie, csrf = login.body.csrf;
    assert.equal((await request(instance, '/api/sessions', { method: 'POST', cookie, body: { cwd: '../' } })).status, 403);
    assert.equal((await request(instance, '/api/sessions', { method: 'POST', cookie, csrf: 'wrong', body: { cwd: '.' } })).status, 403);
    const created = await request(instance, '/api/sessions', { method: 'POST', cookie, csrf, body: { name: 'Integration', cwd: '.' } });
    assert.equal(created.status, 201);
    sessionId = created.body.session.id;
    const first = await attach(instance, cookie, csrf, sessionId);
    assert.match(first.output, /Ask anything|OpenCode|opencode/i);
    const pid = created.body.session.pid;
    assert.ok(pid > 0);
    first.ws.close();
    await delay(500);
    const second = await attach(instance, cookie, csrf, sessionId);
    assert.match(second.output, /Ask anything|OpenCode|opencode/i);
    second.ws.close();
    // Restart only the web bridge. The actual OpenCode/tmux PID must survive.
    await instance.close();
    instance = await start(config);
    const relogin = await request(instance, '/api/login', { method: 'POST', body: { username: 'owner', password: config.password } });
    const state = await request(instance, '/api/state', { cookie: relogin.cookie });
    const recovered = state.body.sessions.find(session => session.id === sessionId);
    assert.equal(recovered.status, 'running');
    assert.equal(recovered.pid, pid);
    const third = await attach(instance, relogin.cookie, relogin.body.csrf, sessionId);
    assert.match(third.output, /Ask anything|OpenCode|opencode/i);
    third.ws.close();
    const stopped = await request(instance, `/api/sessions/${sessionId}/stop`, { method: 'POST', cookie: relogin.cookie, csrf: relogin.body.csrf });
    assert.equal(stopped.body.session.status, 'stopped');
    const restarted = await request(instance, `/api/sessions/${sessionId}/restart`, { method: 'POST', cookie: relogin.cookie, csrf: relogin.body.csrf });
    assert.equal(restarted.body.session.status, 'running');
    const removed = await request(instance, `/api/sessions/${sessionId}`, { method: 'DELETE', cookie: relogin.cookie, csrf: relogin.body.csrf });
    assert.equal(removed.status, 200);
  } finally {
    if (sessionId) await instance.manager.remove(sessionId).catch(() => {});
    await instance.close();
    fs.rmSync(temp, { recursive: true, force: true });
  }
});
