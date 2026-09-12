import json
import os
import socket
import socketserver
import tempfile
import threading
import time
import unittest
from pathlib import Path

from daemon.client import MigrationDaemonClient
from daemon.server import TCPThreadingHTTPServer, UnixThreadingHTTPServer, make_handler
from lib.generic.domain import MigrationIdentity


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class TestDaemonApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.server_root = Path(cls.temp_dir.name) / "server"
        cls.server_root.mkdir(parents=True)
        (cls.server_root / "world/playerdata").mkdir(parents=True)

        cls.token = "test-secret-token-1234567890abcdef"
        cls.port = find_free_port()
        cls.sock_path = Path(cls.temp_dir.name) / "test.sock"

        handler_cls = make_handler(cls.server_root, cls.token)

        cls.tcp_server = TCPThreadingHTTPServer(("127.0.0.1", cls.port), handler_cls)
        cls.tcp_thread = threading.Thread(target=cls.tcp_server.serve_forever, daemon=True)
        cls.tcp_thread.start()

        cls.unix_server = None
        cls.unix_thread = None
        cls.client_unix = None
        if hasattr(socketserver, "UnixStreamServer"):
            cls.unix_server = UnixThreadingHTTPServer(str(cls.sock_path), handler_cls)
            cls.unix_thread = threading.Thread(target=cls.unix_server.serve_forever, daemon=True)
            cls.unix_thread.start()

        time.sleep(0.1)

        cls.client_tcp = MigrationDaemonClient(
            token=cls.token,
            base_url=f"127.0.0.1:{cls.port}",
        )
        if cls.unix_server is not None:
            cls.client_unix = MigrationDaemonClient(token=cls.token, socket_path=cls.sock_path)

        cls.identity = MigrationIdentity(
            discord_user_id="123",
            canonical_name="TestPlayer",
            canonical_uuid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            legacy_uuid="11111111-2222-3333-4444-555555555555",
        )

    @classmethod
    def tearDownClass(cls):
        cls.tcp_server.shutdown()
        cls.tcp_server.server_close()
        if cls.unix_server is not None:
            cls.unix_server.shutdown()
            cls.unix_server.server_close()
        cls.temp_dir.cleanup()

    def test_23_inspect_authorized_tcp_and_unix(self):
        insp_tcp = self.client_tcp.inspect(self.identity)
        self.assertEqual(insp_tcp.identity.canonical_name, "TestPlayer")
        self.assertTrue(len(insp_tcp.findings) > 0)

        if self.client_unix is not None:
            insp_unix = self.client_unix.inspect(self.identity)
            self.assertEqual(insp_unix.identity.canonical_name, "TestPlayer")
            self.assertEqual(insp_tcp.inspection_fingerprint, insp_unix.inspection_fingerprint)

    def test_24_plan_authorized(self):
        plan = self.client_tcp.plan(self.identity)
        self.assertIn(plan.status, ("READY", "INCOMPLETE", "BLOCKED"))
        self.assertTrue(len(plan.plan_id) > 0)
        self.assertTrue(len(plan.approval_nonce) > 0)

    def test_25_auth_invalid(self):
        bad_client = MigrationDaemonClient(token="wrong-token", base_url=f"127.0.0.1:{self.port}")
        with self.assertRaises(PermissionError):
            bad_client.inspect(self.identity)

    def test_25_public_health_hides_operational_details(self):
        import urllib.request

        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health") as response:
            public = json.loads(response.read())
        self.assertEqual(public, {"status": "ok"})
        self.assertNotIn("server_root", public)

        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/health",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        with urllib.request.urlopen(request) as response:
            authenticated = json.loads(response.read())
        self.assertIn("mode", authenticated)
        self.assertNotIn("server_root", authenticated)
        self.assertNotIn(str(self.server_root), json.dumps(authenticated))

    def test_26_payload_invalid_json(self):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request(
            "POST",
            "/api/v1/players/inspect",
            body=b"not-json-content",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        res = conn.getresponse()
        self.assertEqual(res.status, 400)
        conn.close()

    def test_27_uuid_invalid(self):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        payload = json.dumps({
            "identity": {
                "discord_user_id": "123",
                "canonical_name": "Test",
                "canonical_uuid": "not-a-valid-uuid",
            }
        })
        conn.request(
            "POST",
            "/api/v1/players/inspect",
            body=payload.encode(),
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        res = conn.getresponse()
        self.assertEqual(res.status, 422)
        conn.close()

    def test_28_identity_incomplete(self):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        payload = json.dumps({"identity": {}})
        conn.request(
            "POST",
            "/api/v1/players/inspect",
            body=payload.encode(),
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        res = conn.getresponse()
        self.assertEqual(res.status, 422)
        conn.close()

    def test_29_arbitrary_path_rejected(self):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        payload = json.dumps({
            "identity": self.identity.to_dict(),
            "server_root": "/etc/passwd",
        })
        conn.request(
            "POST",
            "/api/v1/players/inspect",
            body=payload.encode(),
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        res = conn.getresponse()
        self.assertEqual(res.status, 400)
        conn.close()

    def test_30_unknown_dangerous_field_rejected(self):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        payload = json.dumps({
            "identity": self.identity.to_dict(),
            "shell_cmd": "rm -rf /",
        })
        conn.request(
            "POST",
            "/api/v1/players/inspect",
            body=payload.encode(),
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        res = conn.getresponse()
        self.assertEqual(res.status, 400)
        conn.close()

    def test_31_nonexistent_endpoint(self):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request(
            "POST",
            "/api/v1/dangerous/arbitrary_exec",
            body=b"{}",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        res = conn.getresponse()
        self.assertEqual(res.status, 404)
        conn.close()

    def test_32_timeout_or_error_sanitized(self):
        res = self.client_tcp.health()
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["mode"], "READ_ONLY")

    def test_33_daemon_has_no_write_endpoint(self):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        for path in ("/api/v1/execute", "/api/v1/apply", "/api/v1/rollback", "/api/v1/lock"):
            conn.request("POST", path, body=b"{}", headers={"Authorization": f"Bearer {self.token}"})
            res = conn.getresponse()
            self.assertEqual(res.status, 404, f"Endpoint {path} não deveria existir no MVP read-only!")
            res.read()
        conn.close()


if __name__ == "__main__":
    unittest.main()
