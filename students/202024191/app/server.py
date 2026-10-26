#!/usr/bin/env python3
"""Small, dependency-free portfolio/guestbook for the OpenShift assignment.

Only explicitly listed public assets are served. Source, configuration, SQLite,
and Secret volumes are never exposed as a directory by the HTTP handler.
"""
from collections import OrderedDict, deque
from contextlib import closing
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import hashlib
import hmac
import json
import os
import re
import signal
import socket
import sqlite3
import threading
import time

ROOT = Path(__file__).resolve().parent
PUBLIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/admin": ("admin.html", "text/html; charset=utf-8"),
    "/admin/": ("admin.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/admin.js": ("admin.js", "text/javascript; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}
MAX_BODY = 8192
MAX_ENTRIES = 1000
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; "
       "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
       "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")


class ApiError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class RateLimiter:
    """Bounded memory and a short window; no visitor identifiers are persisted."""
    def __init__(self, limit=20, window=60):
        self.limit, self.window = limit, window
        self.entries = OrderedDict()
        self.lock = threading.Lock()

    def allow(self, key):
        now = time.monotonic()
        with self.lock:
            q = self.entries.setdefault(key, deque())
            self.entries.move_to_end(key)
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            while len(self.entries) > 1024:
                self.entries.popitem(last=False)
            return True


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 40),
                message TEXT NOT NULL CHECK(length(message) BETWEEN 1 AND 500),
                created_at TEXT NOT NULL
            )""")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=3)
        db.row_factory = sqlite3.Row
        return db

    def ready(self):
        with closing(self.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("SELECT id FROM entries LIMIT 1").fetchall()
            # Verify write access without retaining a readiness-test record.
            db.execute("INSERT INTO entries(name,message,created_at) VALUES(?,?,?)",
                       ("probe", "probe", "probe"))
            db.rollback()

    def listing(self, before=None, limit=6):
        with closing(self.connect()) as db:
            if before is None:
                rows = db.execute("SELECT * FROM entries ORDER BY id DESC LIMIT ?", (limit + 1,)).fetchall()
            else:
                rows = db.execute("SELECT * FROM entries WHERE id < ? ORDER BY id DESC LIMIT ?",
                                  (before, limit + 1)).fetchall()
        items = [dict(row) for row in rows[:limit]]
        return {"items": items, "next_before": items[-1]["id"] if len(rows) > limit else None}

    def add(self, name, message):
        created = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT count(*) FROM entries").fetchone()[0] >= MAX_ENTRIES:
                raise ApiError(409, "방명록이 가득 찼습니다. 관리자에게 정리를 요청해 주세요.")
            cur = db.execute("INSERT INTO entries(name,message,created_at) VALUES(?,?,?)", (name, message, created))
            return {"id": cur.lastrowid, "name": name, "message": message, "created_at": created}

    def remove(self, entry_id):
        with closing(self.connect()) as db, db:
            return db.execute("DELETE FROM entries WHERE id = ?", (entry_id,)).rowcount == 1


class PortfolioServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 32

    def __init__(self, address, db_path, config_path=None, key_hash=None, secret_path=None):
        self.store = Store(db_path)
        self.config = json.loads(Path(config_path or ROOT / "config.json").read_text(encoding="utf-8"))
        self.static_dir = ROOT / "static"
        self.key_hash, self.secret_path = key_hash, secret_path
        self.slots = threading.BoundedSemaphore(32)
        self.posts = RateLimiter(20, 60)
        self.auth_attempts = RateLimiter(30, 60)
        self.admin_hash()  # A missing or malformed Secret is an explicit startup error.
        super().__init__(address, Handler)

    def admin_hash(self):
        value = Path(self.secret_path).read_text().strip() if self.secret_path else (self.key_hash or "")
        if not re.fullmatch(r"[a-f0-9]{64}", value):
            raise ValueError("A SHA-256 administrator key verifier is required")
        return value

    def get_request(self):
        conn, address = super().get_request()
        conn.settimeout(10)
        return conn, address

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "Portfolio"
    sys_version = ""

    def log_message(self, fmt, *args):
        # Do not log Authorization headers, message bodies, or query strings.
        print(json.dumps({"method": self.command, "path": urlsplit(self.path).path[:120],
                          "status": str(args[1]) if len(args) > 1 else "request"}), flush=True)

    def send_data(self, status, body=b"", content_type="application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store" if self.path.startswith("/api/") else "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if status == 429:
            self.send_header("Retry-After", "60")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def json_response(self, status, payload):
        self.send_data(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def require_same_origin(self):
        origin = self.headers.get("Origin")
        if origin and origin not in {"https://" + self.headers.get("Host", ""),
                                    "http://" + self.headers.get("Host", "")}:
            raise ApiError(403, "다른 사이트에서 보낸 요청은 허용하지 않습니다.")
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise ApiError(403, "다른 사이트에서 보낸 요청은 허용하지 않습니다.")

    def require_admin(self):
        self.require_same_origin()
        if not self.server.auth_attempts.allow(self.client_address[0]):
            raise ApiError(429, "인증 요청이 많습니다. 잠시 후 다시 시도해 주세요.")
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or not (20 <= len(auth[7:]) <= 256):
            raise ApiError(401, "관리자 인증키를 확인해 주세요.")
        actual = hashlib.sha256(auth[7:].encode("utf-8")).hexdigest()
        try:
            valid = hmac.compare_digest(actual, self.server.admin_hash())
        except (OSError, ValueError):
            raise ApiError(503, "관리자 인증 설정을 확인할 수 없습니다.") from None
        if not valid:
            raise ApiError(401, "관리자 인증키를 확인해 주세요.")

    def read_json(self):
        if self.headers.get("Transfer-Encoding"):
            raise ApiError(400, "지원하지 않는 요청 형식입니다.")
        if self.headers.get_content_type() != "application/json":
            raise ApiError(415, "JSON 형식으로 요청해 주세요.")
        raw_lengths = self.headers.get_all("Content-Length", [])
        if len(raw_lengths) != 1:
            raise ApiError(411, "요청의 크기를 지정해 주세요.")
        try:
            size = int(raw_lengths[0])
        except ValueError:
            raise ApiError(400, "올바르지 않은 요청 크기입니다.") from None
        if not 0 < size <= MAX_BODY:
            raise ApiError(413, "입력 내용이 너무 큽니다.")
        try:
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise ValueError()
            result = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, socket.timeout):
            raise ApiError(400, "입력 내용을 다시 확인해 주세요.") from None
        if not isinstance(result, dict):
            raise ApiError(400, "올바른 입력 형식이 아닙니다.")
        return result

    def dispatch(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        if self.command in {"GET", "HEAD"}:
            if path in PUBLIC_FILES:
                name, content_type = PUBLIC_FILES[path]
                return self.send_data(200, (self.server.static_dir / name).read_bytes(), content_type)
            if path == "/healthz":
                return self.json_response(200, {"status": "ok"})
            if path == "/readyz":
                self.server.store.ready()
                return self.json_response(200, {"status": "ready"})
            if path == "/api/config":
                return self.json_response(200, self.server.config)
            if path == "/api/admin/check":
                self.require_admin()
                return self.json_response(200, {"authenticated": True})
            if path == "/api/guestbook":
                try:
                    query = parse_qs(parsed.query, max_num_fields=8)
                    before = int(query["before"][0]) if "before" in query else None
                    limit = int(query.get("limit", ["6"])[0])
                    if not (1 <= limit <= 20) or (before is not None and not 0 < before < 2**63):
                        raise ValueError()
                except (ValueError, IndexError):
                    raise ApiError(400, "조회 범위가 올바르지 않습니다.") from None
                return self.json_response(200, self.server.store.listing(before, limit))
        elif self.command == "POST" and path == "/api/guestbook":
            self.require_same_origin()
            data = self.read_json()
            name, message = data.get("name"), data.get("message")
            if not isinstance(name, str) or not isinstance(message, str):
                raise ApiError(400, "이름과 메시지를 입력해 주세요.")
            name, message = name.strip(), message.strip()
            try:
                (name + message).encode("utf-8")
            except UnicodeEncodeError:
                raise ApiError(400, "지원하지 않는 문자가 있습니다.") from None
            if not 1 <= len(name) <= 40 or not 1 <= len(message) <= 500 or "\x00" in name + message:
                raise ApiError(400, "이름은 1~40자, 메시지는 1~500자로 입력해 주세요.")
            if not self.server.posts.allow(self.client_address[0]):
                raise ApiError(429, "메시지가 많이 등록되었습니다. 잠시 후 다시 남겨 주세요.")
            return self.json_response(201, self.server.store.add(name, message))
        elif self.command == "DELETE" and re.fullmatch(r"/api/guestbook/[1-9][0-9]{0,17}", path):
            self.require_admin()
            if not self.server.store.remove(int(path.rsplit("/", 1)[-1])):
                raise ApiError(404, "이미 삭제되었거나 존재하지 않는 메시지입니다.")
            return self.send_data(204)
        elif self.command not in {"GET", "HEAD", "POST", "DELETE"}:
            raise ApiError(405, "지원하지 않는 요청입니다.")
        raise ApiError(404, "요청한 페이지를 찾을 수 없습니다.")

    def handle_request(self):
        try:
            self.dispatch()
        except ApiError as exc:
            self.json_response(exc.status, {"error": exc.message})
        except (sqlite3.Error, OSError, ValueError):
            self.json_response(503, {"error": "잠시 서비스를 이용할 수 없습니다. 다시 시도해 주세요."})

    do_GET = handle_request
    do_HEAD = handle_request
    do_POST = handle_request
    do_DELETE = handle_request
    do_PUT = handle_request
    do_PATCH = handle_request
    do_OPTIONS = handle_request


def main():
    server = PortfolioServer(
        (os.environ.get("BIND_ADDRESS", "0.0.0.0"), int(os.environ.get("PORT", "8080"))),
        os.environ.get("DATABASE_PATH", "/data/guestbook.sqlite3"),
        config_path=os.environ.get("PORTFOLIO_CONFIG"),
        key_hash=os.environ.get("ADMIN_KEY_HASH"),
        secret_path=os.environ.get("ADMIN_KEY_HASH_FILE"),
    )
    def stop(signum, frame):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"Portfolio listening on {server.server_address[0]}:{server.server_address[1]}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
