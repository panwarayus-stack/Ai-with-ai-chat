# OpenCode Web

A small, authenticated browser terminal for the **real OpenCode CLI** running on your own server.

This is not a chatbot, not an API wrapper, and not a simulated terminal. The browser renders the actual OpenCode TUI with xterm.js. Node.js/Express/WebSockets/`node-pty` attach to a detached `tmux` pane, so closing the browser does not stop OpenCode.

```text
Android / PC browser
  └─ xterm.js + multiline composer
       └─ authenticated WebSocket
            └─ Express + node-pty recorder
                 └─ tmux pane
                      └─ real OpenCode CLI
```

## What is included

- OpenCode dark terminal-style interface, responsive for desktop and mobile.
- Multiple named sessions in an authorized workspace.
- Real-time terminal input/output, including OpenCode slash commands and permission prompts.
- Reconnect with recent ANSI output replay.
- Rotated terminal transcripts on disk.
- Multiline prompt composer, per-session prompt history on the device, and mobile terminal keys.
- Stop, restart, close, and transcript download controls.
- Password authentication, HttpOnly cookie, CSRF checks, Origin checks, rate limits, and security headers.
- systemd installer, with PM2 fallback/configuration.
- OpenCode CLI pinned as a local npm dependency (`opencode-ai` 1.19.5 in this build).
- No skills installed or changed, as requested.

## Important persistence semantics

| Event | Result |
| --- | --- |
| Close browser / lose network | OpenCode keeps running in tmux. Reconnect restores recent output. |
| Restart the Node web service | OpenCode keeps running. The web service reattaches to existing tmux panes. |
| Stop a session from the UI | OpenCode is asked to exit, then the pane is terminated after a grace period. Transcript is kept. |
| Restart a stopped session | A new OpenCode TUI opens in the same directory. Use OpenCode `/sessions` to resume a native conversation. |
| Reboot the VPS | Running processes cannot survive a reboot. systemd/PM2 restarts the web service; session metadata and transcripts remain. Stopped sessions can be restarted, and native OpenCode conversation history remains available through OpenCode. |

The web service is kept running 24/7. Individual AI tasks are not automatically resubmitted after a reboot.

## Requirements

- Linux VPS with Node.js **22–24** and npm.
- A non-root user with `sudo` for the installer.
- `tmux`, `python3`, `make`, and `g++` (the Debian/Ubuntu installer installs missing packages).
- Outbound access to your chosen AI provider. Provider credentials are configured in OpenCode, not in the website.

## Install on a Debian/Ubuntu VPS

Copy this repository's contents to the server, for example as `/opt/opencode-web`, owned by your service user:

```bash
sudo mkdir -p /opt/opencode-web
sudo chown "$USER:$USER" /opt/opencode-web
# Copy the repository files into /opt/opencode-web, then:
cd /opt/opencode-web
bash deploy/install.sh
```

The browser entrypoint is `index.html` at the project root. If a host reads `/app/applet/index.html`, deploy the repository contents directly into `/app/applet` and start the app with `npm start` (or the included service installer), not an unrelated `server.ts`.

The installer verifies each important step:

1. Prints OS, Node.js, and npm versions.
2. Installs missing tmux/native-build packages.
3. Runs `npm ci` to install the pinned OpenCode CLI, xterm.js, Express, ws, and node-pty.
4. Generates a private `.env` with a random website password.
5. Creates `~/opencode-workspace` and a conservative OpenCode outside-directory approval policy.
6. Runs `npm run doctor`.
7. Installs and enables systemd when available, otherwise starts PM2.
8. Checks the HTTP health endpoint.

The setup command prints the generated website username/password once. They are also stored in the server-side `.env` file. Do not commit or publish that file.

### Manual setup

```bash
npm ci
npm run setup
npm run doctor
npm start
```

`npm start` is foreground-only. Use systemd or PM2 for persistent operation.

## First sign-in and provider setup

1. Open the website over HTTPS and sign in with the generated website credentials.
2. Create a session. The real OpenCode TUI opens in `~/opencode-workspace`.
3. Connect a provider using OpenCode itself:
   - In the TUI, use `/connect`; or
   - Over SSH as the same service user: `./node_modules/.bin/opencode auth login`.
4. Choose a model in OpenCode and start prompting.

Provider credentials are stored by OpenCode in the service user's home directory. The web frontend never receives provider keys. The terminal environment is allowlisted; arbitrary server environment variables are not passed through. If you intentionally use environment-based provider credentials, add only the variable **names** to `TERMINAL_ENV_ALLOWLIST` in `.env`, then restart the web service.

No OpenCode skills were installed. Existing skills/configuration are preserved. To add skills later, use OpenCode's normal `.opencode/skills/<name>/SKILL.md` layout in the workspace or global config directory.

## Using the interface

- **Terminal:** click the terminal and type exactly as in a local OpenCode terminal. Permission prompts, slash commands, and TUI shortcuts are handled by OpenCode.
- **Composer:** Enter sends; Shift+Enter inserts a newline. Multiline text is bracketed-pasted into the same TUI. Up/Down recalls device-local prompt history.
- **Sessions:** create, switch, stop, restart, or close from the sidebar/toolbar.
- **Transcript:** the download button exports the retained ANSI terminal log. Treat transcripts as sensitive; they can contain prompts, file paths, and tool output.
- **Mobile:** use the key row for Esc, Tab, Ctrl-C, and arrows. The composer is useful for longer prompts.

Prompt history/drafts are stored in browser local/session storage per web session. Raw keystrokes are not separately logged by the web server; terminal output is logged as emitted by the TUI. Clear history on shared devices.

## HTTPS and public access

Do **not** expose the raw HTTP terminal port to the public internet. Password authentication does not encrypt traffic.

Recommended: Caddy on the VPS, with the Node app bound to loopback.

`/etc/caddy/Caddyfile`:

```caddyfile
code.example.com {
    reverse_proxy 127.0.0.1:3000
}
```

`.env` for that deployment:

```dotenv
HOST=127.0.0.1
PORT=3000
PUBLIC_URL=https://code.example.com
COOKIE_SECURE=true
COOKIE_SAME_SITE=strict
TRUST_PROXY=1
FRAME_ANCESTORS="'none'"
```

Restart the service after editing `.env`:

```bash
sudo systemctl restart opencode-web
# or: npx pm2 restart opencode-web
```

Allow ports 80/443 in the firewall, not port 3000. Caddy obtains HTTPS certificates automatically. Nginx/Certbot or another trusted TLS reverse proxy also works; forward WebSocket upgrade headers and set `TRUST_PROXY` to the number of trusted proxy hops.

Without a domain, use an SSH tunnel from a PC:

```bash
ssh -L 3000:127.0.0.1:3000 user@your-vps
# then open http://127.0.0.1:3000
```

For Android, use a private VPN such as WireGuard/Tailscale or a proper HTTPS domain. Do not use plain HTTP over the public internet.

## Service management

### systemd (preferred)

```bash
sudo systemctl status opencode-web
sudo systemctl restart opencode-web
sudo systemctl stop opencode-web
journalctl -u opencode-web -f
```

The unit uses `KillMode=process` so restarting the Node bridge does not kill detached tmux/OpenCode processes. It disables `PrivateTmp` so the tmux socket remains reachable across bridge restarts.

### PM2

```bash
npx pm2 start ecosystem.config.cjs
npx pm2 status
npx pm2 restart opencode-web
npx pm2 stop opencode-web
npx pm2 logs opencode-web
```

For reboot persistence, run `npx pm2 startup`, execute the command it prints, then run `npx pm2 save`. The ecosystem config disables tree-kill so the web bridge can restart without killing tmux.

## Configuration

See `.env.example`. Important values:

| Variable | Purpose |
| --- | --- |
| `WEB_USERNAME`, `WEB_PASSWORD` | Website sign-in. Password must be 16–256 characters. |
| `WORKSPACE_DIR` | Authorized workspace root. New sessions can start only in existing real subdirectories. |
| `PUBLIC_URL` | Exact public HTTPS origin; used for Origin validation. |
| `COOKIE_SECURE`, `COOKIE_SAME_SITE` | Use `true`/`strict` behind HTTPS. `none` requires Secure. |
| `TRUST_PROXY` | Trusted reverse-proxy hop count; `0` when directly exposed. |
| `MAX_SESSIONS` | Maximum web sessions (default 8). |
| `REPLAY_BUFFER_BYTES` | Recent output replayed on reconnect (default 2 MiB). |
| `LOG_FILE_BYTES` | Size of each of two rotated transcript files per session (default 16 MiB). |
| `OPENCODE_BIN`, `TMUX_BIN` | Optional explicit executable paths. |
| `TERMINAL_ENV_ALLOWLIST` | Optional provider/environment variable names passed to OpenCode. |

Website login sessions are in memory and expire after 12 hours by default. Restarting the web service signs browsers out, but does not stop tmux/OpenCode sessions.

## Security model and limits

- Single-owner password authentication; this is not a multi-tenant platform.
- HttpOnly, SameSite cookie; CSRF token for mutations and WebSocket attach; strict Origin validation.
- Login/API rate limits and WebSocket input size/rate limits.
- Session working directories are resolved with `realpath`; traversal and symlink escapes outside `WORKSPACE_DIR` are rejected.
- The generated workspace OpenCode policy asks before outside-directory access. This is an approval guard, **not an OS sandbox**. OpenCode can still act with the permissions of the service user.
- Run as a dedicated non-root Unix user. Keep secrets and unrelated projects out of that user's reach. Do not grant that user passwordless sudo.
- Terminal transcripts and session metadata are stored under `DATA_DIR` with private permissions. Protect backups of that directory and OpenCode's auth storage.
- Multiple browsers can view the same terminal; the most recent resize wins. Anyone signed in can control all sessions.

## Project layout

```text
.
├── index.html             browser terminal entrypoint (project root)
├── server.js              Express, auth routes, WebSocket upgrade
├── sessions.js            tmux session lifecycle and metadata
├── terminal.js            node-pty bridge, replay buffer, transcript rotation
├── auth.js                password/cookie/CSRF/Origin checks
├── config.js              environment and workspace validation
├── ecosystem.config.cjs   PM2 configuration
├── .env.example
├── public/                app.js, login page, mobile-friendly CSS
├── scripts/               setup, doctor, systemd installer
├── deploy/                VPS installer and systemd unit template
└── tests/                 unit + real OpenCode/tmux integration tests
```

## Verification

```bash
npm run doctor
npm test
```

The integration test starts the real OpenCode CLI in tmux and checks authentication, streaming output, browser reconnect, web-service restart with the same OpenCode PID, stop/restart, and cleanup. It does not send an AI prompt or require provider credentials.

On the VPS also verify:

```bash
sudo systemctl is-enabled opencode-web
sudo systemctl is-active opencode-web
curl -fsS http://127.0.0.1:3000/api/health
```

Then test from a browser: create a session, disconnect/reconnect, restart the web service, and confirm the session is still running. Finally reboot the VPS and confirm the web service returns and transcripts/session history remain.

## Troubleshooting

- **`ENOENT ... index.html`:** start this project from its root (`npm start`). The entrypoint is `./index.html`; do not run an unrelated generated `server.ts` that expects a different layout.
- **`node-pty` build fails:** install `python3 make g++` and Node headers. On restricted networks, point `npm_config_nodedir` at a local Node installation containing `include/node`.
- **`tmux` missing:** `sudo apt-get install tmux`.
- **Login says Origin not allowed:** set `PUBLIC_URL` to the exact browser origin and configure `TRUST_PROXY` for your reverse proxy.
- **Blank terminal after create:** wait for OpenCode startup, then press Enter or resize. Check `journalctl -u opencode-web -f` and OpenCode logs under the service user's `~/.local/share/opencode/log`.
- **No model/provider:** run OpenCode `/connect` or `opencode auth login` as the service user.
- **Session stopped after reboot:** expected. Restart it from the UI and use OpenCode `/sessions` to resume the native conversation.
- **Disk usage:** transcripts are bounded to two rotated files per session, but native OpenCode history/snapshots are managed by OpenCode. Monitor the service user's data directory.
