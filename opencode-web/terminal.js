import { EventEmitter } from 'node:events';
import fs from 'node:fs';
import path from 'node:path';
import * as pty from 'node-pty';
import { terminalEnvironment } from './config.js';

/** A bounded, chunk-aware ANSI replay buffer. Raw keystrokes are never logged here. */
export class ReplayBuffer {
  constructor(limit) { this.limit = limit; this.chunks = []; this.bytes = 0; }
  append(chunk) {
    const buffer = Buffer.from(chunk);
    if (!buffer.length) return;
    this.chunks.push(buffer);
    this.bytes += buffer.length;
    // Drop whole chunks rather than cutting a UTF-8 character or an ANSI escape
    // sequence in half. The last chunk is never larger than one PTY read.
    while (this.bytes > this.limit && this.chunks.length > 1) {
      this.bytes -= this.chunks.shift().length;
    }
    if (this.bytes > this.limit && this.chunks.length === 1) {
      this.chunks[0] = this.chunks[0].subarray(this.bytes - this.limit);
      this.bytes = this.limit;
    }
  }
  toString() { return Buffer.concat(this.chunks).toString('utf8'); }
}

/**
 * node-pty is a viewer/recorder of a detached tmux pane, not the lifetime owner
 * of OpenCode. Killing this bridge (or restarting Express) leaves tmux running.
 */
export class TerminalBridge extends EventEmitter {
  constructor(session, config, tmuxArgs) {
    super();
    this.session = session;
    this.config = config;
    this.tmuxArgs = tmuxArgs;
    this.replay = new ReplayBuffer(config.replayBufferBytes);
    this.clients = new Set();
    this.pty = null;
    this.closed = false;
    this.logSize = 0;
    this.logQueue = Promise.resolve();
    this.logError = null;
    this.dir = path.join(config.dataDir, 'sessions', session.id);
    fs.mkdirSync(this.dir, { recursive: true, mode: 0o700 });
    this.logPath = path.join(this.dir, 'terminal.log');
    this.loadTail();
  }

  loadTail() {
    const limit = this.config.replayBufferBytes;
    for (const file of [`${this.logPath}.1`, this.logPath]) {
      let fd;
      try {
        fd = fs.openSync(file, 'r');
        const size = fs.fstatSync(fd).size;
        const length = Math.min(size, limit);
        const buffer = Buffer.alloc(length);
        fs.readSync(fd, buffer, 0, length, size - length);
        this.replay.append(buffer);
      } catch (error) {
        if (error.code !== 'ENOENT') this.logError = error.message;
      } finally { if (fd !== undefined) fs.closeSync(fd); }
    }
    try { this.logSize = fs.statSync(this.logPath).size; } catch { this.logSize = 0; }
  }

  attach() {
    if (this.pty || this.closed) return;
    this.pty = pty.spawn(this.config.tmuxBin, [...this.tmuxArgs, 'attach-session', '-t', this.session.tmuxName], {
      name: 'xterm-256color', cols: this.session.cols || 120, rows: this.session.rows || 36,
      cwd: this.session.cwd, env: terminalEnvironment(this.config), handleFlowControl: false,
    });
    this.pty.onData(data => {
      this.replay.append(data);
      this.persist(data);
      this.emit('output', data);
      for (const client of this.clients) {
        if (client.readyState !== 1) continue;
        if (client.bufferedAmount > 8 * 1024 * 1024) { client.close(1013, 'Output consumer too slow'); continue; }
        client.send(JSON.stringify({ type: 'output', data }));
      }
    });
    this.pty.onExit(({ exitCode }) => {
      this.pty = null;
      this.emit('detach', exitCode);
      for (const client of this.clients) client.close(1012, 'Terminal bridge restarting');
      this.clients.clear();
    });
  }

  persist(data) {
    // Serialize writes and rotations; a slow disk must not reorder terminal bytes.
    this.logQueue = this.logQueue.then(async () => {
      const buffer = Buffer.from(data);
      if (this.logSize + buffer.length > this.config.logFileBytes) {
        await fs.promises.rename(this.logPath, `${this.logPath}.1`).catch(error => { if (error.code !== 'ENOENT') throw error; });
        this.logSize = 0;
      }
      await fs.promises.appendFile(this.logPath, buffer, { mode: 0o600 });
      this.logSize += buffer.length;
      this.logError = null;
    }).catch(error => { this.logError = error.message; this.emit('error', error); });
  }

  addClient(ws) {
    const snapshot = this.replay.toString();
    this.clients.add(ws);
    ws.send(JSON.stringify({ type: 'replay', data: snapshot, truncated: this.replay.bytes >= this.config.replayBufferBytes }));
  }
  removeClient(ws) { this.clients.delete(ws); }
  write(data) { this.pty?.write(data); }
  resize(cols, rows) { this.session.cols = cols; this.session.rows = rows; this.pty?.resize(cols, rows); }
  async flush() { await this.logQueue; }
  close() {
    this.closed = true;
    for (const client of this.clients) client.close(1001, 'Terminal bridge closing');
    this.clients.clear();
    this.pty?.kill();
    this.pty = null;
  }
  async destroy() { this.close(); await this.flush(); await fs.promises.rm(this.dir, { recursive: true, force: true }); }
}
