from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from world.world_engine import WorldEngine

BASE_DIR = Path(__file__).resolve().parent
WEB_ROOT = BASE_DIR / "web"
SAVE_ROOT = BASE_DIR / "save"
WORLD_MD = BASE_DIR / "world.md"


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _sanitize_filename(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in name)
    return cleaned.strip("._") or "imported"


def _write_snapshot(snapshot: Dict[str, Dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def _normalize_snapshot(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    snapshot: Dict[str, Dict[str, Any]] = {}
    for identifier, node in payload.items():
        if not isinstance(node, dict):
            continue
        snapshot[identifier] = {
            "title": node.get("title", identifier),
            "description": node.get("description", ""),
            "value": node.get("value", ""),
            "children": node.get("children", []),
        }

    has_children_lists = all(
        isinstance(node.get("children"), list) for node in snapshot.values()
    )
    if not has_children_lists:
        derived: Dict[str, list[str]] = {key: [] for key in snapshot}
        for identifier in snapshot:
            if identifier == "world":
                continue
            if "." in identifier:
                parent = identifier.rsplit(".", 1)[0]
            elif identifier in {"macro", "micro"}:
                parent = "world"
            else:
                parent = "macro"
            if parent in derived:
                derived[parent].append(identifier)
        for identifier, node in snapshot.items():
            node["children"] = sorted(derived.get(identifier, []))
    else:
        for node in snapshot.values():
            node["children"] = sorted(node.get("children", []))

    return snapshot


@dataclass
class GenerationJob:
    job_id: str
    total: int
    completed: int = 0
    status: str = "running"
    message: str = ""
    save_path: Optional[str] = None


class AppState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.snapshot: Optional[Dict[str, Dict[str, Any]]] = None
        self.current_save: Optional[Path] = None
        self.jobs: Dict[str, GenerationJob] = {}


STATE = AppState()


class RequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def log_message(self, format: str, *args) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self._handle_api_get(parsed)
            return
        if parsed.path == "/":
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self._handle_api_post(parsed)
            return
        self.send_error(404, "Not found")

    def _handle_api_get(self, parsed) -> None:
        if parsed.path == "/api/world":
            with STATE.lock:
                snapshot = STATE.snapshot
                save_path = str(STATE.current_save) if STATE.current_save else None
            if not snapshot:
                self._send_json({"ok": False, "error": "no_snapshot"}, status=404)
                return
            self._send_json({"ok": True, "snapshot": snapshot, "save_path": save_path})
            return

        if parsed.path == "/api/progress":
            query = parse_qs(parsed.query)
            job_id = (query.get("id") or [""])[0]
            if not job_id:
                self._send_json({"ok": False, "error": "missing_id"}, status=400)
                return
            with STATE.lock:
                job = STATE.jobs.get(job_id)
            if not job:
                self._send_json({"ok": False, "error": "job_not_found"}, status=404)
                return
            self._send_json(
                {
                    "ok": True,
                    "status": job.status,
                    "total": job.total,
                    "completed": job.completed,
                    "message": job.message,
                    "save_path": job.save_path,
                }
            )
            return

        self.send_error(404, "Not found")

    def _handle_api_post(self, parsed) -> None:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError:
            self._send_json({"ok": False, "error": "invalid_json"}, status=400)
            return

        if parsed.path == "/api/generate":
            self._handle_generate(payload)
            return
        if parsed.path == "/api/import":
            self._handle_import(payload)
            return
        if parsed.path == "/api/update":
            self._handle_update(payload)
            return

        self.send_error(404, "Not found")

    def _handle_generate(self, payload: Dict[str, Any]) -> None:
        prompt = str(payload.get("prompt", "")).strip()
        if not prompt:
            self._send_json({"ok": False, "error": "missing_prompt"}, status=400)
            return

        try:
            engine = WorldEngine(world_md_path=str(WORLD_MD))
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=500)
            return

        nodes = engine._iter_nodes(skip_root=True)
        job_id = uuid.uuid4().hex
        job = GenerationJob(job_id=job_id, total=len(nodes))
        with STATE.lock:
            STATE.jobs[job_id] = job

        def progress_cb(node, completed: int, total: int) -> None:
            with STATE.lock:
                job.completed = completed
                job.total = total

        def worker() -> None:
            try:
                engine.generate_world(prompt, progress_callback=progress_cb)
                save_path = SAVE_ROOT / f"world_{_timestamp()}.json"
                engine.save_snapshot(save_path)
                snapshot = engine.as_dict()
                with STATE.lock:
                    job.status = "done"
                    job.completed = job.total
                    job.save_path = str(save_path)
                    STATE.snapshot = snapshot
                    STATE.current_save = save_path
            except Exception as exc:
                with STATE.lock:
                    job.status = "error"
                    job.message = str(exc)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        self._send_json({"ok": True, "job_id": job_id, "total": job.total})

    def _handle_import(self, payload: Dict[str, Any]) -> None:
        content = payload.get("content")
        filename = str(payload.get("filename", "imported.json"))
        if not content:
            self._send_json({"ok": False, "error": "missing_content"}, status=400)
            return
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            self._send_json({"ok": False, "error": f"invalid_json: {exc}"}, status=400)
            return
        if not isinstance(data, dict):
            self._send_json({"ok": False, "error": "invalid_snapshot"}, status=400)
            return

        snapshot = _normalize_snapshot(data)
        safe_name = _sanitize_filename(Path(filename).stem)
        save_path = SAVE_ROOT / f"{safe_name}_{_timestamp()}.json"
        with STATE.lock:
            _write_snapshot(snapshot, save_path)
            STATE.snapshot = snapshot
            STATE.current_save = save_path

        self._send_json({"ok": True, "save_path": str(save_path)})

    def _handle_update(self, payload: Dict[str, Any]) -> None:
        identifier = str(payload.get("identifier", "")).strip()
        value = payload.get("value")
        if not identifier:
            self._send_json({"ok": False, "error": "missing_identifier"}, status=400)
            return
        if value is None:
            self._send_json({"ok": False, "error": "missing_value"}, status=400)
            return

        with STATE.lock:
            if not STATE.snapshot:
                self._send_json({"ok": False, "error": "no_snapshot"}, status=404)
                return
            node = STATE.snapshot.get(identifier)
            if not node:
                self._send_json({"ok": False, "error": "node_not_found"}, status=404)
                return
            node["value"] = value
            if STATE.current_save:
                _write_snapshot(STATE.snapshot, STATE.current_save)

        self._send_json({"ok": True})

    def _send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def run() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", 6231), RequestHandler)
    print("Web UI running on http://localhost:6231")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
