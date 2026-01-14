from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional

from character.character_engine import (
    CharacterBlueprint,
    CharacterEngine,
    CharacterRecord,
    MountPoint,
)
from character.character_prompt import CharacterPromptBuilder
from llm_api.llm_client import LLMClient

ADD_TAG = "<|ADD_CHARACTER|>"
UPDATE_TAG = "<|UPDATE_CHARACTER|>"
DEFAULT_LOG_PATH = Path("log") / "character_agent.log"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(filename)s:%(lineno)d %(message)s"


def _truncate_text(text: str, limit: int = 800) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...<truncated {len(text) - limit} chars>"


def _get_logger() -> logging.Logger:
    logger = logging.getLogger("character_agent")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    DEFAULT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(DEFAULT_LOG_PATH, encoding="utf-8")
    formatter = logging.Formatter(LOG_FORMAT)
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


@dataclass(frozen=True)
class CharacterActionDecision:
    flag: str
    identifier: str
    raw: str


class CharacterAgent:
    def __init__(
        self, engine: CharacterEngine, llm_client: Optional[LLMClient] = None
    ) -> None:
        self.engine = engine
        self.logger = _get_logger()
        try:
            self.llm_client = llm_client or engine.llm_client or LLMClient()
        except Exception:
            self.logger.exception(
                "init character_agent llm_client failed records=%s",
                len(engine.records),
            )
            raise
        self.logger.info("init character_agent records=%s", len(self.engine.records))

    def extract_info(self, query: str) -> str:
        try:
            if not self.engine.records:
                return "无相关信息"
            prompt = self._build_extract_prompt(query)
            response = self._chat_once(
                prompt, system_prompt=self._system_prompt(), log_label="CHARACTER_EXTRACT"
            )
            identifier = self._parse_query_identifier(response)
            if not identifier:
                self.logger.info("extract_info miss query_len=%s", len(query))
                return "无相关信息"
            record = self._find_record(identifier)
            if not record:
                self.logger.info("extract_info missing id=%s", identifier)
                return "无相关信息"
            formatted = self._format_profile(record.profile)
            if not formatted.strip():
                self.logger.info("extract_info empty id=%s", identifier)
                return "无相关信息"
            self.logger.info(
                "extract_info hit id=%s profile_len=%s",
                identifier,
                len(formatted),
            )
            return formatted
        except Exception:
            self.logger.exception("extract_info failed query_len=%s", len(query))
            raise

    def decide_action(self, update_info: str) -> CharacterActionDecision:
        response = ""
        try:
            prompt = self._build_decision_prompt(update_info)
            response = self._chat_once(
                prompt, system_prompt=self._system_prompt(), log_label="CHARACTER_DECIDE"
            )
            flag, identifier = self._parse_decision(response)
            normalized = self._normalize_flag(flag)
            if normalized == UPDATE_TAG:
                if not self._find_record(identifier):
                    raise ValueError(f"Character {identifier} not found for decision")
            elif normalized == ADD_TAG:
                identifier = self._ensure_new_identifier(identifier)
            else:
                raise ValueError(f"Unknown action flag: {flag}")
            self.logger.info(
                "decide_action flag=%s id=%s info_len=%s",
                normalized,
                identifier,
                len(update_info),
            )
            return CharacterActionDecision(
                flag=normalized, identifier=identifier, raw=response
            )
        except Exception:
            self.logger.exception(
                "decide_action failed info_len=%s response=%s",
                len(update_info),
                _truncate_text(response),
            )
            raise

    def apply_update(
        self, flag: str, identifier: str, update_info: str
    ) -> CharacterRecord:
        try:
            normalized = self._normalize_flag(flag)
            if normalized == ADD_TAG:
                record = self._apply_add(identifier, update_info)
                self.logger.info("apply_update add id=%s", record.identifier)
                return record
            if normalized == UPDATE_TAG:
                record = self._apply_update(identifier, update_info)
                self.logger.info("apply_update update id=%s", record.identifier)
                return record
            raise ValueError(f"Unknown flag: {flag}")
        except Exception:
            self.logger.exception(
                "apply_update failed flag=%s id=%s info_len=%s",
                flag,
                identifier,
                len(update_info),
            )
            raise

    def create_character(
        self, update_info: str, identifier: str = ""
    ) -> CharacterRecord:
        return self.apply_update(ADD_TAG, identifier, update_info)

    # Prompt builders -----------------------------------------------------
    def _build_extract_prompt(self, query: str) -> str:
        lines = [
            "【任务】选择查询角色",
            "从下列角色ID中选择最相关的一项。",
            "只输出角色ID，不要输出其他内容。",
            "如果没有相关信息，只输出：无相关信息。",
            f"查询：{query.strip()}",
            "可用角色：",
        ]
        for record in self._iter_records():
            lines.append(self._summarize_character(record))
        return "\n".join(lines)

    def _build_decision_prompt(self, update_info: str) -> str:
        lines = [
            "【任务】判断更新操作",
            "你需要决定是新增角色还是修改角色。",
            "输出必须包含两处冗余，且只输出两行：",
            f"1) {ADD_TAG}:ID 或 {UPDATE_TAG}:ID",
            '2) {"action":"ADD_CHARACTER"|"UPDATE_CHARACTER","id":"ID"}',
            "UPDATE 时 ID 必须是已有角色ID；ADD 时请给出新角色ID或留空。",
            f"剧情信息：{update_info.strip()}",
            "可用角色：",
        ]
        for record in self._iter_records():
            lines.append(self._summarize_character(record))
        if not self.engine.records:
            lines.append("- 无")
        return "\n".join(lines)

    def _build_update_prompt(self, record: CharacterRecord, update_info: str) -> str:
        original = self._format_profile(record.profile)
        return "\n".join(
            [
                "【任务】更新角色档案",
                "只输出更新后的角色 JSON，不要解释或 Markdown。",
                "JSON 字段固定为: name, summary, background, motivation, conflict, "
                "abilities, weaknesses, relationships, hooks, faction, profession, species, tier。",
                f"角色ID: {record.identifier}",
                f"已有档案: {original}",
                f"剧情信息: {update_info.strip()}",
            ]
        )

    # Core actions --------------------------------------------------------
    def _apply_update(self, identifier: str, update_info: str) -> CharacterRecord:
        record = self._require_record(identifier)
        prompt = self._build_update_prompt(record, update_info)
        response = self._chat_once(
            prompt, system_prompt=self._system_prompt(), log_label="CHARACTER_UPDATE"
        )
        profile = self._parse_profile(response)
        record.profile = profile
        return record

    def _apply_add(self, identifier: str, update_info: str) -> CharacterRecord:
        new_id = self._ensure_new_identifier(identifier)
        mount_point = self._match_mount_point(update_info)
        region_id = mount_point.region_id if mount_point else None
        polity_id = mount_point.polity_id if mount_point else None
        blueprint = CharacterBlueprint(
            identifier=new_id, region_id=region_id, polity_id=polity_id
        )
        prompt = CharacterPromptBuilder.build_prompt(
            self._build_world_outline(),
            blueprint,
            mount_point=mount_point,
            character_pitch=update_info,
        )
        response = self._chat_once(
            prompt, system_prompt=self._system_prompt(), log_label="CHARACTER_ADD"
        )
        profile = self._parse_profile(response)
        record = CharacterRecord(
            identifier=new_id,
            region_id=region_id,
            polity_id=polity_id,
            profile=profile,
        )
        self.engine.records.append(record)
        return record

    # Helpers -------------------------------------------------------------
    def _iter_records(self) -> Iterable[CharacterRecord]:
        return sorted(self.engine.records, key=lambda item: item.identifier)

    def _find_record(self, identifier: str) -> Optional[CharacterRecord]:
        for record in self.engine.records:
            if record.identifier == identifier:
                return record
        return None

    def _require_record(self, identifier: str) -> CharacterRecord:
        record = self._find_record(identifier)
        if not record:
            raise ValueError(f"Character {identifier} not found")
        return record

    def _normalize_flag(self, flag: str) -> str:
        candidate = flag.strip()
        if candidate in {ADD_TAG, "ADD_CHARACTER"}:
            return ADD_TAG
        if candidate in {UPDATE_TAG, "UPDATE_CHARACTER"}:
            return UPDATE_TAG
        return candidate

    def _parse_decision(self, response: str) -> tuple[str, str]:
        tag_match = re.search(
            r"<\|(ADD_CHARACTER|UPDATE_CHARACTER)\|>\s*[:：]\s*([^\s]+)",
            response,
        )
        if tag_match:
            flag = f"<|{tag_match.group(1)}|>"
            return flag, tag_match.group(2).strip()

        for match in re.finditer(r"\{.*?\}", response, flags=re.DOTALL):
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                continue
            action = str(data.get("action", "")).strip().upper()
            identifier = str(data.get("id", "")).strip()
            if action in {"ADD_CHARACTER", "UPDATE_CHARACTER"}:
                return f"<|{action}|>", identifier

        raise ValueError(f"Unable to parse decision from response: {response}")

    def _parse_query_identifier(self, response: str) -> Optional[str]:
        cleaned = response.strip().strip("\"'")
        if cleaned in {"无相关信息", "无"}:
            return ""
        identifiers = [record.identifier for record in self._iter_records()]
        if cleaned in identifiers:
            return cleaned
        for identifier in identifiers:
            if identifier and identifier in cleaned:
                return identifier
        return None

    def _format_profile(self, profile: Dict[str, object] | str) -> str:
        if isinstance(profile, dict):
            return json.dumps(profile, ensure_ascii=False, separators=(",", ":"))
        return str(profile or "")

    def _parse_profile(self, output: str) -> Dict[str, object] | str:
        cleaned = output.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`").strip()
        if "{" in cleaned and "}" in cleaned:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start >= 0 and end > start:
                fragment = cleaned[start : end + 1]
                try:
                    return json.loads(fragment)
                except json.JSONDecodeError:
                    return output.strip()
        return output.strip()

    def _ensure_new_identifier(self, identifier: str) -> str:
        candidate = identifier.strip() if identifier else ""
        if not candidate or self._find_record(candidate):
            candidate = self._next_identifier()
        return candidate

    def _next_identifier(self) -> str:
        existing = {record.identifier for record in self.engine.records}
        numbers = []
        for item in existing:
            match = re.match(r"c(\d+)$", item)
            if match:
                numbers.append(int(match.group(1)))
        if numbers:
            return f"c{max(numbers) + 1}"
        counter = 1
        while f"c{counter}" in existing:
            counter += 1
        return f"c{counter}"

    def _build_world_outline(self) -> str:
        snapshot = self.engine.world_snapshot
        if not snapshot:
            return "未提供世界快照。"

        lines: list[str] = []
        world_node = snapshot.get("world", {})
        world_value = str(world_node.get("value", "")).strip()
        if world_value:
            lines.append(f"世界初始设定：{world_value}")

        macro_node = snapshot.get("macro", {})
        macro_children = macro_node.get("children", []) if macro_node else []
        for child_id in macro_children:
            child = snapshot.get(child_id, {})
            title = str(child.get("key", child.get("title", ""))).strip()
            value = str(child.get("value", "")).strip()
            if not (title or value):
                continue
            if value:
                lines.append(f"- {child_id} {title}: {value}")
            else:
                lines.append(f"- {child_id} {title}")

        if not lines:
            return "世界纲要缺失。"
        return "\n".join(lines)

    def _match_mount_point(self, update_info: str) -> Optional[MountPoint]:
        info = update_info.strip()
        if not info:
            return None
        mount_points = self.engine.extract_mount_points()
        if not mount_points:
            return None
        for mount in mount_points:
            if mount.polity_key and mount.polity_key in info:
                return mount
        for mount in mount_points:
            if mount.region_key and mount.region_key in info:
                return mount
        return None

    def _summarize_character(self, record: CharacterRecord) -> str:
        name = ""
        summary = ""
        faction = ""
        profession = ""
        if isinstance(record.profile, dict):
            name = str(record.profile.get("name", "")).strip()
            summary = str(record.profile.get("summary", "")).strip()
            faction = str(record.profile.get("faction", "")).strip()
            profession = str(record.profile.get("profession", "")).strip()

        parts = [record.identifier]
        if name:
            parts.append(name)
        labels = []
        if faction:
            labels.append(f"阵营:{faction}")
        if profession:
            labels.append(f"职业:{profession}")
        if summary:
            labels.append(f"简述:{summary}")
        label_text = " | ".join(labels)
        return f"- {' '.join(parts)} | {label_text}" if label_text else f"- {' '.join(parts)}"

    def _system_prompt(self) -> str:
        return (
            "You are a precise character assistant. "
            "Follow formatting instructions exactly and avoid extra commentary."
        )

    def _chat_once(
        self, prompt: str, system_prompt: str, log_label: Optional[str] = None
    ) -> str:
        label = log_label or ""
        self.logger.info("LLM_INPUT label=%s system=%s", label, system_prompt)
        self.logger.info("LLM_INPUT label=%s prompt=%s", label, prompt)
        try:
            output = self.llm_client.chat_once(
                prompt,
                system_prompt=system_prompt,
                log_label=log_label,
            )
        except Exception:
            self.logger.exception(
                "LLM call failed label=%s prompt_len=%s", label, len(prompt)
            )
            raise
        if output.startswith("Error in chat_"):
            self.logger.error("LLM error output label=%s output=%s", label, output)
        self.logger.info("LLM_OUTPUT label=%s output=%s", label, output)
        return output
