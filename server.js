import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import express from 'express';
import helmet from 'helmet';
import { rateLimit } from 'express-rate-limit';
import { WebSocketServer, WebSocket } from 'ws';
import { loadConfig, ROOT } from './config.js';
import { Auth } from './auth.js';
import { SessionManager } from './sessions.js';

const asyncRoute = fn => (req, res, next) => Promise.resolve(fn(req, res, next)).catch(next);
const sendError = (res, error) => res.status(error.status || 500).json({ error: error.status ? error.message : 'Internal server error' });

export async function createApp(overrides = {}) {
  const config = overrides.config || loadConfig();
  fs.mkdirSync(config.dataDir, { recursive: true, mode: 0o700 });
  const auth = new Auth(config);
  const manager = new SessionManager(config);
  await manager.recover();
  const app = express();
  app.disable('x-powered-by');
  if (config.trustProxy) app.set('trust proxy', config.trustProxy);
  app.use(helmet({
    contentSecurityPolicy: {
      directives: {
        defaultSrc: ["'self'"], scriptSrc: ["'self'"], styleSrc: ["'self'", "'unsafe-inline'"],
        imgSrc: ["'self'", 'data:'], fontSrc: ["'self'"], connectSrc: ["'self'", 'ws:', 'wss:'],
        objectSrc: ["'none'"], baseUri: ["'none'"], formAction: ["'self'"],
        frameAncestors: config.frameAncestors,
        upgradeInsecureRequests: config.secure ? [] : null,
      },
    },
    frameguard: false,
    crossOriginEmbedderPolicy: false,
    strictTransportSecurity: config.secure ? undefined : false,
  }));
  app.use(express.json({ limit: '16kb' }));
  app.use('/api', (req, res, next) => { res.set('Cache-Control', 'no-store'); next(); });

  const loginLimiter = rateLimit({ windowMs: 15 * 60_000, limit: 10, standardHeaders: 'draft-8', legacyHeaders: false, message: { error: 'Too many sign-in attempts. Try again later.' } });
  const apiLimiter = rateLimit({ windowMs: 60_000, limit: 240, standardHeaders: 'draft-8', legacyHeaders: false, message: { error: 'Too many requests' } });
  app.use('/api', apiLimiter);

  app.get('/api/health', (req, res) => res.json({ ok: true }));
  app.post('/api/login', loginLimiter, (req, res) => {
    if (!auth.validOrigin(req)) return res.status(403).json({ error: 'Origin not allowed' });
    if (!req.is('application/json')) return res.status(415).json({ error: 'Expected application/json' });
    const { username, password } = req.body || {};
    if (!auth.verify(username, password)) return res.status(401).json({ error: 'Incorrect username or password' });
    const session = auth.create();
    res.set('Set-Cookie', auth.cookie(session.token));
    res.json({ username: config.username, csrf: session.csrf, expires: session.expires });
  });

  const protectedApi = auth.middleware();
  app.post('/api/logout', protectedApi, (req, res) => {
    auth.revoke(req.auth.token);
    res.set('Set-Cookie', auth.cookie('', 0));
    res.json({ ok: true });
  });
  app.get('/api/me', protectedApi, (req, res) => res.json({ username: config.username, csrf: req.auth.csrf, expires: req.auth.expires }));
  app.get('/api/state', protectedApi, (req, res) => res.json({
    workspace: config.workspaceDir, maxSessions: config.maxSessions,
    sessions: manager.list(), persistence: 'tmux', skills: 'unchanged',
  }));
  app.get('/api/sessions', protectedApi, (req, res) => res.json({ sessions: manager.list() }));
  app.post('/api/sessions', protectedApi, asyncRoute(async (req, res) => {
    const { name, cwd, cols, rows } = req.body || {};
    res.status(201).json({ session: await manager.create({ name, cwd, cols, rows }) });
  }));
  app.post('/api/sessions/:id/stop', protectedApi, asyncRoute(async (req, res) => res.json({ session: await manager.stop(req.params.id) })));
  app.post('/api/sessions/:id/restart', protectedApi, asyncRoute(async (req, res) => res.json({ session: await manager.restart(req.params.id) })));
  app.delete('/api/sessions/:id', protectedApi, asyncRoute(async (req, res) => { await manager.remove(req.params.id); res.json({ ok: true }); }));
  app.get('/api/sessions/:id/transcript', protectedApi, asyncRoute(async (req, res) => {
    const data = await manager.transcript(req.params.id);
    res.type('text/plain').set('Content-Disposition', `attachment; filename="opencode-${req.params.id}.ansi.log"`).send(data);
  }));

  const vendor = (relative, type) => (req, res) => res.type(type).sendFile(path.join(ROOT, 'node_modules', relative));
  app.get('/vendor/xterm.js', vendor('@xterm/xterm/lib/xterm.js', 'application/javascript'));
  app.get('/vendor/xterm.css', vendor('@xterm/xterm/css/xterm.css', 'text/css'));
  app.get('/vendor/fit.js', vendor('@xterm/addon-fit/lib/addon-fit.js', 'application/javascript'));
  app.get('/index.html', (req, res) => res.redirect('/'));
  app.use(express.static(path.join(ROOT, 'public'), { index: false, etag: true, maxAge: 0 }));
  app.get('/login', (req, res) => res.sendFile(path.join(ROOT, 'public', 'login.html')));
  app.get('/', (req, res) => {
    if (!auth.fromRequest(req)) return res.redirect('/login');
    // The browser entrypoint lives at the project root so hosts that read
    // ./index.html (for example /app/applet/index.html) find it directly.
    res.set('Cache-Control', 'no-store').sendFile(path.join(ROOT, 'index.html'));
  });
  app.use('/api', (req, res) => res.status(404).json({ error: 'Not found' }));
  app.use((error, req, res, next) => {
    if (res.headersSent) return next(error);
    if (error.type === 'entity.parse.failed') return res.status(400).json({ error: 'Invalid JSON' });
    if (error.type === 'entity.too.large') return res.status(413).json({ error: 'Request too large' });
    console.error(`[request] ${error.message}`);
    sendError(res, error);
  });

  const server = http.createServer(app);
  const wss = new WebSocketServer({ noServer: true, maxPayload: 64 * 1024, perMessageDeflate: false });
  server.on('upgrade', (req, socket, head) => {
    const reject = (status, text) => { socket.write(`HTTP/1.1 ${status} ${text}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n`); socket.destroy(); };
    let url;
    try { url = new URL(req.url, 'http://internal'); } catch { return reject(400, 'Bad Request'); }
    if (url.pathname !== '/ws') return reject(404, 'Not Found');
    if (!auth.validOrigin(req)) return reject(403, 'Forbidden');
    const session = auth.fromRequest(req);
    if (!session) return reject(401, 'Unauthorized');
    wss.handleUpgrade(req, socket, head, ws => {
      ws.auth = session;
      ws.isAlive = true;
      ws.attached = false;
      ws.windowStart = Date.now(); ws.inputBytes = 0;
      const timeout = setTimeout(() => { if (!ws.attached) ws.close(1008, 'Attach timeout'); }, 5000);
      ws.on('close', () => clearTimeout(timeout));
      wss.emit('connection', ws, req);
    });
  });

  wss.on('connection', ws => {
    let bridge = null;
    let sessionId = null;
    ws.on('pong', () => { ws.isAlive = true; });
    ws.on('error', () => {});
    ws.on('message', async (raw, isBinary) => {
      try {
        if (isBinary) throw Object.assign(new Error('Expected JSON text frames'), { status: 1003 });
        const message = JSON.parse(raw.toString());
        if (!ws.attached) {
          const liveAuth = auth.get(ws.auth.token);
          if (message.type !== 'attach' || !liveAuth || message.csrf !== liveAuth.csrf || ws.attaching) throw Object.assign(new Error('Invalid attach request'), { status: 1008 });
          ws.attaching = true;
          const cols = Number(message.cols), rows = Number(message.rows);
          if (!Number.isInteger(cols) || !Number.isInteger(rows) || cols < 20 || cols > 300 || rows < 5 || rows > 100) throw Object.assign(new Error('Invalid terminal size'), { status: 1008 });
          const result = await manager.attach(message.sessionId, ws);
          bridge = result.bridge; sessionId = message.sessionId; ws.attached = true;
          bridge.resize(cols, rows);
          ws.send(JSON.stringify({ type: 'attached', session: result.session }));
          return;
        }
        if (auth.get(ws.auth.token) === null) { ws.close(1008, 'Authentication expired'); return; }
        if (message.type === 'input') {
          if (typeof message.data !== 'string' || message.data.length > 32 * 1024) throw Object.assign(new Error('Invalid input'), { status: 1008 });
          const now = Date.now();
          if (now - ws.windowStart > 1000) { ws.windowStart = now; ws.inputBytes = 0; }
          ws.inputBytes += Buffer.byteLength(message.data);
          if (ws.inputBytes > 256 * 1024) throw Object.assign(new Error('Input rate limit exceeded'), { status: 1008 });
          bridge.write(message.data);
        } else if (message.type === 'resize') {
          const cols = Number(message.cols), rows = Number(message.rows);
          if (!Number.isInteger(cols) || !Number.isInteger(rows) || cols < 20 || cols > 300 || rows < 5 || rows > 100) throw Object.assign(new Error('Invalid terminal size'), { status: 1008 });
          bridge.resize(cols, rows);
          manager.save().catch(() => {});
        } else if (message.type === 'ping') ws.send(JSON.stringify({ type: 'pong' }));
        else throw Object.assign(new Error('Unknown message type'), { status: 1008 });
      } catch (error) {
        if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'error', error: error.status ? error.message : 'Invalid terminal message' }));
        ws.close(error.status ? 1008 : 1011, 'Terminal request failed');
      }
    });
    ws.on('close', () => { if (bridge && sessionId) bridge.removeClient(ws); });
  });

  const heartbeat = setInterval(() => {
    for (const ws of wss.clients) {
      if (!ws.isAlive) { ws.terminate(); continue; }
      ws.isAlive = false;
      ws.ping();
    }
  }, 30_000);
  heartbeat.unref();
  manager.on('warning', message => console.warn(`[warning] ${message}`));
  manager.on('change', () => {
    const payload = JSON.stringify({ type: 'sessions', sessions: manager.list() });
    for (const ws of wss.clients) if (ws.attached && ws.readyState === WebSocket.OPEN && ws.bufferedAmount < 1024 * 1024) ws.send(payload);
  });

  let closing = false;
  const close = async () => {
    if (closing) return;
    closing = true;
    clearInterval(heartbeat);
    for (const ws of wss.clients) ws.close(1001, 'Web service restarting');
    await manager.close();
    auth.close();
    await new Promise(resolve => server.close(resolve));
    await new Promise(resolve => wss.close(resolve));
  };
  return { app, server, config, auth, manager, wss, close };
}

const isMain = process.argv[1] && path.resolve(process.argv[1]) === path.join(ROOT, 'server.js');
if (isMain) {
  try {
    const instance = await createApp();
    await new Promise((resolve, reject) => {
      instance.server.once('error', reject);
      instance.server.listen(instance.config.port, instance.config.host, resolve);
    });
    const address = instance.server.address();
    console.log(`OpenCode Web listening on http://${instance.config.host}:${address.port}`);
    console.log(`Workspace: ${instance.config.workspaceDir}`);
    console.log('Persistent backend: tmux + real OpenCode CLI. Browser disconnects do not stop sessions.');
    let stopping = false;
    const shutdown = async signal => {
      if (stopping) return;
      stopping = true;
      console.log(`${signal}: detaching web viewers; OpenCode sessions remain in tmux.`);
      const timer = setTimeout(() => process.exit(1), 12_000); timer.unref();
      try { await instance.close(); process.exit(0); }
      catch (error) { console.error(error.message); process.exit(1); }
    };
    process.on('SIGTERM', () => shutdown('SIGTERM'));
    process.on('SIGINT', () => shutdown('SIGINT'));
  } catch (error) {
    console.error(`Startup failed: ${error.message}`);
    process.exitCode = 1;
  }
}
