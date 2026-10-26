from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
import hashlib
import http.client
import json
import sqlite3
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import server

TEST_KEY = "test-only-" + "a" * 40
VERIFIER = hashlib.sha256(TEST_KEY.encode()).hexdigest()


class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "guestbook.sqlite3"
        self.start()

    def start(self):
        self.app = server.PortfolioServer(("127.0.0.1", 0), self.path, key_hash=VERIFIER)
        self.thread = threading.Thread(target=self.app.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.app.shutdown(); self.app.server_close(); self.thread.join()

    def tearDown(self):
        self.stop(); self.tmp.cleanup()

    def req(self, method, path, data=None, headers=None, raw=None):
        conn = http.client.HTTPConnection(*self.app.server_address, timeout=5)
        h = dict(headers or {})
        body = raw
        if data is not None:
            body = json.dumps(data, ensure_ascii=False).encode()
            h.setdefault("Content-Type", "application/json")
        conn.request(method, path, body, h)
        res = conn.getresponse()
        result = res.status, dict(res.getheaders()), res.read()
        conn.close()
        return result

    def add(self, name="방문자", message="프로젝트 잘 봤습니다."):
        code, _, body = self.req("POST", "/api/guestbook", {"name": name, "message": message})
        self.assertEqual(code, 201, body)
        return json.loads(body)

    def test_portfolio_contains_approved_career_and_projects(self):
        code, headers, body = self.req("GET", "/")
        self.assertEqual(code, 200)
        for text in ["WORKWAY", "레이어코퍼레이션", "공동창업자", "IRIT-Agent", "CADGen", "해운물류", "개인의창업", "PlayGround", "2023.09", "2025.01", "20년 공부하고", "4개"]:
            self.assertIn(text, body.decode())
        self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_static_allowlist_blocks_source_secrets_and_traversal(self):
        for path in ["/server.py", "/config.json", "/.env", "/data/guestbook.sqlite3", "/../server.py", "/%2e%2e/server.py", "/run/portfolio-admin/key-sha256", "/static/"]:
            self.assertEqual(self.req("GET", path)[0], 404, path)

    def test_head_and_public_configuration(self):
        code, headers, body = self.req("HEAD", "/")
        self.assertEqual(code, 200); self.assertEqual(body, b""); self.assertGreater(int(headers["Content-Length"]), 100)
        self.assertEqual(json.loads(self.req("GET", "/api/config")[2])["name"], "김성욱")
        self.assertNotIn(VERIFIER, self.req("GET", "/api/config")[2].decode())

    def test_round_trip_unicode_and_timestamp(self):
        item = self.add("김 방문 👋", "잘 봤어요.\n모바일과 AI의 연결이 인상적입니다.")
        items = json.loads(self.req("GET", "/api/guestbook")[2])["items"]
        self.assertEqual(items, [item]); self.assertTrue(item["created_at"].endswith("+00:00"))

    def test_invalid_fields_and_lengths(self):
        for data in [{}, {"name": " ", "message": "x"}, {"name": "x", "message": "\n"},
                     {"name": 3, "message": "x"}, {"name": "x"*41, "message": "x"},
                     {"name": "x", "message": "x"*501}, {"name": "x", "message": "\x00"}]:
            self.assertEqual(self.req("POST", "/api/guestbook", data)[0], 400)
        self.add("가" * 40, "나" * 500)

    def test_malformed_and_oversized_requests(self):
        self.assertEqual(self.req("POST", "/api/guestbook", headers={"Content-Type": "application/json"}, raw=b"[")[0], 400)
        self.assertEqual(self.req("POST", "/api/guestbook", headers={"Content-Type": "application/json"}, raw=b"x"*8193)[0], 413)
        self.assertEqual(self.req("POST", "/api/guestbook", raw=b"name=x")[0], 415)
        self.assertEqual(self.req("PUT", "/api/guestbook", {})[0], 405)

    def test_cross_origin_writes_are_rejected(self):
        data = {"name": "x", "message": "x"}
        self.assertEqual(self.req("POST", "/api/guestbook", data, {"Origin": "https://unrelated.example"})[0], 403)
        self.assertEqual(self.req("POST", "/api/guestbook", data, {"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.req("POST", "/api/guestbook", data, {"Origin": f"http://127.0.0.1:{self.app.server_port}"})[0], 201)

    def test_sql_and_script_strings_remain_literal_data(self):
        content = "<script>alert('xss')</script>\n'); DROP TABLE entries;--"
        item = self.add("<img src=x onerror=alert(1)>", content)
        self.assertEqual(json.loads(self.req("GET", "/api/guestbook")[2])["items"][0]["message"], content)
        self.add("두 번째", "테이블이 유지됩니다.")
        self.assertGreater(item["id"], 0)

    def test_admin_requires_correct_key_and_deletion_is_persistent(self):
        item = self.add()
        url = f"/api/guestbook/{item['id']}"
        self.assertEqual(self.req("DELETE", url)[0], 401)
        self.assertEqual(self.req("DELETE", url, headers={"Authorization": "Bearer " + "b" * 40})[0], 401)
        auth = {"Authorization": "Bearer " + TEST_KEY}
        self.assertEqual(self.req("GET", "/api/admin/check", headers=auth)[0], 200)
        self.assertEqual(self.req("DELETE", url, headers=auth)[0], 204)
        self.assertEqual(self.req("DELETE", url, headers=auth)[0], 404)
        self.stop(); self.start()
        self.assertEqual(json.loads(self.req("GET", "/api/guestbook")[2])["items"], [])

    def test_restart_retains_messages(self):
        item = self.add()
        self.stop(); self.start()
        self.assertEqual(json.loads(self.req("GET", "/api/guestbook")[2])["items"], [item])

    def test_pagination_has_no_duplicates(self):
        original = [self.add(message=f"메시지 {i}")["id"] for i in range(9)]
        page1 = json.loads(self.req("GET", "/api/guestbook")[2])
        page2 = json.loads(self.req("GET", f"/api/guestbook?before={page1['next_before']}")[2])
        self.assertEqual([i["id"] for i in page1["items"] + page2["items"]], list(reversed(original)))
        self.assertIsNone(page2["next_before"])
        for query in ["limit=0", "limit=100", "before=-1", "before=abc", "before=" + "9" * 60]:
            self.assertEqual(self.req("GET", "/api/guestbook?" + query)[0], 400)

    def test_concurrent_writes_do_not_lose_entries(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            items = list(pool.map(lambda i: self.add(message=str(i)), range(12)))
        self.assertEqual(len({i["id"] for i in items}), 12)
        self.assertEqual(len(json.loads(self.req("GET", "/api/guestbook?limit=20")[2])["items"]), 12)

    def test_readiness_failure_does_not_fail_liveness(self):
        self.assertEqual(self.req("GET", "/readyz")[0], 200)
        with patch.object(self.app.store, "ready", side_effect=sqlite3.OperationalError("unavailable")):
            self.assertEqual(self.req("GET", "/readyz")[0], 503)
            self.assertEqual(self.req("GET", "/healthz")[0], 200)
        self.assertEqual(json.loads(self.req("GET", "/api/guestbook")[2])["items"], [])

    def test_missing_secret_fails_closed(self):
        self.app.secret_path = str(Path(self.tmp.name) / "missing")
        self.assertEqual(self.req("GET", "/api/admin/check", headers={"Authorization": "Bearer " + TEST_KEY})[0], 503)

    def test_rate_limits_and_capacity_are_explicit(self):
        self.app.posts = server.RateLimiter(limit=1)
        self.add()
        self.assertEqual(self.req("POST", "/api/guestbook", {"name": "x", "message": "x"})[0], 429)
        self.app.posts = server.RateLimiter()
        with patch.object(server, "MAX_ENTRIES", 1):
            self.assertEqual(self.req("POST", "/api/guestbook", {"name": "x", "message": "x"})[0], 409)


if __name__ == "__main__":
    unittest.main()
