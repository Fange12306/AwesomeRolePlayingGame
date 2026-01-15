from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from character.character_agent import CharacterAgent
from character.character_engine import CharacterEngine, CharacterRecord, CharacterRequest
from game.game_agent import GameAgent
from llm_api.llm_client import LLMClient
from world.world_agent import WorldAgent
from world.world_engine import WorldEngine

BASE_DIR = Path(__file__).resolve().parent
WEB_ROOT = BASE_DIR / "web"
SAVE_ROOT = BASE_DIR / "save"
WORLD_SPEC = BASE_DIR / "world" / "world_spec.md"
DEFAULT_LOG_PATH = Path("log") / "web_server.log"


def _get_logger() -> logging.Logger:
    logger = logging.getLogger("web_server")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    DEFAULT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(DEFAULT_LOG_PATH, encoding="utf-8")
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(filename)s:%(lineno)d %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def _truncate_text(text: str, limit: int = 2000) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...<truncated {len(text) - limit} chars>"


LOGGER = _get_logger()


def _sanitize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    sanitized: Dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, str):
            sanitized[key] = _truncate_text(value)
        else:
            sanitized[key] = value
    return sanitized


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
            "key": node.get("key", node.get("title", identifier)),
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


def _list_world_snapshots() -> list[Dict[str, Any]]:
    snapshots: list[Dict[str, Any]] = []
    folders = [SAVE_ROOT, SAVE_ROOT / "world"]
    for folder in folders:
        if not folder.exists():
            continue
        for path in folder.glob("*.json"):
            try:
                rel_path = path.relative_to(SAVE_ROOT)
            except ValueError:
                rel_path = path.name
            snapshots.append(
                {
                    "name": path.name,
                    "path": str(rel_path),
                    "full_path": str(path),
                    "mtime": path.stat().st_mtime,
                }
            )
    snapshots.sort(key=lambda item: item.get("mtime", 0), reverse=True)
    return snapshots


def _list_character_snapshots() -> list[Dict[str, Any]]:
    snapshots: list[Dict[str, Any]] = []
    folders = [SAVE_ROOT / "characters", SAVE_ROOT]
    for folder in folders:
        if not folder.exists():
            continue
        for path in folder.glob("*.json"):
            if folder == SAVE_ROOT and not path.name.startswith("characters_"):
                continue
            try:
                rel_path = path.relative_to(SAVE_ROOT)
            except ValueError:
                rel_path = path.name
            snapshots.append(
                {
                    "name": path.name,
                    "path": str(rel_path),
                    "full_path": str(path),
                    "mtime": path.stat().st_mtime,
                }
            )
    snapshots.sort(key=lambda item: item.get("mtime", 0), reverse=True)
    return snapshots


def _resolve_snapshot_item_path(item: Dict[str, Any]) -> Optional[Path]:
    candidate = item.get("full_path") or item.get("path")
    if not candidate:
        return None
    path = Path(candidate)
    if not path.is_absolute():
        path = SAVE_ROOT / path
    return path if path.exists() else None


def _format_save_path(path: Optional[Path]) -> str:
    if not path:
        return ""
    try:
        return str(path.relative_to(SAVE_ROOT))
    except ValueError:
        return str(path)


def _mark_world_updated(save_path: Optional[Path]) -> None:
    STATE.world_revision += 1
    STATE.world_updated_at = time.time()
    if save_path:
        STATE.current_save = save_path


def _mark_character_updated(save_path: Optional[Path]) -> None:
    STATE.character_revision += 1
    STATE.character_updated_at = time.time()
    if save_path:
        STATE.last_character_save = save_path


def _load_world_snapshot() -> tuple[Optional[Dict[str, Dict[str, Any]]], Optional[Path]]:
    with STATE.lock:
        snapshot = STATE.snapshot
        save_path = STATE.current_save
    if snapshot:
        return snapshot, save_path

    latest = next(
        (item for item in _list_world_snapshots() if _resolve_snapshot_item_path(item)),
        None,
    )
    if not latest:
        return None, None
    path = _resolve_snapshot_item_path(latest)
    if not path:
        return None, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        LOGGER.exception("load_world_snapshot failed path=%s", path)
        return None, path
    return payload, path


def _load_character_snapshot(
    snapshot_path: Optional[Path],
) -> tuple[list[CharacterRecord], Dict[str, Any]]:
    if not snapshot_path or not snapshot_path.exists():
        return [], {}
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    records: list[CharacterRecord] = []
    for item in payload.get("characters", []):
        identifier = str(item.get("id", "")).strip()
        if not identifier:
            continue
        records.append(
            CharacterRecord(
                identifier=identifier,
                region_id=item.get("region_id"),
                polity_id=item.get("polity_id"),
                profile=item.get("profile", {}),
            )
        )
    return records, payload


def _normalize_action_name(flag: str) -> str:
    cleaned = flag.strip()
    if "ADD_NODE" in cleaned:
        return "ADD_NODE"
    if "UPDATE_NODE" in cleaned:
        return "UPDATE_NODE"
    if "ADD_CHARACTER" in cleaned:
        return "ADD_CHARACTER"
    if "UPDATE_CHARACTER" in cleaned:
        return "UPDATE_CHARACTER"
    return cleaned.strip("<|>").strip()


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in {"1", "true", "yes", "y"}:
            return True
        if cleaned in {"0", "false", "no", "n"}:
            return False
    return default


def _resolve_snapshot_path(snapshot_path: str) -> Optional[Path]:
    if not snapshot_path:
        return None
    raw = Path(snapshot_path)
    if raw.is_absolute():
        candidate = raw.resolve()
    else:
        candidate = (SAVE_ROOT / raw).resolve()
    try:
        candidate.relative_to(SAVE_ROOT.resolve())
    except ValueError:
        return None
    return candidate


@dataclass
class GenerationJob:
    job_id: str
    total: int
    completed: int = 0
    status: str = "running"
    message: str = ""
    save_path: Optional[str] = None
    kind: str = "world"
    phase: str = ""
    macro_total: int = 0
    micro_total: int = 0
    stage_completed: int = 0
    stage_total: int = 0
    ready: bool = False


class AppState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.snapshot: Optional[Dict[str, Dict[str, Any]]] = None
        self.current_save: Optional[Path] = None
        self.jobs: Dict[str, GenerationJob] = {}
        self.world_job_id: Optional[str] = None
        self.world_revision: int = 0
        self.character_revision: int = 0
        self.world_updated_at: float = 0.0
        self.character_updated_at: float = 0.0
        self.last_character_save: Optional[Path] = None


STATE = AppState()


class RequestHandler(SimpleHTTPRequestHandler):
    logger = LOGGER

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)
        self.logger = LOGGER
        self._request_payload: Optional[Dict[str, Any]] = None
        self._request_raw: str = ""
        self._request_error_detail: str = ""

    def log_message(self, format: str, *args) -> None:
        return

    def _reset_request_context(self) -> None:
        self._request_payload = None
        self._request_raw = ""
        self._request_error_detail = ""

    def _build_request_context(self) -> Dict[str, Any]:
        parsed = urlparse(self.path)
        context = {
            "method": self.command,
            "path": parsed.path,
            "query": parsed.query,
            "client": self.client_address[0] if self.client_address else "",
            "headers": {
                "Content-Type": self.headers.get("Content-Type", ""),
                "User-Agent": self.headers.get("User-Agent", ""),
            },
        }
        if self._request_error_detail:
            context["error_detail"] = self._request_error_detail
        if self._request_payload is not None:
            context["request_payload"] = _sanitize_payload(self._request_payload)
        elif self._request_raw:
            context["request_raw"] = _truncate_text(self._request_raw)
        return context

    def _log_api_error(self, payload: Dict[str, Any], status: int) -> None:
        context = self._build_request_context()
        context["response_status"] = status
        context["response"] = payload
        detail = json.dumps(context, ensure_ascii=False)
        level = logging.ERROR if status >= 500 else logging.WARNING
        self.logger.log(level, "api_error %s", detail)

    def do_GET(self) -> None:
        self._reset_request_context()
        try:
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/"):
                self._handle_api_get(parsed)
                return
            if parsed.path == "/":
                self.path = "/index.html"
            super().do_GET()
        except Exception:
            self.logger.exception("unhandled GET error path=%s", self.path)
            if self.path.startswith("/api/"):
                try:
                    self._send_json(
                        {"ok": False, "error": "internal_server_error"}, status=500
                    )
                except Exception:
                    return
            else:
                try:
                    self.send_error(500, "Internal server error")
                except Exception:
                    return

    def do_POST(self) -> None:
        self._reset_request_context()
        try:
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/"):
                self._handle_api_post(parsed)
                return
            self.send_error(404, "Not found")
        except Exception:
            self.logger.exception("unhandled POST error path=%s", self.path)
            if self.path.startswith("/api/"):
                try:
                    self._send_json(
                        {"ok": False, "error": "internal_server_error"}, status=500
                    )
                except Exception:
                    return
            else:
                try:
                    self.send_error(500, "Internal server error")
                except Exception:
                    return

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
        if parsed.path == "/api/world/status":
            with STATE.lock:
                job_id = STATE.world_job_id
                job = STATE.jobs.get(job_id) if job_id else None
            if not job:
                self._send_json({"ok": True, "status": "idle"})
                return
            payload = {
                "ok": True,
                "status": job.status,
                "message": job.message,
                "save_path": job.save_path,
                "phase": job.phase,
                "macro_total": job.macro_total,
                "micro_total": job.micro_total,
                "stage_completed": job.stage_completed,
                "stage_total": job.stage_total,
                "ready": job.ready,
            }
            self._send_json(payload)
            return
        if parsed.path == "/api/updates":
            with STATE.lock:
                payload = {
                    "ok": True,
                    "world_revision": STATE.world_revision,
                    "character_revision": STATE.character_revision,
                    "world_updated_at": STATE.world_updated_at,
                    "character_updated_at": STATE.character_updated_at,
                    "world_save_path": _format_save_path(STATE.current_save),
                    "character_save_path": _format_save_path(STATE.last_character_save),
                }
            self._send_json(payload)
            return
        if parsed.path == "/api/world/snapshots":
            snapshots = _list_world_snapshots()
            self._send_json({"ok": True, "snapshots": snapshots})
            return
        if parsed.path == "/api/characters/snapshots":
            snapshots = _list_character_snapshots()
            self._send_json({"ok": True, "snapshots": snapshots})
            return
        if parsed.path == "/api/characters":
            query = parse_qs(parsed.query)
            snapshot_path = (query.get("path") or [""])[0]
            if not snapshot_path:
                self._send_json({"ok": False, "error": "missing_path"}, status=400)
                return
            resolved = _resolve_snapshot_path(snapshot_path)
            if not resolved or not resolved.exists():
                self._send_json({"ok": False, "error": "snapshot_not_found"}, status=404)
                return
            try:
                payload = json.loads(resolved.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                self._send_json(
                    {"ok": False, "error": f"invalid_snapshot: {exc}"},
                    status=400,
                )
                return
            self._send_json(
                {
                    "ok": True,
                    "snapshot": payload,
                    "path": str(resolved.relative_to(SAVE_ROOT)),
                }
            )
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
            payload = {
                "ok": True,
                "status": job.status,
                "total": job.total,
                "completed": job.completed,
                "message": job.message,
                "save_path": job.save_path,
                "kind": job.kind,
                "phase": job.phase,
                "macro_total": job.macro_total,
                "micro_total": job.micro_total,
                "stage_completed": job.stage_completed,
                "stage_total": job.stage_total,
                "ready": job.ready,
            }
            self._send_json(payload)
            return

        self.send_error(404, "Not found")

    def _handle_api_post(self, parsed) -> None:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        self._request_raw = raw.decode("utf-8", errors="replace") if raw else ""
        try:
            payload = json.loads(self._request_raw) if raw else {}
        except json.JSONDecodeError as exc:
            self._request_error_detail = f"json_decode_error: {exc}"
            self._send_json({"ok": False, "error": "invalid_json"}, status=400)
            return
        self._request_payload = payload

        if parsed.path == "/api/generate":
            self._handle_generate(payload)
            return
        if parsed.path == "/api/import":
            self._handle_import(payload)
            return
        if parsed.path == "/api/update":
            self._handle_update(payload)
            return
        if parsed.path == "/api/characters/generate":
            self._handle_character_generate(payload)
            return
        if parsed.path == "/api/game/plan":
            self._handle_game_plan(payload)
            return

        self.send_error(404, "Not found")

    def _handle_generate(self, payload: Dict[str, Any]) -> None:
        prompt = str(payload.get("prompt", "")).strip()
        scale = str(payload.get("scale", "")).strip().lower()
        if not prompt:
            self._send_json({"ok": False, "error": "missing_prompt"}, status=400)
            return

        job_id = uuid.uuid4().hex
        job = GenerationJob(job_id=job_id, total=0, kind="world", phase="macro")
        with STATE.lock:
            STATE.jobs[job_id] = job
            STATE.world_job_id = job_id

        def worker() -> None:
            save_path = SAVE_ROOT / f"world_{_timestamp()}.json"
            stage_one_saved = False

            def finalize_stage_one() -> None:
                nonlocal stage_one_saved
                if stage_one_saved:
                    return
                stage_one_saved = True
                snapshot = engine.as_dict()
                micro_total = len(engine._iter_micro_nodes())
                _write_snapshot(snapshot, save_path)
                with STATE.lock:
                    job.ready = True
                    job.phase = "micro"
                    job.micro_total = micro_total
                    job.stage_total = micro_total
                    job.stage_completed = 0
                    job.message = "第一阶段完成，生成细节中..."
                    job.save_path = str(save_path)
                    STATE.snapshot = snapshot
                    STATE.current_save = save_path
                    _mark_world_updated(save_path)

            def progress_cb(node, completed: int, total: int) -> None:
                with STATE.lock:
                    job.completed = completed
                    job.total = total
                    if job.phase == "micro":
                        job.stage_completed = max(0, completed - job.macro_total)
                    else:
                        job.stage_total = job.macro_total or total
                        job.stage_completed = completed

            try:
                engine = WorldEngine(
                    world_spec_path=str(WORLD_SPEC),
                    user_pitch=prompt,
                    micro_scale=scale,
                    auto_generate=False,
                )
                macro_total = len(engine._iter_macro_nodes())
                with STATE.lock:
                    job.macro_total = macro_total
                    job.stage_total = macro_total
                original_generate_micro_structure = engine._generate_micro_structure

                def wrapped_generate_micro_structure(*args, **kwargs):
                    original_generate_micro_structure(*args, **kwargs)
                    finalize_stage_one()

                engine._generate_micro_structure = wrapped_generate_micro_structure
                engine.generate_world(
                    prompt,
                    progress_callback=progress_cb,
                )
                snapshot = engine.as_dict()
                _write_snapshot(snapshot, save_path)
                with STATE.lock:
                    job.status = "done"
                    job.completed = job.total
                    job.save_path = str(save_path)
                    job.phase = "done"
                    job.ready = True
                    job.message = "生成完成"
                    STATE.snapshot = snapshot
                    STATE.current_save = save_path
                    _mark_world_updated(save_path)
            except Exception as exc:
                self.logger.exception(
                    "generate_world failed job_id=%s prompt_len=%s save_path=%s",
                    job_id,
                    len(prompt),
                    save_path,
                )
                with STATE.lock:
                    job.status = "error"
                    job.message = str(exc)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        self._send_json({"ok": True, "job_id": job_id, "total": job.total})

    def _handle_character_generate(self, payload: Dict[str, Any]) -> None:
        snapshot_raw = str(payload.get("snapshot", "")).strip()
        total_raw = payload.get("total")
        pitch = str(payload.get("pitch", "")).strip()
        if not snapshot_raw:
            self._send_json({"ok": False, "error": "missing_snapshot"}, status=400)
            return
        try:
            total = int(total_raw)
        except (TypeError, ValueError):
            self._send_json({"ok": False, "error": "invalid_total"}, status=400)
            return
        if total <= 0:
            self._send_json({"ok": False, "error": "invalid_total"}, status=400)
            return

        snapshot_path = _resolve_snapshot_path(snapshot_raw)
        if not snapshot_path or not snapshot_path.exists():
            self._send_json({"ok": False, "error": "snapshot_not_found"}, status=404)
            return

        job_id = uuid.uuid4().hex
        job = GenerationJob(
            job_id=job_id,
            total=total + 2,
            message="准备生成角色",
            kind="character",
        )
        with STATE.lock:
            STATE.jobs[job_id] = job

        def progress_cb(completed: int, total_chars: int) -> None:
            with STATE.lock:
                job.completed = completed
                job.total = total_chars + 2
                job.message = f"角色生成 {completed}/{total_chars}"

        def worker() -> None:
            try:
                engine = CharacterEngine.from_world_snapshot(snapshot_path)
                request = CharacterRequest(total=total, pitch=pitch)
                records = engine.generate_characters(
                    request, progress_callback=progress_cb
                )
                with STATE.lock:
                    job.completed = max(job.completed, total)
                    job.message = "角色生成完成，生成关系..."

                relations = engine.generate_relations(records)
                with STATE.lock:
                    job.completed = total + 1
                    job.message = "角色关系生成完成，生成地点关系..."

                location_edges = engine.generate_location_edges(records)
                save_path = SAVE_ROOT / "characters" / f"characters_{_timestamp()}.json"
                engine.save_snapshot(save_path, records)
                with STATE.lock:
                    job.status = "done"
                    job.completed = total + 2
                    job.save_path = str(save_path)
                    job.message = (
                        f"完成：角色 {len(records)} / 关系 {len(relations)} "
                        f"/ 地点关系 {len(location_edges)}"
                    )
                    _mark_character_updated(save_path)
            except Exception as exc:
                self.logger.exception(
                    "generate_characters failed job_id=%s total=%s snapshot_path=%s",
                    job_id,
                    total,
                    snapshot_path,
                )
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
        raw_name = Path(filename).name
        safe_name = _sanitize_filename(raw_name)
        if not safe_name.lower().endswith(".json"):
            safe_name = f"{safe_name}.json"
        candidate_paths = [SAVE_ROOT / "world" / safe_name, SAVE_ROOT / safe_name]
        save_path = next((path for path in candidate_paths if path.exists()), candidate_paths[-1])
        with STATE.lock:
            _write_snapshot(snapshot, save_path)
            STATE.snapshot = snapshot
            STATE.current_save = save_path
            _mark_world_updated(save_path)

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
            world_job = (
                STATE.jobs.get(STATE.world_job_id)
                if STATE.world_job_id
                else None
            )
            if (
                world_job
                and world_job.kind == "world"
                and world_job.status == "running"
            ):
                self._send_json(
                    {"ok": False, "error": "world_generation_running"},
                    status=409,
                )
                return
            if not STATE.snapshot:
                self._send_json({"ok": False, "error": "no_snapshot"}, status=404)
                return
            node = STATE.snapshot.get(identifier)
            if not node:
                self._send_json({"ok": False, "error": "node_not_found"}, status=404)
                return
            node["value"] = value
            if not STATE.current_save:
                STATE.current_save = SAVE_ROOT / "world" / f"world_{_timestamp()}.json"
            _write_snapshot(STATE.snapshot, STATE.current_save)
            _mark_world_updated(STATE.current_save)

        self._send_json({"ok": True})

    def _handle_game_plan(self, payload: Dict[str, Any]) -> None:
        text = str(payload.get("text") or payload.get("input") or "").strip()
        if not text:
            self._send_json({"ok": False, "error": "missing_text"}, status=400)
            return
        apply_updates = _coerce_bool(payload.get("apply"), default=True)
        snapshot, snapshot_path = _load_world_snapshot()
        if not snapshot:
            self._send_json({"ok": False, "error": "no_world_snapshot"}, status=404)
            return

        character_snapshot = next(
            (
                item
                for item in _list_character_snapshots()
                if _resolve_snapshot_item_path(item)
            ),
            None,
        )
        character_snapshot_path = (
            _resolve_snapshot_item_path(character_snapshot) if character_snapshot else None
        )

        try:
            llm_client = LLMClient()
            if snapshot_path:
                world_engine = WorldEngine.from_snapshot(
                    snapshot_path, llm_client=llm_client
                )
            else:
                world_engine = WorldEngine(
                    world_spec_path=None, llm_client=llm_client, auto_generate=False
                )
                world_engine.apply_snapshot(snapshot)

            records, character_payload = _load_character_snapshot(character_snapshot_path)
            character_engine = CharacterEngine(
                world_snapshot=snapshot,
                world_snapshot_path=snapshot_path,
                llm_client=llm_client,
            )
            character_engine.records = records
            if character_payload:
                relations = character_payload.get("relations") or []
                location_edges = character_payload.get("character_location_edges") or []
                if isinstance(relations, list):
                    character_engine.relations = list(relations)
                if isinstance(location_edges, list):
                    character_engine.location_edges = list(location_edges)
                payload_world_path = str(
                    character_payload.get("world_snapshot_path", "")
                ).strip()
                if payload_world_path and not snapshot_path:
                    candidate = Path(payload_world_path)
                    if candidate.exists():
                        character_engine.world_snapshot_path = candidate

            world_agent = WorldAgent(world_engine, llm_client=llm_client)
            character_agent = CharacterAgent(character_engine, llm_client=llm_client)
            game_agent = GameAgent(
                world_agent=world_agent,
                character_agent=character_agent,
                llm_client=llm_client,
            )

            decision = game_agent.decide_updates(text)
            actions: list[Dict[str, str]] = []
            applied = {"world": False, "character": False}
            current_snapshot = snapshot
            world_save_path = snapshot_path
            character_save_path = character_snapshot_path
            if decision.update_world:
                if hasattr(world_agent, "collect_actions"):
                    world_decisions = world_agent.collect_actions(text)
                else:
                    world_decisions = world_agent.decide_actions(text)
                world_nodes: list[WorldNode] = []
                if apply_updates:
                    world_nodes = world_agent.apply_updates(world_decisions, text)
                    current_snapshot = world_engine.as_dict()
                    if not world_save_path:
                        world_save_path = SAVE_ROOT / f"world_{_timestamp()}.json"
                    _write_snapshot(current_snapshot, world_save_path)
                    with STATE.lock:
                        STATE.snapshot = current_snapshot
                        STATE.current_save = world_save_path
                        _mark_world_updated(world_save_path)
                    applied["world"] = True
                for idx, world_decision in enumerate(world_decisions):
                    node_label = ""
                    if apply_updates:
                        node = world_nodes[idx] if idx < len(world_nodes) else None
                        node_label = node.key if node else ""
                    else:
                        node = world_engine.view_node(world_decision.index)
                        node_label = node.key
                    actions.append(
                        {
                            "agent": "world",
                            "action": _normalize_action_name(world_decision.flag),
                            "target": world_decision.index,
                            "label": node_label,
                        }
                    )
                if apply_updates:
                    character_engine.set_world_snapshot(current_snapshot)
                    character_engine.world_snapshot_path = world_save_path
            if decision.update_characters:
                if hasattr(character_agent, "collect_actions"):
                    character_decisions = character_agent.collect_actions(text)
                else:
                    character_decisions = character_agent.decide_actions(text)
                label = ""
                if current_snapshot:
                    character_engine.set_world_snapshot(current_snapshot)
                if world_save_path:
                    character_engine.world_snapshot_path = world_save_path
                character_records: list[CharacterRecord] = []
                if apply_updates:
                    character_records = character_agent.apply_updates(
                        character_decisions, text
                    )
                    if character_records:
                        record = character_records[-1]
                        if isinstance(record.profile, dict):
                            label = str(record.profile.get("name", "")).strip()
                    character_save_path = character_save_path or (
                        SAVE_ROOT / "characters" / f"characters_{_timestamp()}.json"
                    )
                    character_engine.save_snapshot(
                        character_save_path, character_engine.records
                    )
                    with STATE.lock:
                        _mark_character_updated(character_save_path)
                    applied["character"] = True
                for idx, character_decision in enumerate(character_decisions):
                    label = ""
                    if apply_updates:
                        record = (
                            character_records[idx]
                            if idx < len(character_records)
                            else None
                        )
                        if record and isinstance(record.profile, dict):
                            label = str(record.profile.get("name", "")).strip()
                    else:
                        for record in character_engine.records:
                            if record.identifier == character_decision.identifier:
                                if isinstance(record.profile, dict):
                                    label = str(record.profile.get("name", "")).strip()
                                break
                    actions.append(
                        {
                            "agent": "character",
                            "action": _normalize_action_name(character_decision.flag),
                            "target": character_decision.identifier,
                            "label": label,
                        }
                    )

            response = {
                "ok": True,
                "decision": {
                    "update_world": decision.update_world,
                    "update_characters": decision.update_characters,
                    "reason": decision.reason,
                },
                "actions": actions,
                "applied": applied,
                "context": {
                    "world_snapshot": _format_save_path(world_save_path) or "当前内存快照",
                    "character_snapshot": _format_save_path(character_save_path),
                    "character_count": len(character_engine.records),
                },
            }
            self._send_json(response)
        except Exception as exc:
            self.logger.exception(
                "game_plan failed text_len=%s world_snapshot=%s",
                len(text),
                snapshot_path,
            )
            self._send_json({"ok": False, "error": str(exc)}, status=500)

    def _send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
        if status >= 400 or (isinstance(payload, dict) and payload.get("ok") is False):
            self._log_api_error(payload, status)
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
