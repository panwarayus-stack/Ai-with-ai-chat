import crypto from 'node:crypto';

const COOKIE = 'ocw_session';
const digest = value => crypto.createHash('sha256').update(value).digest('hex');
const equal = (a, b) => {
  const aa = Buffer.from(String(a));
  const bb = Buffer.from(String(b));
  return aa.length === bb.length && crypto.timingSafeEqual(aa, bb);
};

export function parseCookies(header = '') {
  const result = Object.create(null);
  for (const part of header.split(';')) {
    const index = part.indexOf('=');
    if (index < 1) continue;
    const key = part.slice(0, index).trim();
    if (!/^[A-Za-z0-9_-]+$/.test(key)) continue;
    try { result[key] = decodeURIComponent(part.slice(index + 1).trim()); } catch { /* ignore malformed */ }
  }
  return result;
}

export class Auth {
  constructor(config) {
    this.config = config;
    this.sessions = new Map();
    this.salt = crypto.randomBytes(32);
    this.expected = crypto.scryptSync(config.password, this.salt, 64);
    this.timer = setInterval(() => this.prune(), 60_000);
    this.timer.unref();
  }

  verify(username, password) {
    if (typeof username !== 'string' || typeof password !== 'string' || password.length > 256) return false;
    const actual = crypto.scryptSync(password, this.salt, 64);
    const passwordOk = crypto.timingSafeEqual(actual, this.expected);
    return equal(username, this.config.username) && passwordOk;
  }

  create() {
    this.prune();
    if (this.sessions.size >= 100) this.sessions.delete(this.sessions.keys().next().value);
    const token = crypto.randomBytes(32).toString('base64url');
    const record = { csrf: crypto.randomBytes(32).toString('base64url'), expires: Date.now() + this.config.sessionTtlMs };
    this.sessions.set(digest(token), record);
    return { token, csrf: record.csrf, expires: record.expires };
  }

  get(token) {
    if (!token || typeof token !== 'string' || token.length > 128) return null;
    const record = this.sessions.get(digest(token));
    if (!record || record.expires <= Date.now()) return null;
    return record;
  }

  fromRequest(req) {
    const token = parseCookies(req.headers.cookie)[COOKIE];
    const record = this.get(token);
    return record ? { ...record, token } : null;
  }

  revoke(token) { if (token) this.sessions.delete(digest(token)); }
  prune() { for (const [key, value] of this.sessions) if (value.expires <= Date.now()) this.sessions.delete(key); }
  close() { clearInterval(this.timer); this.sessions.clear(); }

  cookie(token, maxAge = this.config.sessionTtlMs / 1000) {
    const parts = [`${COOKIE}=${encodeURIComponent(token)}`, 'Path=/', 'HttpOnly', `SameSite=${this.config.sameSite[0].toUpperCase()}${this.config.sameSite.slice(1)}`, `Max-Age=${Math.floor(maxAge)}`];
    if (this.config.secure) parts.push('Secure');
    if (this.config.sameSite === 'none') parts.push('Partitioned');
    return parts.join('; ');
  }

  expectedOrigin(req) {
    if (this.config.publicUrl) return this.config.publicUrl;
    const host = req.headers.host;
    if (!host || /[\s/@]/.test(host)) return null;
    return `${req.secure ? 'https' : 'http'}://${host}`;
  }

  validOrigin(req) {
    const origin = req.headers.origin;
    const expected = this.expectedOrigin(req);
    return Boolean(origin && expected && origin === expected);
  }

  middleware() {
    return (req, res, next) => {
      const session = this.fromRequest(req);
      if (!session) return res.status(401).json({ error: 'Authentication required' });
      req.auth = session;
      if (!['GET', 'HEAD', 'OPTIONS'].includes(req.method)) {
        if (!this.validOrigin(req)) return res.status(403).json({ error: 'Origin not allowed' });
        if (!equal(req.headers['x-csrf-token'] || '', session.csrf)) return res.status(403).json({ error: 'Invalid CSRF token' });
      }
      next();
    };
  }
}

export { COOKIE };
