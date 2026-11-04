import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { Auth, parseCookies } from '../auth.js';
import { ReplayBuffer } from '../terminal.js';
import { isInside, resolveWorkspaceDirectory } from '../config.js';

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
