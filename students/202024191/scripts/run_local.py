#!/usr/bin/env python3
"""Run the same app locally, with an ignored SQLite database and no dependencies."""
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
(ROOT / ".local").mkdir(exist_ok=True)
os.environ.setdefault("BIND_ADDRESS", "127.0.0.1")
os.environ.setdefault("PORT", "8080")
os.environ.setdefault("DATABASE_PATH", str(ROOT / ".local/guestbook.sqlite3"))
os.environ.setdefault("ADMIN_KEY_HASH_FILE", str(ROOT / "admin-key.sha256"))
os.execv(sys.executable, [sys.executable, str(ROOT / "app/server.py")])
