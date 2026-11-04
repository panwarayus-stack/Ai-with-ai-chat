"""Hugging Face Space entrypoint for OpenCode Web.

ZeroGPU Spaces use the Gradio SDK. The real OpenCode terminal is still the
Node/Express/xterm.js app in this repository; this Gradio/FastAPI process only
supervises it and reverse-proxies HTTP + WebSocket traffic on the single public
Space port (7860). OpenCode itself does not use the ZeroGPU GPU.
"""

from __future__ import annotations

import asyncio
import atexit
import hashlib
import logging
import os
import shutil
import socket
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import spaces
import gradio as gr
import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from websockets.asyncio.client import connect as ws_connect

ROOT = Path(__file__).resolve().parent


def public_port() -> int:
    """Return the port the Hugging Face Space router expects.

    Gradio Spaces expose GRADIO_SERVER_PORT (normally 7860). Some ZeroGPU
    runtimes also set PORT to an internal port, so prefer the Gradio port when
    running inside a Space. Local development can still use PORT.
    """
    if os.environ.get("SPACE_ID") or os.environ.get("SPACES_ZERO_GPU"):
        return int(os.environ.get("GRADIO_SERVER_PORT", "7860"))
    return int(os.environ.get("PORT", "7860"))


PUBLIC_PORT = public_port()
NODE_MIN_MAJOR = 22
NODE_MAX_MAJOR = 25
HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-encoding",
    "content-length",
}

load_dotenv(ROOT / ".env")
# The terminal is served by Node/xterm.js. Gradio's optional Node SSR renderer
# is not needed and can otherwise occupy the same internal port range.
os.environ["GRADIO_SSR_MODE"] = "false"
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("opencode-space")

node_process: Optional[subprocess.Popen] = None
internal_port: Optional[int] = None
http_client: Optional[httpx.AsyncClient] = None


# ZeroGPU requires at least one @spaces.GPU function to be present at startup.
# The terminal does not call this function, so normal OpenCode use does not
# consume GPU quota. It exists only as an explicit ZeroGPU compatibility probe.
@spaces.GPU(duration=1)
def zerogpu_probe() -> dict:
    try:
        import torch
        available = torch.cuda.is_available()
        return {
            "cuda_available": available,
            "device": torch.cuda.get_device_name(0) if available else None,
        }
    except Exception as exc:  # pragma: no cover - only used if manually called
        return {"cuda_available": False, "error": str(exc)}


def report_zerogpu_startup() -> bool:
    """Send ZeroGPU's startup report when serving FastAPI with uvicorn.

    The `spaces` package normally sends this report from a patched
    `gradio.Blocks.launch()`. This Space mounts Gradio onto FastAPI and calls
    `uvicorn.run()` instead, so `launch()` is never reached. Without the report
    the platform stops the container with:
    "No @spaces.GPU function detected during startup".

    Off ZeroGPU, `spaces.zero.startup` is not defined and this is a no-op.
    """
    try:
        from spaces.zero import startup  # type: ignore[attr-defined]
    except ImportError:
        return False
    startup()
    log.info("ZeroGPU startup report sent")
    return True


def _node_version_ok(executable: str) -> bool:
    try:
        out = subprocess.check_output([executable, "--version"], text=True, timeout=10).strip()
        major = int(out.lstrip("v").split(".", 1)[0])
        return NODE_MIN_MAJOR <= major < NODE_MAX_MAJOR
    except Exception:
        return False


def node_tools() -> tuple[list[str], list[str], Optional[Path]]:
    """Return node command, npm command, and optional Node header directory.

    Prefer the pinned nodejs-wheel runtime because it also ships the headers
    node-gyp needs to build node-pty. Fall back to a suitable system Node only
    when the wheel is not installed (for example, plain local Node development).
    """
    try:
        import nodejs_wheel
    except ImportError:
        nodejs_wheel = None

    if nodejs_wheel is not None:
        base = Path(nodejs_wheel.__file__).resolve().parent
        node = base / "bin" / "node"
        npm_cli = base / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js"
        if node.exists() and npm_cli.exists() and _node_version_ok(str(node)):
            return [str(node)], [str(node), str(npm_cli)], base

    system_node = shutil.which("node")
    system_npm = shutil.which("npm")
    if system_node and system_npm and _node_version_ok(system_node):
        return [system_node], [system_npm], None

    raise RuntimeError(
        "Node.js 22-24 is required. Install nodejs-wheel from requirements.txt "
        "or provide node/npm on PATH."
    )


def ensure_npm_dependencies(node_cmd: list[str], npm_cmd: list[str], nodedir: Optional[Path]) -> None:
    lock = ROOT / "package-lock.json"
    package = ROOT / "package.json"
    stamp = ROOT / "node_modules" / ".opencode-space-stamp"
    digest = hashlib.sha256()
    for file in (package, lock):
        digest.update(file.read_bytes())
    version = subprocess.check_output(node_cmd + ["--version"], text=True).strip()
    digest.update(version.encode())
    expected = digest.hexdigest()
    if stamp.exists() and stamp.read_text().strip() == expected:
        log.info("Node dependencies already installed for %s", version)
        return

    env = os.environ.copy()
    if nodedir is not None:
        env["npm_config_nodedir"] = str(nodedir)
    log.info("Installing Node dependencies with %s", version)
    subprocess.run(
        npm_cmd + ["ci", "--omit=dev", "--no-audit", "--no-fund"],
        cwd=ROOT,
        env=env,
        check=True,
    )
    stamp.write_text(expected + "\n")


def ensure_runtime_dirs() -> tuple[Path, Path]:
    workspace = Path(os.environ.get("WORKSPACE_DIR", str(Path.home() / "opencode-workspace"))).expanduser()
    data_dir = Path(os.environ.get("DATA_DIR", str(ROOT / "data"))).expanduser()
    if not workspace.is_absolute():
        workspace = (ROOT / workspace).resolve()
    if not data_dir.is_absolute():
        data_dir = (ROOT / data_dir).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(data_dir, 0o700)

    policy = workspace / "opencode.json"
    if not policy.exists():
        policy.write_text(
            '{\n  "$schema": "https://opencode.ai/config.json",\n'
            '  "permission": { "external_directory": { "*": "ask" } }\n}\n'
        )
        os.chmod(policy, 0o600)
    return workspace, data_dir


def free_loopback_port() -> int:
    """Ask the OS for an unused loopback port instead of assuming 7861 is free."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def child_environment(workspace: Path, data_dir: Path, port: int) -> dict[str, str]:
    env = os.environ.copy()
    env["HOST"] = "127.0.0.1"
    env["PORT"] = str(port)
    env["WORKSPACE_DIR"] = str(workspace)
    env["DATA_DIR"] = str(data_dir)
    env.setdefault("COOKIE_SECURE", "true")
    secure_cookies = env.get("COOKIE_SECURE", "true").lower() == "true"
    env.setdefault("COOKIE_SAME_SITE", "none" if secure_cookies else "strict")
    # The Node backend is always behind this Space proxy, even locally.
    env["TRUST_PROXY"] = "1"
    env.setdefault("MAX_SESSIONS", "2")
    env.setdefault(
        "FRAME_ANCESTORS",
        "'self' https://huggingface.co https://*.huggingface.co https://*.hf.space",
    )
    space_host = os.environ.get("SPACE_HOST")
    if space_host and not env.get("PUBLIC_URL"):
        env["PUBLIC_URL"] = f"https://{space_host}"
    return env


def start_node() -> int:
    global node_process, internal_port
    password = os.environ.get("WEB_PASSWORD", "")
    if len(password) < 16:
        raise RuntimeError(
            "WEB_PASSWORD is missing or too short. Add a Space secret named WEB_PASSWORD "
            "with at least 16 characters."
        )

    node_cmd, npm_cmd, nodedir = node_tools()
    ensure_npm_dependencies(node_cmd, npm_cmd, nodedir)
    workspace, data_dir = ensure_runtime_dirs()
    port = free_loopback_port()
    env = child_environment(workspace, data_dir, port)

    log.info("Starting OpenCode Node backend on 127.0.0.1:%s", port)
    node_process = subprocess.Popen(node_cmd + ["server.js"], cwd=ROOT, env=env)
    atexit.register(stop_node)

    deadline = time.monotonic() + 120
    url = f"http://127.0.0.1:{port}/api/health"
    try:
        while time.monotonic() < deadline:
            if node_process.poll() is not None:
                raise RuntimeError(f"OpenCode backend exited early with code {node_process.returncode}")
            try:
                response = httpx.get(url, timeout=2)
                if response.status_code == 200 and response.json() == {"ok": True, "service": "opencode-web"}:
                    internal_port = port
                    log.info("OpenCode backend is healthy")
                    return port
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(0.5)
        raise RuntimeError("OpenCode backend did not become healthy within 120 seconds")
    except Exception:
        stop_node()
        raise


def stop_node() -> None:
    global node_process, internal_port
    internal_port = None
    if node_process is None:
        return
    proc = node_process
    node_process = None
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    log.info("OpenCode Node backend stopped")


@asynccontextmanager
async def lifespan(_: FastAPI):
    global http_client
    port = await asyncio.to_thread(start_node)
    http_client = httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{port}",
        timeout=httpx.Timeout(60.0, connect=10.0),
        follow_redirects=False,
    )
    try:
        yield
    finally:
        if http_client is not None:
            await http_client.aclose()
            http_client = None
        stop_node()


app = FastAPI(title="OpenCode Web Space", docs_url=None, redoc_url=None, lifespan=lifespan)


def _proxy_request_headers(request: Request) -> list[tuple[str, str]]:
    excluded = HOP_HEADERS | {"x-forwarded-for", "x-forwarded-proto", "x-forwarded-host"}
    headers = [(k, v) for k, v in request.headers.raw if k.decode().lower() not in excluded]
    client_host = request.client.host if request.client else ""
    forwarded_for = request.headers.get("x-forwarded-for")
    default_proto = "https" if os.environ.get("SPACE_HOST") else request.url.scheme
    headers.append((b"x-forwarded-for", (forwarded_for or client_host).encode()))
    headers.append((b"x-forwarded-proto", request.headers.get("x-forwarded-proto", default_proto).encode()))
    headers.append((b"x-forwarded-host", request.headers.get("host", "").encode()))
    return headers


async def _proxy_http(request: Request) -> Response:
    if http_client is None:
        return Response("OpenCode backend is starting", status_code=503)
    path = request.url.path
    query = request.url.query.encode()
    body = await request.body()
    upstream = http_client.build_request(
        request.method,
        httpx.URL(path=path, query=query),
        headers=_proxy_request_headers(request),
        content=body,
    )
    resp = await http_client.send(upstream)
    out = Response(content=resp.content, status_code=resp.status_code)
    for key, value in resp.headers.multi_items():
        lower = key.lower()
        if lower in HOP_HEADERS or lower == "content-type":
            continue
        out.headers.append(key, value)
    content_type = resp.headers.get("content-type")
    if content_type:
        out.headers["content-type"] = content_type
    return out


@app.websocket("/ws")
async def proxy_websocket(ws: WebSocket) -> None:
    await ws.accept()
    port = internal_port
    if port is None:
        await ws.close(code=1013)
        return
    headers = {
        k: v for k, v in ws.headers.items()
        if k.lower() in {"cookie", "origin", "user-agent", "host"}
    }
    default_proto = "https" if os.environ.get("SPACE_HOST") else "http"
    headers["x-forwarded-for"] = ws.headers.get("x-forwarded-for") or (ws.client.host if ws.client else "")
    headers["x-forwarded-proto"] = ws.headers.get("x-forwarded-proto", default_proto)
    headers["x-forwarded-host"] = ws.headers.get("host", "")
    try:
        async with ws_connect(
            f"ws://127.0.0.1:{port}/ws",
            additional_headers=headers,
            max_size=8 * 1024 * 1024,
            ping_interval=20,
            ping_timeout=20,
        ) as upstream:
            async def browser_to_node() -> None:
                while True:
                    message = await ws.receive()
                    kind = message.get("type")
                    if kind == "websocket.disconnect":
                        await upstream.close()
                        return
                    text = message.get("text")
                    data = message.get("bytes")
                    if text is not None:
                        await upstream.send(text)
                    elif data is not None:
                        await upstream.send(data)

            async def node_to_browser() -> None:
                async for message in upstream:
                    if isinstance(message, bytes):
                        await ws.send_bytes(message)
                    else:
                        await ws.send_text(message)

            tasks = [asyncio.create_task(browser_to_node()), asyncio.create_task(node_to_browser())]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            for task in done:
                task.result()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.warning("WebSocket proxy closed: %s", exc)
        try:
            await ws.close(code=1011)
        except Exception:
            pass


with gr.Blocks(title="OpenCode Web Status") as status_ui:
    gr.Markdown(
        "# OpenCode Web\n"
        "The real OpenCode terminal is served at `/`. This Gradio page exists because "
        "ZeroGPU Spaces use the Gradio SDK. OpenCode does not consume ZeroGPU GPU quota."
    )
    gr.HTML('<a href="/" target="_top">Open the terminal</a>')

app = gr.mount_gradio_app(app, status_ui, path="/gradio", ssr_mode=False)


@app.api_route("/", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def proxy_root(request: Request) -> Response:
    return await _proxy_http(request)


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def proxy_all(request: Request, path: str) -> Response:
    return await _proxy_http(request)


if __name__ == "__main__":
    report_zerogpu_startup()
    uvicorn.run(app, host="0.0.0.0", port=PUBLIC_PORT, ws_max_size=8 * 1024 * 1024)
