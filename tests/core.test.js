import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { Auth, parseCookies } from '../auth.js';
import { ReplayBuffer } from '../terminal.js';
import { applyWorkspaceDefaults, isInside, loadConfig, resolveWorkspaceDirectory, terminalEnvironment } from '../config.js';
import { SessionManager } from '../sessions.js';

const config = {
  password: 'test-password-at-least-16', username: 'owner', sessionTtlMs: 60000,
  sameSite: 'strict', secure: false, publicUrl: 'https://code.example.com',
};

test('cookie parser ignores malformed keys and decodes values', () => {
  const parsed = parseCookies('ocw_session=abc%20def; bad key=x; empty=');
  assert.equal(parsed.ocw_session, 'abc def');
  assert.equal(parsed.empty, '');
  assert.equal(parsed['bad key'], undefined);
});

test('authentication verifies credentials, revokes tokens, and checks origin/CSRF', () => {
  const auth = new Auth(config);
  assert.equal(auth.verify('owner', config.password), true);
  assert.equal(auth.verify('owner', 'wrong'), false);
  const session = auth.create();
  assert.equal(auth.get(session.token).csrf, session.csrf);
  assert.equal(auth.validOrigin({ headers: { origin: 'https://code.example.com' } }), true);
  assert.equal(auth.validOrigin({ headers: { origin: 'https://evil.example' } }), false);
  const middleware = auth.middleware();
  let status = null;
  const res = { status(code) { status = code; return this; }, json() { return this; } };
  middleware({ method: 'POST', headers: { cookie: `ocw_session=${session.token}`, origin: 'https://code.example.com', 'x-csrf-token': 'wrong' } }, res, () => {});
  assert.equal(status, 403);
  auth.revoke(session.token);
  assert.equal(auth.get(session.token), null);
  auth.close();
});

test('replay buffer is bounded and preserves recent real output', () => {
  const replay = new ReplayBuffer(10);
  replay.append('12345');
  replay.append('67890');
  replay.append('abc');
  assert.equal(replay.toString(), '67890abc');
  assert.ok(replay.bytes <= 10);
});

test('workspace resolution rejects traversal and symlink escapes', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ocw-workspace-'));
  const outside = fs.mkdtempSync(path.join(os.tmpdir(), 'ocw-outside-'));
  fs.mkdirSync(path.join(root, 'project'));
  fs.symlinkSync(outside, path.join(root, 'escape'));
  const cfg = { workspaceDir: root };
  assert.equal(await resolveWorkspaceDirectory(cfg, 'project'), path.join(root, 'project'));
  await assert.rejects(resolveWorkspaceDirectory(cfg, '../'), /outside/);
  await assert.rejects(resolveWorkspaceDirectory(cfg, 'escape'), /outside/);
  assert.equal(isInside(root, root), true);
  assert.equal(isInside(root, path.join(root, 'a')), true);
  assert.equal(isInside(root, root + '-sibling'), false);
  fs.rmSync(root, { recursive: true, force: true });
  fs.rmSync(outside, { recursive: true, force: true });
});

function baseEnv(root) {
  return {
    WEB_PASSWORD: 'test-password-at-least-16', WEB_USERNAME: 'owner',
    WORKSPACE_DIR: root, DATA_DIR: path.join(root, 'data'),
    TMUX_BIN: '/bin/true', OPENCODE_BIN: '/bin/true',
  };
}

test('provider credentials reach OpenCode while site secrets stay out', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ocw-config-'));
  const env = {
    ...baseEnv(root),
    ANTHROPIC_API_KEY: 'sk-ant-test', OPENROUTER_API_KEY: 'sk-or-test',
    PROVIDER_ENV_ALLOWLIST: 'MY_PROVIDER_API_KEY', MY_PROVIDER_API_KEY: 'extra',
    OPENCODE_MODEL: 'anthropic/claude-sonnet-4-5', UNRELATED_SECRET: 'nope',
  };
  const config = loadConfig(env);
  assert.equal(config.maxSessions, 8);
  assert.deepEqual(config.providerEnvNames.filter(name => env[name]).sort(),
    ['ANTHROPIC_API_KEY', 'MY_PROVIDER_API_KEY', 'OPENROUTER_API_KEY']);
  const terminal = terminalEnvironment(config, env);
  assert.equal(terminal.ANTHROPIC_API_KEY, 'sk-ant-test');
  assert.equal(terminal.MY_PROVIDER_API_KEY, 'extra');
  assert.equal(terminal.TERM, 'xterm-256color');
  assert.equal(terminal.WEB_PASSWORD, undefined);
  assert.equal(terminal.UNRELATED_SECRET, undefined);
  fs.rmSync(root, { recursive: true, force: true });
});

test('provider allowlist rejects site secrets and invalid model ids', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ocw-config-'));
  assert.throws(() => loadConfig({ ...baseEnv(root), PROVIDER_ENV_ALLOWLIST: 'WEB_PASSWORD' }), /forbidden/);
  assert.throws(() => loadConfig({ ...baseEnv(root), PROVIDER_ENV_ALLOWLIST: 'COOKIE_SECURE' }), /forbidden/);
  assert.throws(() => loadConfig({ ...baseEnv(root), OPENCODE_MODEL: 'not a model!' }), /OPENCODE_MODEL/);
  fs.rmSync(root, { recursive: true, force: true });
});

test('default model is written once and never overwrites the project config', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ocw-config-'));
  const config = loadConfig({ ...baseEnv(root), OPENCODE_MODEL: 'anthropic/claude-sonnet-4-5' });
  assert.equal(applyWorkspaceDefaults(config), 'anthropic/claude-sonnet-4-5');
  const file = path.join(root, 'opencode.json');
  const written = JSON.parse(fs.readFileSync(file, 'utf8'));
  assert.equal(written.model, 'anthropic/claude-sonnet-4-5');
  assert.equal(written.$schema, 'https://opencode.ai/config.json');
  fs.writeFileSync(file, JSON.stringify({ model: 'openai/gpt-5' }));
  assert.equal(applyWorkspaceDefaults(config), 'openai/gpt-5');
  assert.equal(JSON.parse(fs.readFileSync(file, 'utf8')).model, 'openai/gpt-5');
  // Without OPENCODE_MODEL nothing is written at all.
  const plain = loadConfig(baseEnv(root));
  assert.equal(applyWorkspaceDefaults(plain), null);
  fs.rmSync(root, { recursive: true, force: true });
});

test('session cap reports an actionable, typed error instead of a bare limit', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ocw-sessions-'));
  const manager = new SessionManager({
    workspaceDir: root, dataDir: path.join(root, 'data'), tmuxBin: '/bin/true',
    maxSessions: 1, opencodeBin: '/bin/true', replayBufferBytes: 65536, logFileBytes: 1048576,
  });
  manager.sessions.set('00000000-0000-4000-8000-000000000000', { id: '00000000-0000-4000-8000-000000000000' });
  await assert.rejects(manager.create({ name: 'extra' }), error => {
    assert.equal(error.status, 409);
    assert.equal(error.code, 'SESSION_LIMIT');
    assert.equal(error.details.maxSessions, 1);
    assert.match(error.message, /Session limit reached \(1\/1\)/);
    return true;
  });
  manager.closed = true;
  clearInterval(manager.statusTimer);
  fs.rmSync(root, { recursive: true, force: true });
});
