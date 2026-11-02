import 'dotenv/config';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';

export const ROOT = path.dirname(fileURLToPath(import.meta.url));

function expandHome(value) {
  if (value === '~') return os.homedir();
  if (value.startsWith('~/')) return path.join(os.homedir(), value.slice(2));
  return value;
}

function integer(name, fallback, min, max, env = process.env) {
  const raw = env[name] ?? String(fallback);
  const value = Number(raw);
  if (!Number.isInteger(value) || value < min || value > max) {
    throw new Error(`${name} must be an integer between ${min} and ${max}`);
  }
  return value;
}

function boolean(name, fallback = false, env = process.env) {
  const raw = env[name] ?? String(fallback);
  if (!['true', 'false', '1', '0'].includes(raw)) throw new Error(`${name} must be true or false`);
  return raw === 'true' || raw === '1';
}

export function findExecutable(name, explicit = '') {
  const candidates = explicit
    ? [expandHome(explicit)]
    : [path.join(ROOT, '.tools', 'bin', name), name];
  for (const candidate of candidates) {
    try {
      if (path.isAbsolute(candidate)) {
        fs.accessSync(candidate, fs.constants.X_OK);
        return candidate;
      }
      const found = execFileSync('/bin/sh', ['-c', 'command -v "$1"', 'sh', candidate], {
        encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], timeout: 3000,
      }).trim();
      if (found) return found;
    } catch { /* try next candidate */ }
  }
  throw new Error(`${name} is not installed or executable. Run npm run setup / see README.md`);
}

export function loadConfig(env = process.env) {
  const password = env.WEB_PASSWORD ?? '';
  if (password.length < 16 || password.length > 256) {
    throw new Error('WEB_PASSWORD must contain 16–256 characters. Run npm run setup.');
  }
  const username = env.WEB_USERNAME || 'owner';
  if (!/^[a-zA-Z0-9_.-]{1,64}$/.test(username)) throw new Error('WEB_USERNAME is invalid');
  const workspaceDir = fs.realpathSync(expandHome(env.WORKSPACE_DIR || path.join(os.homedir(), 'opencode-workspace')));
  if (!fs.statSync(workspaceDir).isDirectory()) throw new Error('WORKSPACE_DIR must be a directory');
  const dataDir = path.resolve(ROOT, expandHome(env.DATA_DIR || './data'));
  const sameSite = (env.COOKIE_SAME_SITE || 'strict').toLowerCase();
  if (!['strict', 'lax', 'none'].includes(sameSite)) throw new Error('COOKIE_SAME_SITE is invalid');
  const secure = boolean('COOKIE_SECURE', false, env);
  if (sameSite === 'none' && !secure) throw new Error('SameSite=None requires COOKIE_SECURE=true and HTTPS');
  let publicUrl = '';
  if (env.PUBLIC_URL) {
    const url = new URL(env.PUBLIC_URL);
    if (!['http:', 'https:'].includes(url.protocol) || url.pathname !== '/' || url.search || url.hash || url.username || url.password) {
      throw new Error('PUBLIC_URL must be an http(s) origin without credentials, path, query, or fragment');
    }
    publicUrl = url.origin;
    if (secure && url.protocol !== 'https:') throw new Error('COOKIE_SECURE=true requires an https PUBLIC_URL');
  }
  const allowlist = (env.TERMINAL_ENV_ALLOWLIST || '').split(',').map(x => x.trim()).filter(Boolean);
  for (const name of allowlist) {
    if (!/^[A-Z_][A-Z0-9_]*$/i.test(name) || /^(WEB_|COOKIE_|SESSION_TTL|FRAME_ANCESTORS|PUBLIC_URL|TRUST_PROXY|DATA_DIR|WORKSPACE_DIR|TMUX|OPENCODE_BIN)/.test(name)) {
      throw new Error(`Invalid or forbidden TERMINAL_ENV_ALLOWLIST entry: ${name}`);
    }
  }
  return {
    host: env.HOST || '0.0.0.0',
    port: integer('PORT', 3000, 0, 65535, env),
    username, password, workspaceDir, dataDir,
    opencodeBin: env.OPENCODE_BIN ? findExecutable('opencode', env.OPENCODE_BIN) : path.join(ROOT, 'node_modules', '.bin', 'opencode'),
    tmuxBin: findExecutable('tmux', env.TMUX_BIN || ''),
    publicUrl,
    trustProxy: integer('TRUST_PROXY', 0, 0, 10, env),
    sameSite, secure,
    sessionTtlMs: integer('SESSION_TTL_HOURS', 12, 1, 168, env) * 3600_000,
    frameAncestors: (env.FRAME_ANCESTORS || "'none'").split(/\s+/).filter(Boolean),
    replayBufferBytes: integer('REPLAY_BUFFER_BYTES', 2 * 1024 * 1024, 65536, 8 * 1024 * 1024, env),
    logFileBytes: integer('LOG_FILE_BYTES', 16 * 1024 * 1024, 1024 * 1024, 64 * 1024 * 1024, env),
    maxSessions: integer('MAX_SESSIONS', 8, 1, 32, env),
    terminalEnvAllowlist: allowlist,
  };
}

export function terminalEnvironment(config) {
  const env = {
    PATH: process.env.PATH || '/usr/local/bin:/usr/bin:/bin',
    HOME: process.env.HOME || os.homedir(),
    USER: process.env.USER || os.userInfo().username,
    LOGNAME: process.env.LOGNAME || os.userInfo().username,
    LANG: process.env.LANG || 'C.UTF-8',
    TERM: 'xterm-256color',
    COLORTERM: 'truecolor',
  };
  for (const name of config.terminalEnvAllowlist) {
    if (process.env[name] !== undefined) env[name] = process.env[name];
  }
  for (const name of ['LC_ALL', 'LC_CTYPE', 'TZ', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME']) {
    if (process.env[name]) env[name] = process.env[name];
  }
  return env;
}

export function isInside(parent, child) {
  const relative = path.relative(parent, child);
  return relative === '' || (!relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative));
}

export async function resolveWorkspaceDirectory(config, requested = '.') {
  if (typeof requested !== 'string' || requested.length > 1024 || requested.includes('\0')) {
    throw Object.assign(new Error('Invalid working directory'), { status: 400 });
  }
  const target = path.resolve(config.workspaceDir, expandHome(requested));
  if (!isInside(config.workspaceDir, target)) {
    throw Object.assign(new Error('Working directory is outside WORKSPACE_DIR'), { status: 403 });
  }
  const fsAsync = fs.promises;
  let real;
  try { real = await fsAsync.realpath(target); }
  catch { throw Object.assign(new Error('Working directory does not exist'), { status: 400 }); }
  if (!isInside(config.workspaceDir, real) || !(await fsAsync.stat(real)).isDirectory()) {
    throw Object.assign(new Error('Working directory resolves outside WORKSPACE_DIR'), { status: 403 });
  }
  return real;
}
