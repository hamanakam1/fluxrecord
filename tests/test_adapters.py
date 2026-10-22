import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from fluxrecord import dashboard, tools


class ToolsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bin = Path(self.temp.name) / "bin"
        self.bin.mkdir()
        self.tool = self.bin / "fluxdemo"
        self.tool.write_text("#!/bin/sh\n")
        self.tool.chmod(0o755)
        self.plain = self.bin / "notatool"
        self.plain.write_text("data")
        self.plain.chmod(0o644)
        self.patcher = mock.patch.dict(os.environ, {"PATH": str(self.bin)})
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.temp.cleanup()

    def test_scan_finds_executables_only(self):
        names = [entry["name"] for entry in tools.scan_path()]
        self.assertIn("fluxdemo", names)
        self.assertNotIn("notatool", names)

    def test_inventory_reports_count_and_boundary(self):
        result = tools.inventory()
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["tools"][0]["name"], "fluxdemo")
        self.assertIn("no executable was run", result["note"])

    def test_inventory_respects_the_report_limit(self):
        result = tools.inventory(limit=0)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["returned"], 0)
        self.assertTrue(result["truncated"])

    def test_history_adds_metadata_newest_first(self):
        result = tools.inventory(history=True)
        entry = result["tools"][0]
        self.assertIn("modified", entry)
        self.assertIn("bytes", entry)
        self.assertEqual(result["history"]["ordered"], "most recently modified first")
        self.assertIsNotNone(result["history"]["newest_modified_at"])

    def test_limit_bounds_the_inventory(self):
        self.assertEqual(len(tools.scan_path(limit=0)), 0)


class _Handler(BaseHTTPRequestHandler):
    payload = b'{"tickets": [{"id": "M-1", "title": "One", "status": "planned"}]}'
    redirect = False

    def do_GET(self):  # noqa: N802 - http.server API
        if _Handler.redirect:
            self.send_response(302)
            self.send_header("Location", "/api/tickets")
            self.end_headers()
            return
        body = _Handler.payload
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        return


class DashboardTest(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def test_preview_reads_tickets(self):
        result = dashboard.preview(self.origin, "planned", 0)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["tickets"][0]["id"], "M-1")
        self.assertEqual(result["boundary"]["method"], "GET")
        self.assertEqual(result["boundary"]["max_bytes"], dashboard.MAX_BYTES)
        self.assertFalse(result["truncated"])

    def test_preview_refuses_non_local_hosts(self):
        for url in ("http://example.com", "http://10.0.0.5:8080", "ftp://127.0.0.1"):
            with self.assertRaises(ValueError):
                dashboard.preview(url)

    def test_preview_refuses_empty_url_and_negative_offset(self):
        with self.assertRaises(ValueError):
            dashboard.preview("", "planned", 0)
        with self.assertRaises(ValueError):
            dashboard.preview(self.origin, "planned", -1)

    def test_a_bearer_token_is_sent_when_one_is_given(self):
        seen = {}

        class Recording(_Handler):
            def do_GET(self):  # noqa: N802 - http.server API
                seen["authorization"] = self.headers.get("Authorization")
                super().do_GET()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Recording)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            origin = f"http://127.0.0.1:{server.server_address[1]}"
            result = dashboard.preview(origin, "planned", 0, "a-token")
            self.assertEqual(seen["authorization"], "Bearer a-token")
            self.assertEqual(result["boundary"]["credentials"], "bearer token")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_no_credentials_are_sent_by_default(self):
        result = dashboard.preview(self.origin, "planned", 0)
        self.assertEqual(result["boundary"]["credentials"], "none")

    def test_preview_refuses_redirects(self):
        _Handler.redirect = True
        try:
            with self.assertRaises(OSError):
                dashboard.preview(self.origin, "planned", 0)
        finally:
            _Handler.redirect = False

    def test_preview_enforces_the_byte_ceiling(self):
        _Handler.payload = b'{"tickets": [' + b'{"id": "M-2"},' * 200 + b'{"id": "M-3"}]}'
        try:
            with mock.patch.object(dashboard, "MAX_BYTES", 64):
                result = dashboard.preview(self.origin, "planned", 0)
            self.assertTrue(result["truncated"])
            self.assertLessEqual(result["bytes"], 64)
            self.assertEqual(result["count"], 0)
        finally:
            _Handler.payload = b'{"tickets": [{"id": "M-1", "title": "One", "status": "planned"}]}'


class DashboardTargetTest(unittest.TestCase):
    def test_target_is_built_from_a_local_origin(self):
        target = dashboard._target("http://127.0.0.1:8080", "blocked", 12)
        self.assertEqual(target, "http://127.0.0.1:8080/api/tickets?status=blocked&offset=12")

    def test_a_url_with_credentials_is_refused(self):
        with self.assertRaises(ValueError):
            dashboard.preview("http://user:pass@127.0.0.1:8080", "planned", 0)

    def test_status_filter_cannot_inject_query_parameters(self):
        with self.assertRaises(ValueError):
            dashboard._target("http://localhost:8080", "planned&offset=999", 0)
        with self.assertRaises(ValueError):
            dashboard._target("http://localhost:8080", "", 0)


if __name__ == "__main__":
    unittest.main()
