#!/usr/bin/env bash
# Run from the extracted project on a Debian/Ubuntu VPS as a non-root sudo user.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"

echo "== Environment =="
uname -a
node --version
npm --version
node -e 'const m=+process.versions.node.split(".")[0]; if(m<22||m>=25) throw Error("Node.js 22–24 required")'
if [ "$(id -u)" -eq 0 ]; then echo "Run as a non-root user with sudo, not root." >&2; exit 1; fi

missing=0
for command in tmux python3 make g++; do command -v "$command" >/dev/null 2>&1 || missing=1; done
if [ "$missing" -eq 1 ]; then
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "Install tmux, python3, make, and g++ with your distribution package manager, then rerun." >&2
    exit 1
  fi
  sudo apt-get update
  sudo apt-get install -y tmux python3 make g++ ca-certificates
fi
command -v tmux >/dev/null
tmux -V

echo "== Dependencies and real OpenCode CLI =="
npm ci --no-audit --no-fund
npm run setup
npm run doctor

echo "== Persistent service =="
if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
  node scripts/install-service.js
  sudo systemctl is-enabled opencode-web
  sudo systemctl is-active opencode-web
else
  echo "systemd is unavailable; using PM2."
  npx pm2 start ecosystem.config.cjs
  npx pm2 save
  echo "For reboot persistence, run the command printed by: npx pm2 startup"
  echo "Then run: npx pm2 save"
fi

PORT="$(node -e 'require("dotenv").config({quiet:true}); console.log(process.env.PORT||3000)')"
for attempt in 1 2 3 4 5; do
  if curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null; then
    echo "OK: health check passed on port ${PORT}"
    break
  fi
  [ "$attempt" -eq 5 ] && { echo "Health check failed" >&2; exit 1; }
  sleep 2
done
printf '\nInstalled. Read README.md before exposing the port publicly.\n'
printf 'Local URL: http://127.0.0.1:%s\n' "$PORT"
printf 'Server LAN addresses: '; hostname -I 2>/dev/null || true
printf 'Credentials: see the setup output and private .env file.\n'
