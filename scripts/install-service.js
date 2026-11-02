import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { execFileSync } from 'node:child_process';
import { ROOT } from '../config.js';

if (process.platform !== 'linux') throw new Error('The systemd installer requires Linux.');
if (process.getuid?.() === 0) throw new Error('Run as the non-root service user with sudo available, not as root.');
const user = os.userInfo().username;
const group = os.userInfo().gid;
const template = fs.readFileSync(path.join(ROOT, 'deploy', 'opencode-web.service.template'), 'utf8');
const unit = template.replaceAll('__USER__', user).replaceAll('__GROUP__', String(group)).replaceAll('__DIR__', ROOT).replaceAll('__NODE__', process.execPath);
const target = '/etc/systemd/system/opencode-web.service';
const sudo = (args, options = {}) => execFileSync('sudo', args, { stdio: 'inherit', ...options });
// Review the generated unit before installation: it runs the web bridge as the
// current non-root user and deliberately does not kill detached tmux panes.
console.log(unit);
sudo(['tee', target], { input: unit, stdio: ['pipe', 'ignore', 'inherit'] });
sudo(['systemctl', 'daemon-reload']);
sudo(['systemctl', 'enable', 'opencode-web']);
sudo(['systemctl', 'restart', 'opencode-web']);
sudo(['systemctl', 'is-enabled', 'opencode-web']);
sudo(['systemctl', 'is-active', 'opencode-web']);
console.log('OK: opencode-web is enabled and active under systemd.');
