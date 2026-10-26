#!/usr/bin/env python3
"""Exercise the deployed HTTPS API. --persistence restarts only this app's Pod.

Creates disposable verification messages, removes only their IDs, and never
prints the administrator key. Requires an authenticated oc and the local key.
"""
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import argparse
import json
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
APP = "portfolio-202024191"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oc", default="oc")
    parser.add_argument("--url")
    parser.add_argument("--persistence", action="store_true")
    parser.add_argument("--output", default="docs/live-verification.json")
    args = parser.parse_args()

    def oc(*words):
        return subprocess.check_output([args.oc, *words], text=True).strip()

    base = (args.url or "https://" + oc("get", "route", APP, "-o", "jsonpath={.spec.host}")).rstrip("/")
    if not base.startswith("https://"):
        raise SystemExit("Use the HTTPS Route URL.")
    key = (ROOT / ".local/admin-key.txt").read_text().strip()
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "url": base,
              "project": oc("project", "-q"), "checks": []}
    created_ids = []

    def request(method, path, payload=None, auth=None, headers=None):
        headers = dict(headers or {})
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        if auth is not None:
            headers["Authorization"] = "Bearer " + auth
        try:
            response = urlopen(Request(base + path, data, headers, method=method), timeout=20)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read()
            body = json.loads(raw) if raw and "application/json" in response.headers.get("Content-Type", "") else raw.decode()
            return response.status, body

    def check(name, expected, actual, **details):
        assert actual == expected, f"{name}: expected {expected!r}, got {actual!r}"
        report["checks"].append({"name": name, "result": "PASS", **details})
        print("PASS", name)

    try:
        for path in ["/", "/admin", "/healthz", "/readyz", "/api/config"]:
            check("HTTPS " + path, 200, request("GET", path)[0])
        check("empty input rejected", 400, request("POST", "/api/guestbook", {"name": " ", "message": "test"})[0])
        check("long input rejected", 400, request("POST", "/api/guestbook", {"name": "test", "message": "x" * 501})[0])
        check("cross-origin rejected", 403, request("POST", "/api/guestbook", {"name": "test", "message": "test"}, headers={"Origin": "https://example.invalid"})[0])
        check("wrong key rejected", 401, request("GET", "/api/admin/check", auth="invalid-test-key-0000000000")[0])
        check("correct key accepted", 200, request("GET", "/api/admin/check", auth=key)[0])
        marker = "검증 전용 " + uuid.uuid4().hex[:8]
        literal = '<script>alert("test")</script> & SQLite \' parameter test'
        code, entry = request("POST", "/api/guestbook", {"name": marker, "message": literal})
        check("create guestbook entry", 201, code)
        entry_id = entry["id"]; created_ids.append(entry_id)
        found = next(x for x in request("GET", "/api/guestbook")[1]["items"] if x["id"] == entry_id)
        check("script and SQL punctuation preserved literally", literal, found["message"])
        check("unauthenticated deletion rejected", 401, request("DELETE", f"/api/guestbook/{entry_id}")[0])
        check("wrong-key deletion rejected", 401, request("DELETE", f"/api/guestbook/{entry_id}", auth="invalid-test-key-0000000000")[0])
        for path in ["/server.py", "/config.json", "/data/guestbook.sqlite3", "/.local/admin-key.txt"]:
            check("private path blocked " + path, 404, request("GET", path)[0])
        if args.persistence:
            pods = json.loads(oc("get", "pods", "-l", "app=" + APP, "-o", "json"))["items"]
            old_uid = pods[0]["metadata"]["uid"]
            pvc_uid = oc("get", "pvc", APP + "-data", "-o", "jsonpath={.metadata.uid}")
            oc("delete", "pod", "-l", "app=" + APP, "--wait=true", "--timeout=90s")
            oc("rollout", "status", "deployment/" + APP, "--timeout=180s")
            deadline = time.monotonic() + 180
            while True:
                pods = json.loads(oc("get", "pods", "-l", "app=" + APP, "-o", "json"))["items"]
                ready = [p for p in pods if p["metadata"]["uid"] != old_uid and
                         any(c["type"] == "Ready" and c["status"] == "True" for c in p.get("status", {}).get("conditions", []))]
                if ready: break
                if time.monotonic() > deadline: raise TimeoutError("Replacement Pod not ready")
                time.sleep(2)
            check("replacement Pod has a new UID", True, ready[0]["metadata"]["uid"] != old_uid,
                  old_pod_uid=old_uid, new_pod_uid=ready[0]["metadata"]["uid"])
            check("same PVC retained", pvc_uid, oc("get", "pvc", APP + "-data", "-o", "jsonpath={.metadata.uid}"), pvc_uid=pvc_uid)
            for attempt in range(15):
                code, body = request("GET", "/api/guestbook")
                if code == 200: break
                time.sleep(2)
            restored = next(x for x in body["items"] if x["id"] == entry_id)
            check("same entry survives Pod replacement", entry, restored, entry_id=entry_id)
        check("authenticated deletion", 204, request("DELETE", f"/api/guestbook/{entry_id}", auth=key)[0])
        created_ids.remove(entry_id)
        check("deleted entry absent", False, any(x["id"] == entry_id for x in request("GET", "/api/guestbook")[1]["items"]))
        check("deleting absent entry returns 404", 404, request("DELETE", f"/api/guestbook/{entry_id}", auth=key)[0])
        report["completed_at"] = datetime.now(timezone.utc).isoformat()
        report["result"] = "PASS"
        output = ROOT / args.output
        output.parent.mkdir(exist_ok=True, parents=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(f"Saved {len(report['checks'])} checks: {output}")
    finally:
        for entry_id in created_ids:
            try: request("DELETE", f"/api/guestbook/{entry_id}", auth=key)
            except Exception: pass


if __name__ == "__main__":
    main()
