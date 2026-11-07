import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { EventEmitter } from 'node:events';
import { TerminalBridge } from './terminal.js';
import { resolveWorkspaceDirectory, terminalEnvironment } from './config.js';

const execFileAsync = promisify(execFile);
const shellQuote = value => `'${String(value).replaceAll("'", "'\\''")}'`;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export class SessionManager extends EventEmitter {
  constructor(config) {
    super();
    this.config = config;
    this.sessions = new Map();
    this.bridges = new Map();
    this.locks = new Set();
    this.closed = false;
    this.writeQueue = Promise.resolve();
    fs.mkdirSync(path.join(config.dataDir, 'sessions'), { recursive: true, mode: 0o700 });
    fs.chmodSync(config.dataDir, 0o700);
    this.file = path.join(config.dataDir, 'sessions.json');
    // A private named tmux server isolates these panes from the user's normal tmux.
    const namespace = crypto.createHash('sha256').update(config.dataDir).digest('hex').slice(0, 12);
    this.socketName = `opencode-web-${namespace}`;
    this.tmuxArgs = ['-L', this.socketName];
    this.load();
    // remain-on-exit preserves a failed TUI's real error output. Poll the pane
    // metadata so the sidebar still changes from running to exited.
    this.statusTimer = setInterval(() => {
      for (const id of this.sessions.keys()) this.refresh(id).catch(() => {});
    }, 5000);
    this.statusTimer.unref();
  }

  load() {
    try {
      const parsed = JSON.parse(fs.readFileSync(this.file, 'utf8'));
      if (!Array.isArray(parsed)) throw new Error('Session index must be an array');
      for (const item of parsed) {
        if (!UUID.test(item.id) || typeof item.cwd !== 'string' || typeof item.name !== 'string') throw new Error('Invalid session index entry');
        const session = { ...item, tmuxName: `ocw-${item.id}`, status: 'stopped', pid: null, exitCode: null };
        this.sessions.set(session.id, session);
      }
    } catch (error) {
      if (error.code !== 'ENOENT') throw new Error(`Cannot read ${this.file}: ${error.message}`, { cause: error });
    }
  }

  save() {
    const snapshot = JSON.stringify([...this.sessions.values()].map(({ bridge, ...session }) => session), null, 2);
    this.writeQueue = this.writeQueue.then(async () => {
      const temporary = `${this.file}.${process.pid}.tmp`;
      await fs.promises.writeFile(temporary, snapshot, { mode: 0o600 });
      await fs.promises.rename(temporary, this.file);
    });
    return this.writeQueue;
  }

  async tmux(args, { allowMissing = false } = {}) {
    try {
      const result = await execFileAsync(this.config.tmuxBin, [...this.tmuxArgs, ...args], {
        env: terminalEnvironment(this.config), timeout: 8000, maxBuffer: 1024 * 1024,
      });
      return result.stdout.trim();
    } catch (error) {
      const message = String(error.stderr || error.message);
      if (allowMissing && /no server running|can't find session|no current session|error connecting/i.test(message)) return null;
      throw new Error(`tmux: ${message.slice(0, 500)}`);
    }
  }

  async recover() {
    const listing = await this.tmux(['list-sessions', '-F', '#{session_name}'], { allowMissing: true });
    const live = new Set((listing || '').split('\n').filter(name => name.startsWith('ocw-')));
    for (const session of this.sessions.values()) {
      if (live.has(session.tmuxName)) await this.watch(session);
      else { session.status = 'stopped'; session.pid = null; }
    }
    await this.save();
  }

  async paneState(session) {
    const output = await this.tmux(['list-panes', '-t', session.tmuxName, '-F', '#{pane_dead}\t#{pane_dead_status}\t#{pane_pid}'], { allowMissing: true });
    if (!output) return null;
    const [dead, exitCode, pid] = output.split('\n')[0].split('\t');
    return { dead: dead === '1', exitCode: dead === '1' ? Number(exitCode) : null, pid: Number(pid) || null };
  }

  async watch(session) {
    const state = await this.paneState(session);
    if (!state) { session.status = 'stopped'; return; }
    session.status = state.dead ? 'exited' : 'running';
    session.pid = state.pid;
    session.exitCode = state.exitCode;
    let bridge = this.bridges.get(session.id);
    if (!bridge) {
      bridge = new TerminalBridge(session, this.config, this.tmuxArgs);
      this.bridges.set(session.id, bridge);
      bridge.on('error', error => this.emit('warning', `Terminal log: ${error.message}`));
      bridge.on('detach', async () => {
        try {
          const current = await this.refresh(session.id);
          if (!this.closed && this.bridges.get(session.id) === bridge && ['running', 'exited'].includes(current.status)) {
            setTimeout(() => { if (!this.closed && !bridge.closed) bridge.attach(); }, 1000).unref();
          }
        } catch { /* a deliberately killed pane needs no recorder */ }
      });
    }
    bridge.attach();
  }

  async refresh(id) {
    const session = this.get(id);
    const state = await this.paneState(session);
    const status = !state ? 'stopped' : state.dead ? 'exited' : 'running';
    if (session.status !== status || session.pid !== state?.pid) {
      session.status = status;
      session.pid = state?.pid ?? null;
      session.exitCode = state?.exitCode ?? null;
      await this.save();
      this.emit('change', this.public(session));
    }
    return session;
  }

  get(id) {
    if (!UUID.test(String(id))) throw Object.assign(new Error('Invalid session ID'), { status: 400 });
    const session = this.sessions.get(id);
    if (!session) throw Object.assign(new Error('Session not found'), { status: 404 });
    return session;
  }

  public(session) {
    return {
      id: session.id, name: session.name, cwd: session.cwd, status: session.status,
      createdAt: session.createdAt, updatedAt: session.updatedAt, pid: session.pid,
      exitCode: session.exitCode, cols: session.cols, rows: session.rows,
      viewers: this.bridges.get(session.id)?.clients.size || 0,
    };
  }

  list() { return [...this.sessions.values()].sort((a, b) => b.createdAt - a.createdAt).map(session => this.public(session)); }

  async create({ name, cwd = '.', cols = 120, rows = 36 } = {}) {
    if (this.closed) throw new Error('Session manager is closing');
    if (this.sessions.size >= this.config.maxSessions) throw Object.assign(
      new Error(`Session limit reached (${this.sessions.size}/${this.config.maxSessions}). Close a session with the × button, then create a new one. Raise MAX_SESSIONS to allow more.`),
      { status: 409, code: 'SESSION_LIMIT', details: { maxSessions: this.config.maxSessions } },
    );
    const directory = await resolveWorkspaceDirectory(this.config, cwd);
    const cleanName = typeof name === 'string' ? name.replace(/[\x00-\x1f\x7f]/g, '').trim().slice(0, 64) : '';
    const id = crypto.randomUUID();
    const now = Date.now();
    const session = {
      id, name: cleanName || `Session ${this.sessions.size + 1}`, cwd: directory,
      tmuxName: `ocw-${id}`, createdAt: now, updatedAt: now,
      status: 'starting', pid: null, exitCode: null,
      cols: Math.max(40, Math.min(300, Number(cols) || 120)),
      rows: Math.max(10, Math.min(100, Number(rows) || 36)),
    };
    this.sessions.set(id, session);
    try {
      await this.startPane(session, true);
      await this.watch(session);
      await this.save();
      this.emit('change', this.public(session));
      return this.public(session);
    } catch (error) {
      this.bridges.get(id)?.close();
      this.bridges.delete(id);
      this.sessions.delete(id);
      await this.tmux(['kill-session', '-t', session.tmuxName], { allowMissing: true }).catch(() => {});
      await fs.promises.rm(path.join(this.config.dataDir, 'sessions', id), { recursive: true, force: true });
      await this.save();
      throw error;
    }
  }

  async startPane(session, create = false) {
    // Only the fixed, server-selected OpenCode executable is launched. No browser
    // supplied shell command, executable, environment dump, or arbitrary flags.
    const command = [this.config.opencodeBin, session.cwd].map(shellQuote).join(' ');
    if (create) {
      // Establish remain-on-exit before launching OpenCode, so even an immediate
      // CLI startup failure leaves a pane with its real error output visible.
      await this.tmux(['new-session', '-d', '-s', session.tmuxName, '-c', session.cwd, '-x', String(session.cols), '-y', String(session.rows), '/bin/sleep 86400']);
      await this.tmux(['set-option', '-t', session.tmuxName, 'remain-on-exit', 'on']);
      await this.tmux(['set-option', '-s', 'default-terminal', 'tmux-256color']);
    }
    await this.tmux(['respawn-pane', '-k', '-t', session.tmuxName, '-c', session.cwd, command]);
    await this.tmux(['set-option', '-t', session.tmuxName, 'status', 'off']);
    await this.tmux(['set-option', '-t', session.tmuxName, 'mouse', 'on']);
    await this.tmux(['set-option', '-t', session.tmuxName, 'history-limit', '10000']);
    session.updatedAt = Date.now();
  }

  async attach(id, ws) {
    const session = await this.refresh(id);
    const bridge = this.bridges.get(id);
    if (!bridge || session.status === 'stopped') throw Object.assign(new Error('Session is stopped. Restart it first.'), { status: 409 });
    bridge.addClient(ws);
    return { session: this.public(session), bridge };
  }

  async stop(id) {
    return this.exclusive(id, async () => {
      const session = this.get(id);
      const state = await this.paneState(session);
      if (state && !state.dead) {
        // First ask the actual TUI to exit. Escalate only after a grace period.
        await this.tmux(['send-keys', '-t', session.tmuxName, 'C-c']);
        let exited = false;
        for (let i = 0; i < 20; i++) {
          await new Promise(resolve => setTimeout(resolve, 250));
          const current = await this.paneState(session);
          if (!current || current.dead) { exited = true; break; }
        }
        if (!exited) await this.tmux(['kill-session', '-t', session.tmuxName], { allowMissing: true });
      }
      // The recorder has already flushed the real output to disk. Remove the
      // tmux pane as well, including panes that had exited before Stop.
      await this.tmux(['kill-session', '-t', session.tmuxName], { allowMissing: true });
      const bridge = this.bridges.get(id);
      bridge?.close();
      await bridge?.flush();
      this.bridges.delete(id);
      session.status = 'stopped'; session.pid = null; session.updatedAt = Date.now();
      await this.save();
      this.emit('change', this.public(session));
      return this.public(session);
    });
  }

  async restart(id) {
    return this.exclusive(id, async () => {
      const session = this.get(id);
      const state = await this.paneState(session);
      if (state && !state.dead) throw Object.assign(new Error('Stop the running session before restarting it.'), { status: 409 });
      // Revalidate the directory: a symlink must not escape the workspace while stopped.
      session.cwd = await resolveWorkspaceDirectory(this.config, session.cwd);
      await this.startPane(session, !state);
      await this.watch(session);
      await this.save();
      this.emit('change', this.public(session));
      return this.public(session);
    });
  }

  async remove(id) {
    await this.stop(id);
    const bridge = this.bridges.get(id);
    if (bridge) await bridge.destroy();
    else await fs.promises.rm(path.join(this.config.dataDir, 'sessions', id), { recursive: true, force: true });
    this.bridges.delete(id);
    this.sessions.delete(id);
    await this.save();
    this.emit('change', null);
  }

  async exclusive(id, operation) {
    this.get(id);
    if (this.locks.has(id)) throw Object.assign(new Error('Another session operation is in progress'), { status: 409 });
    this.locks.add(id);
    try { return await operation(); } finally { this.locks.delete(id); }
  }

  async transcript(id) {
    const session = this.get(id);
    const bridge = this.bridges.get(id);
    await bridge?.flush();
    const dir = path.join(this.config.dataDir, 'sessions', session.id);
    const chunks = [];
    for (const file of ['terminal.log.1', 'terminal.log']) {
      try { chunks.push(await fs.promises.readFile(path.join(dir, file))); }
      catch (error) { if (error.code !== 'ENOENT') throw error; }
    }
    return Buffer.concat(chunks);
  }

  async close() {
    this.closed = true;
    clearInterval(this.statusTimer);
    // Detach recorders only. NEVER kill the tmux server on web-service shutdown.
    for (const bridge of this.bridges.values()) { bridge.close(); await bridge.flush(); }
    this.bridges.clear();
    await this.save();
  }
}
