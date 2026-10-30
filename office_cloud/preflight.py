"""Read-only inventory and existing-thread checks for a PC-independent Office.

No model turn, authentication, data export, database initialization or fallback
thread creation is performed. Reports must stay in the private local directory.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import queue
import re
import shutil
import sqlite3
import subprocess
import threading
import time


_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
_READ_METHODS = frozenset({
    "initialize", "account/read", "model/list", "thread/read", "thread/turns/list",
})
_MAX_LINE = 4 * 1024 * 1024


def _json_file(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object: " + path.name)
    return value


def inventory(root: Path) -> dict:
    """Read the actual registry; never seed missing departments or read messages."""
    root = root.resolve(strict=True)
    database = root / "office.db"
    settings = _json_file(root / "settings.json")
    connection = _json_file(root / "local_backups/desktop-chat/connection.json")
    hq = connection.get("thread_id")
    if not isinstance(hq, str) or not _UUID.fullmatch(hq):
        raise ValueError("The existing HQ thread identity is missing or invalid")
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=10)) as db:
        db.execute("PRAGMA query_only=ON")
        row = db.execute("SELECT data FROM office_departments WHERE id='registry'").fetchone()
        if not row:
            raise ValueError("The existing department registry is missing")
        registry = json.loads(row[0])
    threads = [{"role": "hq", "thread_id": hq, "host": "local"}]
    seen = {hq}
    for team in registry.get("teams", []):
        for agent in team.get("agents", []):
            ident = agent.get("thread_id")
            if not ident:
                continue
            if not isinstance(ident, str) or not _UUID.fullmatch(ident):
                raise ValueError("A registered department thread identity is invalid")
            if ident in seen:
                continue
            seen.add(ident)
            threads.append({
                "role": "department", "team_id": team.get("id"),
                "agent_id": agent.get("id"), "thread_id": ident,
                "host": agent.get("host", "local"),
                "project_agent": bool(agent.get("project_id")),
            })
    return {
        "schema_version": 1,
        "state": "source_inventoried",
        "ready_for_cutover": False,
        "source_root": str(root),
        "database_bytes": database.stat().st_size,
        "database_wal_exists": (root / "office.db-wal").exists(),
        "model_settings": {key: settings.get(key) for key in
                           ("model_provider", "api_type", "gemma_model", "vision_model")},
        "threads": threads,
        "pending": [
            "User-owned free hosting account and exact free resource eligibility",
            "Official target account login; do not copy auth caches",
            "Original native thread history and selected model on the target",
            "PC-independent mobile/home input and department execution adapter",
            "Referenced files, backup capacity and target memory fit",
            "Actual company-PC-off round trip from the home PC",
        ],
        "notice": "Metadata only; no database, message history or credentials exported",
    }


class ReadOnlyAppServer:
    """Own one short-lived CLI child; an RPC allowlist prevents inference."""

    def __init__(self, executable: str, cwd: Path):
        self.events: queue.Queue = queue.Queue(maxsize=128)
        self.counter = 0
        self.process = subprocess.Popen(
            [executable, "app-server", "--listen", "stdio://"],
            cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            while True:
                line = self.process.stdout.readline(_MAX_LINE + 1)
                if not line or len(line) > _MAX_LINE:
                    break
                packet = json.loads(line)
                self.events.put(packet, timeout=5)
        except (OSError, ValueError, queue.Full):
            pass
        finally:
            try:
                self.events.put(None, timeout=1)
            except queue.Full:
                pass

    def _send(self, value):
        self.process.stdin.write((json.dumps(value) + "\n").encode("utf-8"))
        self.process.stdin.flush()

    def call(self, method, params, timeout=20):
        if method not in _READ_METHODS:
            raise ValueError("This preflight never starts or changes a model turn")
        self.counter += 1
        ident = self.counter
        self._send({"id": ident, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                packet = self.events.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                break
            if packet is None:
                raise ValueError("The read-only app-server connection ended")
            if packet.get("id") == ident and "method" not in packet:
                if "error" in packet:
                    # Do not put the server's arbitrary payload or credentials in reports.
                    raise ValueError("Read-only RPC failed: " + method)
                return packet.get("result") or {}
            if "method" in packet and "id" in packet:
                self._send({"id": packet["id"], "error": {
                    "code": -32601, "message": "Read-only preflight cannot execute tools"}})
        raise ValueError("Read-only RPC timed out: " + method)

    def initialize(self):
        self.call("initialize", {"clientInfo": {
            "name": "office-read-only-preflight", "version": "1.0"}})
        self._send({"method": "initialized", "params": {}})

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()  # Only the child this preflight created.
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        for stream in (self.process.stdin, self.process.stdout):
            if stream:
                stream.close()


def check_target(manifest: dict, cwd: Path, executable: str) -> dict:
    """Run on the chosen host after official login and authorized history handoff."""
    rows = manifest.get("threads")
    if not isinstance(rows, list) or not rows or len(rows) > 100:
        raise ValueError("Supply the original inventory's bounded thread list")
    for row in rows:
        if not isinstance(row, dict) or not _UUID.fullmatch(str(row.get("thread_id", ""))):
            raise ValueError("Invalid original thread identity")
    server = ReadOnlyAppServer(executable, cwd.resolve(strict=True))
    checks = []
    try:
        server.initialize()
        account = server.call("account/read", {"refreshToken": False}).get("account")
        if not isinstance(account, dict) or account.get("type") != "chatgpt":
            raise ValueError("Official ChatGPT subscription login is required; API-key access is not accepted")
        for row in rows:
            ident = row["thread_id"]
            try:
                saved = server.call("thread/read", {"threadId": ident, "includeTurns": False})
                thread = saved.get("thread") or {}
                if thread.get("id") != ident:
                    raise ValueError("The original native thread was not found")
                page = server.call("thread/turns/list", {
                    "threadId": ident, "limit": 1, "itemsView": "notLoaded",
                    "sortDirection": "desc"})
                checks.append({"thread_id": ident, "identity_found": True,
                               "has_persisted_turn": bool(page.get("data"))})
            except ValueError:
                checks.append({"thread_id": ident, "identity_found": False,
                               "has_persisted_turn": False})
    finally:
        server.close()
    all_found = all(row["identity_found"] and row["has_persisted_turn"] for row in checks)
    return {"state": "target_history_readable" if all_found else "blocked_missing_history",
            "ready_for_cutover": False, "thread_checks": checks,
            "model_turn_started": False,
            "notice": "Identity/history presence is not semantic continuity or UI verification"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    source = commands.add_parser("inventory")
    source.add_argument("--root", type=Path, required=True)
    source.add_argument("--output", type=Path, required=True)
    target = commands.add_parser("target")
    target.add_argument("--manifest", type=Path, required=True)
    target.add_argument("--cwd", type=Path, required=True)
    target.add_argument("--codex", default="codex")
    target.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "inventory":
            result = inventory(args.root)
        else:
            executable = shutil.which(args.codex)
            if not executable:
                raise ValueError("The approved Codex CLI is not installed on this host")
            result = check_target(_json_file(args.manifest), args.cwd, executable)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Never overwrite an existing report or any product data path.
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
        print(json.dumps({"state": result["state"], "report": str(args.output.resolve()),
                          "ready_for_cutover": False}))
        return 0 if not result["state"].startswith("blocked") else 2
    except (OSError, ValueError, sqlite3.Error) as error:
        print(json.dumps({"state": "blocked", "error": str(error),
                          "model_turn_started": False, "ready_for_cutover": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
