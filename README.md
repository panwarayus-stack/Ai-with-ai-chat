---
title: OpenCode Web
emoji: 🖥️
colorFrom: gray
colorTo: green
sdk: gradio
sdk_version: 5.49.1
python_version: "3.12"
app_file: app.py
suggested_hardware: zero-a10g
startup_duration_timeout: 30m
pinned: false
short_description: Browser terminal for the real OpenCode CLI
---

# OpenCode Web

A self-hosted browser terminal for the **real OpenCode CLI/TUI**. This is not a
chatbot, an API wrapper, or a simulated terminal. The server runs OpenCode inside
`tmux`, streams the terminal to xterm.js over WebSockets, and keeps sessions alive
when the browser disconnects.

This repository is configured as a **Hugging Face Space** using the **Gradio SDK**
and **ZeroGPU** hardware. The Gradio/FastAPI process in `app.py` is only a
supervisor/reverse proxy; the actual terminal is still the Node/Express app.

> **Important:** OpenCode does **not** use the ZeroGPU GPU. It talks to whichever
> model provider you configure, using your provider credentials/credits. ZeroGPU
> is used here because free ZeroGPU Spaces can host Gradio apps; the GPU function
> in `app.py` is only a compatibility probe and is not called by the terminal.

## Hugging Face Space deployment

1. Create a new Space at <https://huggingface.co/new-space>.
   - SDK: **Gradio**
   - Hardware: **ZeroGPU** (`zero-a10g`)
   - Visibility: your choice. A public Space exposes the login page to the internet;
     keep `WEB_PASSWORD` strong.
2. Push this repository to the Space, or upload these files. The Space needs at least:
   - `README.md` (the YAML block at the top is required)
   - `app.py`
   - `requirements.txt`
   - `packages.txt`
   - `package.json`, `package-lock.json`
   - `server.js`, `config.js`, `auth.js`, `sessions.js`, `terminal.js`
   - `index.html`, `public/`
3. In **Settings → Variables and secrets**, add these **Secrets**:

   | Name | Required | Purpose |
   |---|---:|---|
   | `WEB_PASSWORD` | yes | Terminal login password, at least 16 characters |
   | `WEB_USERNAME` | no | Login username, defaults to `owner` |
   | Provider credentials | yes, for model use | e.g. `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, or use OpenCode `/connect` after login |

   If you use provider environment variables, also add a **Variable** named
   `TERMINAL_ENV_ALLOWLIST` containing the comma-separated names you want passed
   into OpenCode, for example:

   ```text
   ANTHROPIC_API_KEY,OPENAI_API_KEY,GEMINI_API_KEY
   ```

   Secrets are not exposed to the browser. They are only passed to the OpenCode
   child process when explicitly allowlisted.
4. Wait for the Space to build and start. On first boot `app.py` installs the Node
   dependencies from `package-lock.json`, starts the Node backend on an internal
   port, and proxies HTTP/WebSocket traffic from the public Space port `7860`.
5. Open the Space and log in. The terminal is at `/`; a minimal Gradio status page
   is at `/gradio`.

### Free ZeroGPU limits and persistence

- Free personal accounts in good standing can host up to **2 ZeroGPU Spaces**.
- Free hardware can sleep when inactive. A sleeping/restarted Space is **not** a
  24/7 VPS.
- Space disk is ephemeral. `tmux` sessions, transcripts, and OpenCode auth stored
  on local disk can be lost when the Space restarts. Browser disconnect/reconnect
  while the Space is running still preserves the session.
- ZeroGPU GPU quota applies to `@spaces.GPU` calls. Normal OpenCode terminal use
  does not call the GPU probe, but your model provider may still charge you.
- If you need guaranteed 24/7 persistence, use a paid always-on host or attach
  durable storage and design backup/restore around it.

## Local development

Requirements: Node.js 22-24, npm, tmux 3.2+, Python 3.10+ if running the Space
wrapper locally.

```bash
npm ci
npm run setup
npm run doctor
npm start
```

Open <http://localhost:3000>. `npm run setup` creates a local `.env` with a random
password and a dedicated `~/opencode-workspace` directory.

To run the Hugging Face wrapper locally:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
WEB_PASSWORD='a-long-local-password' PUBLIC_URL='http://localhost:7860' COOKIE_SECURE=false COOKIE_SAME_SITE=strict .venv/bin/python app.py
```

Open <http://localhost:7860>.

## Controls

- `Ctrl-B D`: detach from the terminal without stopping OpenCode.
- `Ctrl-C`: interrupt the foreground OpenCode task.
- `Ctrl-B [`: tmux copy/scroll mode; `q` exits it.
- UI buttons: new session, reconnect, detach, stop, restart, upload, download
  transcript, history, multiline input.

## Architecture

```text
Browser (xterm.js)
  │ HTTPS/WSS
  ▼
Hugging Face Space port 7860
  app.py: FastAPI + Gradio status page + reverse proxy
  │ HTTP/WS on 127.0.0.1
  ▼
server.js: Express + authenticated WebSocket attach
  │ node-pty
  ▼
tmux session per terminal
  │
  ▼
real OpenCode CLI/TUI
```

The Node backend keeps a server-side PTY bridge and bounded replay buffer for each
session. Closing the browser detaches viewers but does not kill OpenCode. On a
Space restart, however, the container and its local `tmux` sessions are recreated.

## Security

- Password login, HttpOnly session cookie, CSRF checks for mutations and WebSocket
  attach, exact Origin validation, and rate limits.
- Working directories are restricted to `WORKSPACE_DIR`.
- Provider secrets are kept server-side and only passed to OpenCode through
  `TERMINAL_ENV_ALLOWLIST`.
- The Node backend binds only to loopback inside the Space; only `app.py` listens
  on the public port.
- This is not an OS sandbox. Anyone with the terminal password can run commands
  as the Space user. Do not share the password.

## Troubleshooting

### Configuration error: Missing configuration in README

The YAML front matter at the very top of `README.md` is required. It must start on
line 1 with `---` and include `sdk`, `sdk_version`, and `app_file`.

### Space fails with `WEB_PASSWORD is missing or too short`

Add a Space **Secret** named `WEB_PASSWORD` with at least 16 characters, then
restart the Space.

### `ENOENT ... index.html`

Run the app from the repository root. `index.html` is at the root and is served by
`server.js`; there is no `/app/applet` path in this project.

### Node dependencies fail to build

`packages.txt` installs `build-essential`, `python3-dev`, and `tmux`. The Space
wrapper uses `nodejs-wheel` and sets `npm_config_nodedir` to its bundled Node
headers so `node-pty` can compile. Check the Space logs for the first failing npm
package.

### Terminal works but model calls fail

OpenCode still needs valid provider credentials. Use `/connect` inside OpenCode or
set provider secrets and `TERMINAL_ENV_ALLOWLIST`. ZeroGPU does not provide model
API credits.

## Verification

```bash
npm run doctor
npm test
npm run test:integration   # requires tmux and OpenCode; no model credentials needed for attach tests
```
