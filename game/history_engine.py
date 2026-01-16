from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from llm_api.llm_client import LLMClient

DEFAULT_LOG_PATH = Path("log") / "history.jsonl"
DEFAULT_ENGINE_LOG_PATH = Path("log") / "history_engine.log"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(filename)s:%(lineno)d %(message)s"


def _get_logger() -> logging.Logger:
    logger = logging.getLogger("history_engine")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    DEFAULT_ENGINE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(DEFAULT_ENGINE_LOG_PATH, encoding="utf-8")
    formatter = logging.Formatter(LOG_FORMAT)
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


@dataclass
class HistoryChange:
    kind: str
    action: str
    identifier: str
    before: Optional[dict[str, Any]] = None
    after: Optional[dict[str, Any]] = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "action": self.action,
            "identifier": self.identifier,
            "before": self.before,
            "after": self.after,
            "note": self.note,
        }


@dataclass
class HistoryEntry:
    entry_id: str
    created_at: str
    update_info: str
    decision: dict[str, Any]
    world_changes: list[HistoryChange] = field(default_factory=list)
    character_changes: list[HistoryChange] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "created_at": self.created_at,
            "update_info": self.update_info,
            "decision": self.decision,
            "world_changes": [item.to_dict() for item in self.world_changes],
            "character_changes": [item.to_dict() for item in self.character_changes],
            "summary": self.summary,
        }


class HistoryEngine:
    def __init__(
        self,
        log_path: Optional[str | Path] = None,
        save_root: Optional[str | Path] = None,
        llm_client: Optional[LLMClient] = None,
    ) -> None:
        self.logger = _get_logger()
        self.log_path = Path(log_path) if log_path else DEFAULT_LOG_PATH
        self.save_root = Path(save_root) if save_root else None
        self.llm_client = llm_client
        self.entries: list[HistoryEntry] = []
        self.last_save_path: Optional[Path] = None

    def record(
        self,
        update_info: str,
        decision: dict[str, Any],
        world_changes: Iterable[HistoryChange],
        character_changes: Iterable[HistoryChange],
        summary: str = "",
        use_llm_summary: bool = False,
    ) -> HistoryEntry:
        entry = HistoryEntry(
            entry_id=uuid.uuid4().hex,
            created_at=datetime.now().isoformat(timespec="seconds"),
            update_info=update_info,
            decision=decision,
            world_changes=list(world_changes),
            character_changes=list(character_changes),
            summary="",
        )
        entry.summary = summary or self._build_summary(entry, use_llm=use_llm_summary)
        self.entries.append(entry)
        self._write_entry(entry)
        self._write_snapshot(entry)
        self.logger.info(
            "history_record id=%s world=%s characters=%s summary_len=%s",
            entry.entry_id,
            len(entry.world_changes),
            len(entry.character_changes),
            len(entry.summary),
        )
        return entry

    def summarize_recent(self, limit: int = 10, use_llm: bool = False) -> str:
        if self.entries:
            entries = list(self.entries)
        else:
            entries = list(self._load_entries(limit))
        if limit and limit > 0:
            entries = entries[-limit:]
        if not entries:
            return "No history recorded."
        if use_llm and self.llm_client:
            return self._build_llm_summary(entries)
        lines = [self._format_entry_line(entry) for entry in entries]
        return "\n".join(line for line in lines if line)

    def _build_summary(self, entry: HistoryEntry, use_llm: bool = False) -> str:
        if use_llm and self.llm_client:
            return self._build_llm_summary([entry])
        parts = []
        if entry.world_changes:
            parts.append(
                "world: " + ", ".join(self._format_change(item) for item in entry.world_changes)
            )
        if entry.character_changes:
            parts.append(
                "characters: "
                + ", ".join(self._format_change(item) for item in entry.character_changes)
            )
        if not parts:
            return "no changes"
        return "; ".join(parts)

    def _build_llm_summary(self, entries: Iterable[HistoryEntry]) -> str:
        if not self.llm_client:
            return ""
        entries_list = list(entries)
        if not entries_list:
            return ""
        lines = ["Summarize the following change log in 2-4 sentences."]
        for entry in entries_list:
            lines.append(f"Story: {entry.update_info}")
            if entry.world_changes:
                lines.append("World changes:")
                for change in entry.world_changes:
                    lines.append(self._format_change(change))
            if entry.character_changes:
                lines.append("Character changes:")
                for change in entry.character_changes:
                    lines.append(self._format_change(change))
        prompt = "\n".join(lines)
        try:
            return self.llm_client.chat_once(prompt, system_prompt="You summarize change logs.")
        except Exception:
            return self._build_summary(entries_list[0])

    def _format_entry_line(self, entry: HistoryEntry) -> str:
        summary = entry.summary or self._build_summary(entry)
        return f"{entry.created_at} {entry.entry_id}: {summary}"

    def _format_change(self, change: HistoryChange) -> str:
        name = ""
        if change.after and isinstance(change.after, dict):
            name = str(change.after.get("key") or change.after.get("name") or "").strip()
        if not name and change.before and isinstance(change.before, dict):
            name = str(change.before.get("key") or change.before.get("name") or "").strip()
        label = f"{change.identifier}"
        if name:
            label = f"{label}({name})"
        if change.action == "UPDATE_NODE":
            old_key = ""
            new_key = ""
            if change.before:
                old_key = str(change.before.get("key", "")).strip()
            if change.after:
                new_key = str(change.after.get("key", "")).strip()
            if old_key and new_key and old_key != new_key:
                return f"{change.action} {label} {old_key}->{new_key}"
        if change.action == "UPDATE_CHARACTER":
            old_name = ""
            new_name = ""
            if change.before:
                old_name = str(change.before.get("name", "")).strip()
            if change.after:
                new_name = str(change.after.get("name", "")).strip()
            if old_name and new_name and old_name != new_name:
                return f"{change.action} {label} {old_name}->{new_name}"
        return f"{change.action} {label}"

    def _write_entry(self, entry: HistoryEntry) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(entry.to_dict(), ensure_ascii=False)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(payload + "\n")
        except Exception:
            self.logger.exception("history_write_failed entry_id=%s", entry.entry_id)

    def _write_snapshot(self, entry: HistoryEntry) -> None:
        if not self.save_root:
            return
        try:
            self.save_root.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = self.save_root / f"history_{timestamp}_{entry.entry_id}.json"
            payload = json.dumps(
                entry.to_dict(), ensure_ascii=False, separators=(",", ":")
            )
            path.write_text(payload, encoding="utf-8")
            self.last_save_path = path
        except Exception:
            self.logger.exception(
                "history_snapshot_write_failed entry_id=%s", entry.entry_id
            )

    def _parse_change(self, payload: dict[str, Any]) -> HistoryChange:
        return HistoryChange(
            kind=str(payload.get("kind", "")),
            action=str(payload.get("action", "")),
            identifier=str(payload.get("identifier", "")),
            before=payload.get("before"),
            after=payload.get("after"),
            note=str(payload.get("note", "")),
        )

    def _load_entries(self, limit: int) -> Iterable[HistoryEntry]:
        if not self.log_path.exists():
            return []
        entries: list[HistoryEntry] = []
        try:
            with self.log_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    entry = HistoryEntry(
                        entry_id=str(payload.get("entry_id", "")),
                        created_at=str(payload.get("created_at", "")),
                        update_info=str(payload.get("update_info", "")),
                        decision=payload.get("decision", {}) or {},
                        world_changes=[
                            self._parse_change(item)
                            for item in payload.get("world_changes", []) or []
                            if isinstance(item, dict)
                        ],
                        character_changes=[
                            self._parse_change(item)
                            for item in payload.get("character_changes", []) or []
                            if isinstance(item, dict)
                        ],
                        summary=str(payload.get("summary", "")),
                    )
                    entries.append(entry)
                    if limit and len(entries) >= limit:
                        break
        except Exception:
            self.logger.exception("history_load_failed path=%s", self.log_path)
        return entries
