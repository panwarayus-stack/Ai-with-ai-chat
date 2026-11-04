import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { loadConfig, terminalEnvironment } from '../config.js';

let failures = 0;
const check = (name, fn) => {
  try { console.log(`OK   ${name}: ${fn()}`); }
  catch (error) { failures++; console.error(`FAIL ${name}: ${error.message}`); }
};
const run = (command, args, env) => execFileSync(command, args, { encoding: 'utf8', timeout: 30_000, env, stdio: ['ignore', 'pipe', 'pipe'] }).trim();
const config = loadConfig();
const env = { ...process.env, ...terminalEnvironment(config) };
check('Node.js', () => process.versions.node);
check('OpenCode CLI', () => run(config.opencodeBin, ['--version'], env));
check('tmux', () => run(config.tmuxBin, ['-V'], env));
check('node-pty', () => { fs.accessSync(path.join(process.cwd(), 'node_modules', 'node-pty')); return 'installed'; });
check('workspace', () => { fs.accessSync(config.workspaceDir, fs.constants.R_OK | fs.constants.W_OK); return config.workspaceDir; });
check('private data directory', () => {
  fs.accessSync(config.dataDir, fs.constants.R_OK | fs.constants.W_OK);
  const mode = fs.statSync(config.dataDir).mode & 0o777;
  if (mode !== 0o700) throw new Error(`expected mode 0700, found ${mode.toString(8)}`);
  return config.dataDir;
});
check('authentication', () => { if (config.password.length < 16) throw new Error('password too short'); return 'configured (value hidden)'; });
check('public origin', () => config.publicUrl || 'derived from request; set PUBLIC_URL for production');
check('HTTPS cookies', () => config.secure ? 'Secure enabled' : 'disabled — use HTTPS before public deployment');
console.log('\nProvider credentials are managed by OpenCode. Run `opencode auth list` as the service user to inspect them.');
console.log('Hugging Face: check the Space logs and Settings → Variables and secrets; local: npm start.');
process.exitCode = failures ? 1 : 0;
