import 'dotenv/config';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';

export const ROOT = path.dirname(fileURLToPath(import.meta.url));

// OpenCode reads model-provider credentials straight from the environment of the
// process it runs in. The terminal deliberately starts with a tiny environment, so
// without this list a Space secret such as ANTHROPIC_API_KEY would never reach the
// CLI. OpenCode would then fall back to its OpenCode Zen free tier, which is
// limited per account *and* per network address, and therefore looks "already
// exhausted" on shared cloud hosts. Only these names are forwarded; site secrets
// (WEB_*, COOKIE_*, ...) never are.
export const PROVIDER_ENV_NAMES = Object.freeze([
  'ANTHROPIC_API_KEY',
  'OPENAI_API_KEY',
  'OPENROUTER_API_KEY',
  'GEMINI_API_KEY',
  'GOOGLE_GENERATIVE_AI_API_KEY',
  'GROQ_API_KEY',
  'MISTRAL_API_KEY',
  'DEEPSEEK_API_KEY',
  'XAI_API_KEY',
  'CEREBRAS_API_KEY',
  'TOGETHER_API_KEY',
  'FIREWORKS_API_KEY',
  'OPENCODE_API_KEY',
]);

const FORBIDDEN_ENV = /^(WEB_|COOKIE_|SESSION_TTL|FRAME_ANCESTORS|PUBLIC_URL|TRUST_PROXY|DATA_DIR|WORKSPACE_DIR|TMUX|OPENCODE_BIN|OPENCODE_MODEL|PROVIDER_ENV_ALLOWLIST)/;

function forbiddenEnvName(name) {
  return FORBIDDEN_ENV.test(name);
}

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
    if (!/^[A-Z_][A-Z0-9_]*$/i.test(name) || forbiddenEnvName(name)) {
      throw new Error(`Invalid or forbidden TERMINAL_ENV_ALLOWLIST entry: ${name}`);
    }
  }
  const extraProviders = (env.PROVIDER_ENV_ALLOWLIST || '').split(',').map(x => x.trim()).filter(Boolean);
  for (const name of extraProviders) {
    if (!/^[A-Z_][A-Z0-9_]*$/i.test(name) || forbiddenEnvName(name)) {
      throw new Error(`Invalid or forbidden PROVIDER_ENV_ALLOWLIST entry: ${name}`);
    }
  }
  const providerEnvNames = [...new Set([...PROVIDER_ENV_NAMES, ...extraProviders])];
  const defaultModel = (env.OPENCODE_MODEL || '').trim();
  if (defaultModel && !/^[A-Za-z0-9][A-Za-z0-9._/-]{2,127}$/.test(defaultModel)) {
    throw new Error('OPENCODE_MODEL must look like provider/model, for example anthropic/claude-sonnet-4-5');
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
    providerEnvNames,
    defaultModel,
  };
}

export function terminalEnvironment(config, env = process.env) {
  const environment = {
    PATH: env.PATH || '/usr/local/bin:/usr/bin:/bin',
    HOME: env.HOME || os.homedir(),
    USER: env.USER || os.userInfo().username,
    LOGNAME: env.LOGNAME || os.userInfo().username,
    LANG: env.LANG || 'C.UTF-8',
    TERM: 'xterm-256color',
    COLORTERM: 'truecolor',
  };
  for (const name of config.terminalEnvAllowlist || []) {
    if (env[name] !== undefined) environment[name] = env[name];
  }
  // Model-provider credentials only: these let the real OpenCode CLI authenticate
  // instead of depending on the shared Zen free tier. They stay inside the tmux
  // process; nothing here is ever serialized to the browser.
  for (const name of config.providerEnvNames || []) {
    if (env[name] !== undefined && environment[name] === undefined) environment[name] = env[name];
  }
  for (const name of ['LC_ALL', 'LC_CTYPE', 'TZ', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME']) {
    if (env[name]) environment[name] = env[name];
  }
  return environment;
}

// OpenCode reads its project config from WORKSPACE_DIR/opencode.json. When a
// default model is configured, record it once so every fresh Space container
// starts on a paid/authenticated model instead of the shared free tier.
export function applyWorkspaceDefaults(config) {
  if (!config.defaultModel) return null;
  const file = path.join(config.workspaceDir, 'opencode.json');
  let data;
  try {
    const raw = fs.readFileSync(file, 'utf8');
    data = raw.trim() ? JSON.parse(raw) : {};
  } catch (error) {
    if (error.code !== 'ENOENT') return null; // hand-edited JSONC: never rewrite it
    data = {};
  }
  if (!data || typeof data !== 'object' || Array.isArray(data)) return null;
  if (typeof data.model === 'string' && data.model) return data.model;
  data.$schema = data.$schema || 'https://opencode.ai/config.json';
  data.model = config.defaultModel;
  fs.writeFileSync(file, `${JSON.stringify(data, null, 2)}\n`, { mode: 0o600 });
  return config.defaultModel;
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
