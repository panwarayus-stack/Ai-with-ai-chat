# Instagram Bot for Voroa

A ready-to-deploy Instagram **direct-message command bot** written in Python.
Users send slash commands (`/Weather Agra`, `/Anime Frieren`, `/AI what is a
black hole?`) in DMs and the bot replies in the same thread with real data
from public APIs — plus a small set of local entertainment commands.

The project is a **headless background process** (no web dashboard, no GUI,
no Node.js). It is designed to run as a **Voroa Background Worker** with the
start command `python app.py`.

> **Status of this build (October 2026).** All 16 active commands are
> implemented and covered by 115 automated tests (offline, mocked HTTP).
> Provider endpoints, auth schemes, response shapes and rate limits were
> verified against the providers' current documentation (see
> [Provider verification](#provider-verification)). Live end-to-end calls
> were **not** possible from the build sandbox (outbound network is
> restricted) — the first real run happens on your Voroa worker with your
> credentials.

---

## Contents

1. [Commands](#commands)
2. [Project structure](#project-structure)
3. [Provider verification](#provider-verification)
4. [Local setup](#local-setup)
5. [Configuration](#configuration)
6. [Voroa deployment](#voroa-deployment)
7. [Session persistence (important)](#session-persistence-important)
8. [Instagram integration & limitations](#instagram-integration--limitations)
9. [Security](#security)
10. [Testing](#testing)
11. [Troubleshooting](#troubleshooting)

---

## Commands

### Active (16)

| Command | What it does | Data source |
| --- | --- | --- |
| `/Info` | Info about the account the bot is logged in as (only) | Instagram (instagrapi) |
| `/Gender <name>` | Gender + likely nationality, with probabilities and sample counts | Genderize + Nationalize |
| `/AI <question>` | General assistant (its own conversation memory) | OpenRouter |
| `/Gf <message>` | "Aria" chat companion (separate memory & persona) | OpenRouter |
| `/Sherlock <username>` | Public profile pages found for a username | Sherlock project |
| `/Help <question>` | Command list, usage, troubleshooting (fully local) | local |
| `/Weather <city>` | Current weather, feels-like, wind, 3-day forecast | Open-Meteo |
| `/Anime <anime name>` | Synopsis, genres, episodes, characters, seasons vs related works, poster | AniList (Jikan fallback) |
| `/Pic [male\|female\|random]` | SFW anime artwork + source URL (never explicit) | waifu.im + nekos.best |
| `/Roast <name>` | Playful, good-natured roast | local templates |
| `/Ship <name1> <name2>` | Deterministic entertainment-only compatibility score | local (hash) |
| `/Truth` | Random truth question | local bank |
| `/Dare` | Random safe dare (always optional) | local bank |
| `/Quote` | Motivational / funny / anime quote with honest attribution | local bank |
| `/Celeb <full name>` | Biography + public photo of a notable person | Wikipedia + Commons |
| `/Wiki <search term>` | Article summary, key content, lead image, source link | Wikipedia + Commons |

### Paused (2 — placeholders only)

| Command | Status |
| --- | --- |
| `/Num <number>` | **Paused.** Returns a clear notice, makes **no** network calls. TODO provider slot reserved in `src/commands/paused.py` (`PHONE_LOOKUP_API_KEY`). |
| `/Id <username>` | **Paused.** Same rules (`INSTAGRAM_ID_API_KEY`). |

Paused commands are **not** in the command registry, **not** in `/Help`, and
no endpoints, keys or results are invented for them. Flipping
`ENABLE_NUM_COMMAND` / `ENABLE_ID_COMMAND` does not fabricate a provider.

Commands that are deliberately **not** implemented: `/Joke`, `/8ball`,
`/Age`, `/Sticker`, `/Take`, `/Meme`, `/Emix`.

---

## Project structure

```
app.py                  Entry point: login, inbox polling, dispatch, shutdown
config.py               Environment configuration + validation (no secrets logged)
requirements.txt        Runtime dependencies (Voroa installs this automatically)
requirements-dev.txt    Test dependencies (pytest)
.env.example            Every configuration variable, documented
.gitignore              Keeps .env, sessions, credentials and logs out of git
src/
  commands/             Command handlers + registry (one module per topic)
    paused.py           /Num and /Id disabled placeholders (TODO providers)
  services/             Reusable API clients
    openrouter.py       One shared client + per-mode conversation memory
    demographics.py     Genderize + Nationalize (combined /Gender logic)
    weather.py          Open-Meteo geocoding + forecast
    anime.py            AniList GraphQL (primary) + Jikan (fallback)
    images.py           waifu.im + nekos.best with content policy
    wikipedia.py        Wikipedia search/summary + Commons attribution
    instagram.py        instagrapi wrapper (DMs, account info, sessions)
    sherlock.py         Sherlock engine wrapper (bounded site sweep)
    session_store.py    File / Redis persistence (Voroa has no disks)
  lib/
    ai_queue.py         Shared bounded FIFO queue — one AI request at a time
    http.py             Timeouts, retries, Retry-After, typed errors
    validation.py       Command parsing + input validation
    formatting.py       DM message chunking + text helpers
    utils.py            Small shared helpers
data/                   Quotes, truths, dares, roasts (JSON banks)
tests/                  115 offline tests (commands, services, queue, config)
```

---

## Provider verification

| Provider | Endpoints / auth | Verified | Notes |
| --- | --- | --- | --- |
| OpenRouter | `POST /api/v1/chat/completions`, Bearer key, optional `HTTP-Referer` / `X-OpenRouter-Title` | docs + integration tests | Free `:free` models exist and rotate; free tier ≈20 req/min. Configured model is just an env var. Never fakes answers. |
| Genderize / Nationalize | `GET api.genderize.io/?name=`, `GET api.nationalize.io/?name=`, optional `apikey` | docs + integration tests | Anonymous access allowed on a small free quota (a key raises it). 429 = quota. Both keys optional. |
| Open-Meteo | `geocoding-api.open-meteo.com/v1/search`, `api.open-meteo.com/v1/forecast` | docs + integration tests | Keyless. `timezone=auto` for local time. WMO weather codes mapped to text. |
| AniList | `POST graphql.anilist.co`, keyless | docs + integration tests | ~90 req/min. `relationType` distinguishes real seasons (PREQUEL/SEQUEL) from spin-offs etc. No episode lists invented. |
| Jikan | `GET api.jikan.moe/v4/anime` | docs + integration tests | Keyless, ~3 req/s / 60 req/min — used **only** as fallback. |
| waifu.im | `GET api.waifu.im/images?IncludedTags=…&IsNsfw=False` | docs + integration tests | **API changed**: the old `/search` endpoint is gone; this uses the current `/images` API. SFW-only filter is mandatory; mild tags (e.g. `oppai`) mixed in at ~30%; explicit tags excluded. Male category does not exist → see nekos.best. |
| nekos.best | `GET nekos.best/api/v2/husbando` (keyless, custom User-Agent required) | docs + integration tests | Documented alternative for **male** artwork and waifu.im fallback. Returns verifiable `anime_name` / artist / source. |
| Wikipedia / Commons | action API + REST summary + Commons `imageinfo` | docs + integration tests | Custom `User-Agent` required by policy. Disambiguation handled; person detection is cue-based and honest ("not verified as a biography" when unsure). |
| Sherlock | `sherlock-project` PyPI package v0.16 (installed & API verified locally) | package API verified | CLI + library both work on Python ≥3.9. Default sweep is a curated site subset (`SHERLOCK_SITES`) to stay friendly to small hosts. |
| Instagram | `instagrapi` v3.x (PyPI; Python ≥3.10) | package API verified | Chosen over `dilame/instagram-private-api`, which is **TypeScript/Node.js** and would require a second runtime. Unofficial API — see limitations below. |
| Voroa | docs: background worker, env vars, persistent disks | docs verified | **Persistent disks are not available on Voroa today** (their docs, Aug 2026) — see session persistence below. |

---

## Local setup

Requires **Python 3.10+** (verified on 3.11).

```bash
git clone <this repo> && cd <repo>
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env      # then fill in IG_USERNAME / IG_PASSWORD at minimum
python app.py --check     # validates config + imports + command registry
pytest                    # 115 tests, all offline
python app.py             # start the bot
```

Use a **dedicated Instagram account** for the bot. On first run the bot logs
in, saves the session, and starts polling its DM inbox every
`IG_POLL_INTERVAL` seconds.

---

## Configuration

Every variable lives in [`.env.example`](.env.example) with inline
descriptions. Highlights:

* **Required to run:** `IG_USERNAME`, `IG_PASSWORD`.
* **Optional but recommended:** `OPENROUTER_API_KEY` (for `/AI`, `/Gf`),
  `REDIS_URL` (session persistence on Voroa).
* **Optional keys that raise provider quotas:** `GENDERIZE_API_KEY`,
  `NATIONALIZE_API_KEY`.
* **Everything else works keyless** (Open-Meteo, AniList, Jikan, waifu.im,
  nekos.best, Wikipedia, Sherlock).

Missing optional keys never crash the bot: the affected command answers with
a clear configuration message and nothing is faked.

---

## Voroa deployment

The bot is a long-running process with **no web port** — deploy it as a
Voroa **Background Worker** ([Voroa docs: background workers](https://getvoroa.com/docs/deploy-background-worker/)).

### 1. Push the project to GitHub

This repository is the deployable project. Push it (branch of your choice).

### 2. Create the service

1. Voroa dashboard → **New** → **Background Worker**.
2. Pick the repository and branch.
3. Root directory: `/` (project root).
4. **Start command:** `python app.py`
5. **Build command:** leave **empty**. Voroa detects `requirements.txt` and
   installs dependencies automatically — adding `pip install` in the build
   step only duplicates the install (their docs explicitly warn against it).
6. Instance: the free instance is fine to start (the bot polls and waits).

### 3. Set environment variables

In the service's **Environment** tab, add at least:

```
IG_USERNAME=your_bot_username
IG_PASSWORD=your_bot_password
OPENROUTER_API_KEY=sk-or-...        (optional, enables /AI and /Gf)
OPENROUTER_MODEL=meta-llama/llama-3.3-70b-instruct:free
REDIS_URL=                          (paste your managed Key Value URL — see below)
WIKIPEDIA_USER_AGENT=InstagramBot/1.0 (https://your.domain; you@example.com)
```

Voroa encrypts service environment variables and does not echo them back.
Copy the rest from `.env.example` as needed.

**No time to set them now? Deploy first, configure later.** If
`IG_USERNAME`/`IG_PASSWORD` are missing the worker no longer crash-loops: it
starts, logs what is missing, and **waits** — staying up and SSH-reachable —
until you provide them live (see
[Live configuration via SSH](#live-configuration-via-ssh)). The same is true
for one-time 2FA codes.

### 4. Session persistence — read before first deploy

**Voroa has no persistent disks today** (their docs: "Persistent disks are
not available on Voroa today… treat your service's filesystem as temporary").
A redeploy wipes everything the process wrote to disk, including the
Instagram login session.

How this bot handles it:

* **Without `REDIS_URL`:** the session is cached in local files
  (`auth/…` and `auth/state/…`). It survives process restarts *within one
  deploy*, but **every redeploy forces a fresh Instagram login** (the bot
  logs in again automatically using `IG_USERNAME`/`IG_PASSWORD`).
* **With `REDIS_URL` (recommended):** set it to a Voroa managed **Key Value
  (Redis)** URL (the free plan includes one database). The session and the
  processed-message list are stored there and survive deploys and restarts.
  Files are still written as a write-through cache.

### Live configuration via SSH (credentials & OTP)

You do **not** have to finish configuration before deploying. The bot is
built for "configure later":

1. Deploy even with the environment still empty. The worker starts, logs what
   is missing, and **waits** (no crash-loop, no Instagram calls while
   waiting). Voroa shows it as running and SSH stays available.
2. Open a terminal: Voroa dashboard → your service → **SSH**.
3. Provide values live — the waiting bot picks them up within seconds, no
   restart needed:

```bash
# Instagram login (password is prompted with hidden input)
python app.py --set-credentials your_bot_username

# One-time 2FA / verification code, when Instagram asks for one
python app.py --set-code 123456

# Remove a stored code (codes are one-time and cleared after login anyway)
python app.py --clear-code
```

How it works:

* Values are written to the session store — files under `auth/` (git-ignored,
  chmod 600) or Redis when `REDIS_URL` is set. With Redis they even survive
  redeploys.
* Live values take priority over dashboard env vars, so you can override a
  stale `IG_VERIFICATION_CODE` or a wrong password without touching the
  dashboard.
* For OTP you can also just write the file directly:
  `echo 123456 > auth/verification_code.txt` — the bot polls
  `IG_VERIFICATION_FILE` during login for up to `IG_VERIFICATION_WAIT`
  seconds (default 300).
* The dashboard **Environment** tab still works exactly as before — use
  whichever is faster. Env vars win only when no live value is present.

Security notes: credentials and codes are never printed back or logged, and
`auth/` is excluded from git. Avoid `--set-credentials user pass` on shared
machines (the two-argument form can land in shell history); the prompted
form is always safe.

### 5. Uptime expectations

* A background worker runs continuously and is **not** idle-stopped (unlike
  free web services), but on the **free plan** the workspace has a shared
  monthly pool of running hours (750/month across free services). If those
  hours run out, the worker stops until the next cycle. **Do not assume 24/7
  uptime on the free plan**; a paid instance runs continuously.
* Voroa restarts the worker if it exits. This bot catches command errors and
  backs off on network failures instead of exiting, so crash-loops should not
  happen. If the start command itself is wrong, the deploy shows "Failed to
  start" in the dashboard.

### 6. Verify the deployment

* Build log: dependencies installed once (no "installed twice" warning).
* Service log: `Instagram login OK for @…` then `Bot is running — polling
  every 20s (commands: 16 active)`.
* DM the bot `/Help` from another account.

---

## Anti-suspension: staying quiet on Instagram

Automated accounts get flagged (or hit with "verify it's you" challenges) when
they behave like a 24/7 robot. This bot is built to **come online only when
somebody messages it**, and otherwise keep its request footprint as close to a
quiet human as the Instagram private API allows.

### What "only becomes online when someone replies" means in practice

Instagram's private API has **no push channel** — the only way to notice a new
DM is to ask. So the bot cannot literally sleep until pinged; instead it:

- **Stays silent when nothing is happening.** No messages → no replies, no
  likes, no follows, no posting, no profile actions. The bot never starts a
  conversation.
- **Slows down when the inbox is idle.** After `IG_IDLE_AFTER_MINUTES`
  (default 10) without any new message, polling stretches to
  `IG_IDLE_POLL_INTERVAL` (default 180 s). While people are active it polls at
  `IG_POLL_INTERVAL` (default 60 s).
- **Every poll interval is jittered** by ±15% so the request pattern never
  looks metronomic.
- **Waits before replying** a random `IG_REPLY_DELAY_MIN`–`IG_REPLY_DELAY_MAX`
  seconds (default 3–10) — humans don't answer in 200 ms.

### Request pacing and budgets

- **`IG_MIN_REQUEST_GAP`** (default 5 s) is a hard floor between *any* two
  Instagram API calls. Long conversations pace themselves out instead of
  bursting.
- **`IG_MAX_REPLIES_PER_HOUR`** (default 20) caps how much the bot will say
  per hour. Past the budget it stays silent (logged as a warning) and the
  window resets on its own.
- **`IG_MARK_SEEN=false`** (default) skips "mark thread read" calls entirely —
  each one is an extra request for little benefit.
- **`IG_REPLY_IN_GROUPS=false`** (default) ignores group threads and replies
  only in 1:1 DMs, cutting traffic sharply.
- **Re-login protection:** `sessionid` cookies are persisted and reused
  (see above); invalid credentials abort instead of retrying. Logins are the
  single most suspicious request an automation makes.
- **Failed logins never crash-loop.** If Instagram refuses a login (rate
  limit, challenge, wrong password), the process *stays up* and retries
  slowly — `IG_LOGIN_RETRY_MIN` doubling to `IG_LOGIN_RETRY_MAX` (60 s →
  30 min), and only every `IG_RATE_LIMIT_COOLDOWN` (20 min) for rate limits
  and challenges. A crash-loop restarts the bot every ~30 s and hammers the
  login endpoint; that is precisely what keeps "Rate limit exceeded" and
  "verify it's you" active.

All of these are environment variables — tune them in the Voroa dashboard.
If Instagram still challenges the account, raise `IG_MIN_REQUEST_GAP` and
`IG_POLL_INTERVAL`, and lower `IG_MAX_REPLIES_PER_HOUR`. Non-command chatter
is answered with nothing at all, so a chatty contact costs zero extra
Instagram requests.

## Instagram integration & limitations

* `instagrapi` is an **unofficial** private-API wrapper. Instagram can
  rate-limit, challenge (checkpoint) or restrict automated accounts at any
  time. Use a secondary account and moderate volume.
* The bot **does not bypass CAPTCHAs or authentication challenges**. If
  Instagram raises one: approve the login in the Instagram app on a trusted
  device, then restart the bot. For 2FA accounts deliver the one-time code
  live (`python app.py --set-code <code>` via SSH) or set
  `IG_VERIFICATION_CODE` — the bot waits for it during login.
* Expired sessions are detected (`ClientLoginRequired`) and refreshed
  automatically from stored settings or a fresh login.
* The reference library in the original brief
  ([`dilame/instagram-private-api`](https://github.com/dilame/instagram-private-api))
  is TypeScript/Node.js. Since the brief mandates Python-only hosting,
  `instagrapi` (PyPI, actively versioned, Python ≥3.10) is the compatible
  Python solution — its DM, session and account APIs were verified against
  the installed package.
* `/Info` shows **only** the authenticated account. Session settings,
  cookies, passwords and tokens are never logged or sent to users.

---

## Security

* Secrets come only from environment variables. `.env`, `auth/`,
  `*.session`, `session*.json`, cookies and logs are git-ignored
  (enforced by `tests/test_app.py`).
* No credentials are embedded in source. The config `summary()` log line is
  redacted.
* All HTTP calls have timeouts, bounded retries and rate-limit handling
  (`src/lib/http.py`); the AI queue is bounded; conversation memory is
  bounded; processed message IDs are bounded and persisted.
* User input is validated and URL parameters are encoded by the HTTP layer.
* A failed command replies with a friendly error and can never crash the
  polling loop.
* Note on repository history: earlier revisions of this repository contained
  a hard-coded Gemini API key in a now-removed web front-end file. **Rotate
  that key** if it was ever real — it remains in git history.

---

## Testing

```bash
pip install -r requirements-dev.txt
pytest            # 115 tests, no network access required
```

Coverage includes (per the delivery checklist):

1. All 16 active commands registered and callable.
2. `/Num` and `/Id` present only as disabled placeholders (not in registry or
   help, no network calls).
3. `/Gender` combines both providers, applies the 5-percentage-point rule and
   survives partial failures.
4. `/AI` and `/Gf` share one FIFO queue with exactly one active request, and
   keep separate prompts/memories; errors are never faked.
5. Wikipedia and anime replies are built strictly from provider payloads
   (titles, URLs, extracts asserted against canned real-shaped responses).
6. Every module imports cleanly.
7. `python app.py --check` passes (the same start-path wiring Voroa uses).
8. Secrets and session files are excluded from version control.

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `IG_USERNAME is required` in the log | The bot is **waiting**, not dead. Two fixes: (a) Voroa dashboard → Environment → set `IG_USERNAME` / `IG_PASSWORD` → Restart, or (b) live via SSH: `python app.py --set-credentials your_bot_username` (password prompted). |
| `/AI` answers "not configured" | Add `OPENROUTER_API_KEY`. The bot refuses to fake answers. |
| "OpenRouter rate limit reached" | Free tier ≈20 req/min. Wait a moment; the shared queue serialises requests. |
| "configured AI model … is unavailable" | Free models rotate off. Pick a current `:free` slug at [openrouter.ai/models](https://openrouter.ai/models) and update `OPENROUTER_MODEL`. `openrouter/free` auto-routes across free models. |
| Instagram challenge/checkpoint loop | Approve the login in the Instagram app on a trusted device. Set `IG_VERIFICATION_CODE` for 2FA. The bot will not bypass challenges. |
| Instagram asks for a one-time code | Live (no restart): SSH → `python app.py --set-code 123456`. The bot waits up to `IG_VERIFICATION_WAIT` seconds (default 300) and uses the code automatically. Or set `IG_VERIFICATION_CODE` in the dashboard and restart. |
| Instagram keeps asking to "verify it's you" | Too many requests for a human account. Raise `IG_POLL_INTERVAL`/`IG_MIN_REQUEST_GAP`, lower `IG_MAX_REPLIES_PER_HOUR`, keep `IG_MARK_SEEN=false`, `IG_REPLY_IN_GROUPS=false` — see [Anti-suspension](#anti-suspension-staying-quiet-on-instagram). |
| `Rate limit exceeded` (code 1675004) or HTTP 429 at login | Instagram is throttling the login endpoint (usually after many repeated login attempts). The bot now **stays up** and retries every `IG_RATE_LIMIT_COOLDOWN` (default 20 min) instead of crash-looping. Do **not** keep redeploying — each restart is another login attempt. Wait 30–60 minutes, approve any pending "was this you?" prompt in the Instagram app on your phone, then let the bot retry on its own. |
| Deploy aborted: "your service started and then exited repeatedly" | Fixed: a failed login no longer exits the process. Redeploy after merging this change; if the deploy still aborts, check the Logs tab for the last error before the exit. |
| Bot replies slower than expected | Working as designed (human-like `IG_REPLY_DELAY_MIN`–`IG_REPLY_DELAY_MAX`, request pacing). Only tune these down if the account is challenge-free. |
| "Instagram rejected the verification code" | Codes expire quickly and are one-time. Get a fresh code and run `python app.py --set-code <code>` again. |
| Session lost after every redeploy | Set `REDIS_URL` to a Voroa managed Key Value URL (see [session persistence](#session-persistence-important)). |
| `/Gender` says "quota reached" | Genderize/Nationalize free quotas are small; add `GENDERIZE_API_KEY` / `NATIONALIZE_API_KEY` (one genderize.io key covers both) or retry later. |
| `/Weather` can't find the city | Use `City, Country` (e.g. `Agra, India`). |
| `/Anime` returns "Other matches" | Re-send with the exact title shown. The bot never guesses a watch order. |
| `/Pic` returns an error | Both image providers were unreachable or refused the content filter — the message says which. Try again later. |
| `/Sherlock` is slow or times out | Each checked site gets a real network timeout. Narrow `SHERLOCK_SITES` (e.g. `GitHub,Twitter,Instagram`) or raise `SHERLOCK_TIMEOUT`. |
| Worker restarts in a loop | Check the Voroa deploy log: the start command must be exactly `python app.py` and `requirements.txt` must install. |

---

## License / usage notes

Follow each provider's terms of service, the Wikimedia user-agent policy,
Instagram's automation rules, and applicable privacy law. `/Ship`,
`/Gender`, `/Roast` and similar outputs are entertainment only and are
labelled as such in the replies.
