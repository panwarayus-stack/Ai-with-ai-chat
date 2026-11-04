"""Regression tests for the Hugging Face Space wrapper.

Run after installing requirements.txt:
    .venv/bin/python -m unittest discover -s tests -p 'test_*.py'
"""

import os
import socket
import unittest
from pathlib import Path
from unittest import mock

# Simulate the hosted Space setting that previously started Gradio's Node SSR
# renderer and collided with the OpenCode backend.
os.environ["GRADIO_SSR_MODE"] = "true"

import app  # noqa: E402


class SpaceWrapperTests(unittest.TestCase):
    def test_gradio_ssr_is_disabled_even_when_environment_enables_it(self):
        self.assertFalse(app.status_ui.ssr_mode)
        self.assertIsNone(getattr(app.status_ui, "node_process", None))
        self.assertEqual(os.environ["GRADIO_SSR_MODE"], "false")

    def test_internal_port_is_not_the_old_fixed_gradio_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
            try:
                blocker.bind(("127.0.0.1", 7861))
            except OSError:
                pass  # Already occupied: the allocator must still avoid it.
            port = app.free_loopback_port()
        self.assertNotEqual(port, 7861)
        self.assertGreater(port, 0)

    def test_child_environment_overrides_gradio_port_mutations(self):
        with mock.patch.dict(os.environ, {"PORT": "7861", "HOST": "0.0.0.0", "SPACE_HOST": "example.hf.space"}, clear=False):
            env = app.child_environment(Path("/tmp/workspace"), Path("/tmp/data"), 43210)
        self.assertEqual(env["PORT"], "43210")
        self.assertEqual(env["HOST"], "127.0.0.1")
        self.assertEqual(env["PUBLIC_URL"], "https://example.hf.space")

    def test_missing_password_fails_before_starting_node(self):
        with mock.patch.dict(os.environ, {"WEB_PASSWORD": "short"}, clear=False), \
             mock.patch.object(app, "node_tools") as node_tools, \
             self.assertRaisesRegex(RuntimeError, "WEB_PASSWORD"):
            app.start_node()
        node_tools.assert_not_called()

    def test_stop_node_clears_internal_port(self):
        process = mock.Mock()
        process.poll.return_value = None
        with mock.patch.object(app, "node_process", process), \
             mock.patch.object(app, "internal_port", 43210):
            app.stop_node()
        self.assertIsNone(app.internal_port)
        process.terminate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
