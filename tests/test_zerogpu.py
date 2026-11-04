"""ZeroGPU startup-report regression tests.

These tests simulate the hosted ZeroGPU environment with a local stand-in for
SPACES_ZERO_DEVICE_API_URL. They do not require a GPU or torch.
"""

import json
import os
import subprocess
import sys
import threading
import unittest
import unittest.mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class StartupReportHandler(BaseHTTPRequestHandler):
    server_version = "ZeroGPUStandIn/1.0"

    def do_POST(self):
        self.server.requests.append((self.command, self.path))
        if self.path == "/startup-report":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


class ZeroGPUStartupTests(unittest.TestCase):
    def test_public_port_prefers_gradio_server_port_inside_space(self):
        import app

        with unittest.mock.patch.dict(
            os.environ,
            {"SPACE_ID": "test/space", "SPACES_ZERO_GPU": "true", "GRADIO_SERVER_PORT": "7860", "PORT": "7861"},
            clear=False,
        ):
            self.assertEqual(app.public_port(), 7860)
        with unittest.mock.patch.dict(
            os.environ,
            {"SPACE_ID": "", "SPACES_ZERO_GPU": "", "PORT": "17860"},
            clear=False,
        ):
            self.assertEqual(app.public_port(), 17860)

    def test_startup_report_is_sent_on_zerogpu(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), StartupReportHandler)
        server.requests = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            env = os.environ.copy()
            env.update(
                {
                    "SPACES_ZERO_GPU": "true",
                    "SPACES_ZERO_DEVICE_API_URL": f"http://127.0.0.1:{server.server_address[1]}",
                    "GRADIO_SERVER_PORT": "7860",
                    "PORT": "7861",
                    "GRADIO_SSR_MODE": "true",
                }
            )
            code = (
                "import json, app; "
                "print(json.dumps({"
                "'reported': app.report_zerogpu_startup(), "
                "'public_port': app.PUBLIC_PORT, "
                "'probe_registered': hasattr(app.zerogpu_probe, 'zerogpu')"
                "}))"
            )
            result = subprocess.run(
                [sys.executable, "-c", code],
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                env=env,
                text=True,
                capture_output=True,
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertTrue(payload["reported"])
            self.assertEqual(payload["public_port"], 7860)
            self.assertTrue(payload["probe_registered"])
            self.assertIn(("POST", "/startup-report"), server.requests)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_startup_report_is_noop_off_zerogpu(self):
        env = os.environ.copy()
        env.pop("SPACES_ZERO_GPU", None)
        env.pop("SPACE_ID", None)
        code = "import app; print(app.report_zerogpu_startup())"
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            env=env,
            text=True,
            capture_output=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().splitlines()[-1], "False")


if __name__ == "__main__":
    unittest.main()
