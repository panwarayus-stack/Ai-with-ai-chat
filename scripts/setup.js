import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { ROOT, findExecutable } from '../config.js';

const fail = message => { console.error(`FAIL: ${message}`); process.exitCode = 1; };
const run = (command, args) => execFileSync(command, args, { encoding: 'utf8', timeout: 30_000, stdio: ['ignore', 'pipe', 'pipe'] }).trim();

console.log('OpenCode Web setup');
const major = Number(process.versions.node.split('.')[0]);
if (major < 22 || major >= 25) fail(`Node.js 22–24 is required; found ${process.versions.node}`);
else console.log(`OK: Node.js ${process.versions.node}`);

const envPath = path.join(ROOT, '.env');
let generatedPassword = null;
if (!fs.existsSync(envPath)) {
  generatedPassword = crypto.randomBytes(24).toString('base64url');
  const workspace = process.env.WORKSPACE_DIR || path.join(os.homedir(), 'opencode-workspace');
  let template = fs.readFileSync(path.join(ROOT, '.env.example'), 'utf8');
  template = template.replace(/^WEB_PASSWORD=.*$/m, `WEB_PASSWORD=${generatedPassword}`);
  template = template.replace(/^WORKSPACE_DIR=.*$/m, `WORKSPACE_DIR=${workspace}`);
  fs.writeFileSync(envPath, template, { mode: 0o600 });
  console.log('OK: created private .env (0600)');
} else {
  fs.chmodSync(envPath, 0o600);
  console.log('OK: existing .env preserved');
}

// Read the generated/existing file without printing secrets.
const { parse } = await import('dotenv');
const env = { ...process.env, ...parse(fs.readFileSync(envPath)) };
const expand = value => value === '~' ? os.homedir() : value.startsWith('~/') ? path.join(os.homedir(), value.slice(2)) : value;
const workspace = path.resolve(expand(env.WORKSPACE_DIR || path.join(os.homedir(), 'opencode-workspace')));
const dataDir = path.resolve(ROOT, expand(env.DATA_DIR || './data'));
fs.mkdirSync(workspace, { recursive: true, mode: 0o700 });
fs.mkdirSync(dataDir, { recursive: true, mode: 0o700 });
fs.chmodSync(dataDir, 0o700);
console.log(`OK: workspace ${workspace}`);

// A conservative, project-local OpenCode policy. Existing configuration and
// skills are never overwritten. This is an approval guard, not an OS sandbox.
const policyPath = path.join(workspace, 'opencode.json');
if (!fs.existsSync(policyPath)) {
  fs.writeFileSync(policyPath, JSON.stringify({
    $schema: 'https://opencode.ai/config.json',
    permission: { external_directory: { '*': 'ask' } },
  }, null, 2) + '\n', { mode: 0o600 });
  console.log('OK: created workspace OpenCode policy (outside-directory access asks)');
} else console.log('OK: existing OpenCode configuration preserved');

try {
  const tmux = findExecutable('tmux', env.TMUX_BIN || '');
  console.log(`OK: ${run(tmux, ['-V'])} (${tmux})`);
} catch (error) { fail(error.message); }
try {
  const opencode = env.OPENCODE_BIN ? findExecutable('opencode', env.OPENCODE_BIN) : path.join(ROOT, 'node_modules', '.bin', 'opencode');
  console.log(`OK: OpenCode ${run(opencode, ['--version'])} (${opencode})`);
} catch (error) { fail(`OpenCode CLI: ${error.message}. Run npm ci first.`); }
try {
  const pty = await import('node-pty');
  if (typeof pty.spawn !== 'function') throw new Error('spawn export missing');
  console.log('OK: node-pty native addon loads');
} catch (error) { fail(`node-pty: ${error.message}`); }

if (generatedPassword) {
  console.log('\n--- Save these sign-in credentials now ---');
  console.log(`Username: ${env.WEB_USERNAME || 'owner'}`);
  console.log(`Password: ${generatedPassword}`);
  console.log('The password is also in .env on the server. It is never sent to browser code.');
}
console.log('\nNext: npm start for local development, or push this repository to a Hugging Face Gradio/ZeroGPU Space.');
console.log('For Hugging Face, set WEB_PASSWORD as a Space secret; do not commit .env.');
console.log('Provider setup: open a session and use OpenCode /connect, or allowlist provider secrets with TERMINAL_ENV_ALLOWLIST.');
console.log('Skills: unchanged (no skills were requested).');
