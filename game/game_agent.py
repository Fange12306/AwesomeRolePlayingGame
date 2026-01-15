from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from character.character_agent import CharacterActionDecision, CharacterAgent
from character.character_engine import CharacterRecord
from llm_api.llm_client import LLMClient
from world.world_agent import ActionDecision, WorldAgent
from world.world_engine import WorldNode

DEFAULT_LOG_PATH = Path("log") / "game_agent.log"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(filename)s:%(lineno)d %(message)s"


def _truncate_text(text: str, limit: int = 800) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...<truncated {len(text) - limit} chars>"


def _get_logger() -> logging.Logger:
    logger = logging.getLogger("game_agent")
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
class GameUpdateDecision:
    update_world: bool
    update_characters: bool
    raw: str
    reason: str = ""


@dataclass
class GameUpdateResult:
    decision: GameUpdateDecision
    world_decisions: list[ActionDecision] = field(default_factory=list)
    world_nodes: list[WorldNode] = field(default_factory=list)
    character_decisions: list[CharacterActionDecision] = field(default_factory=list)
    character_records: list[CharacterRecord] = field(default_factory=list)
    world_decision: Optional[ActionDecision] = None
    world_node: Optional[WorldNode] = None
    character_decision: Optional[CharacterActionDecision] = None
    character_record: Optional[CharacterRecord] = None


class GameAgent:
    def __init__(
        self,
        world_agent: Optional[WorldAgent] = None,
        character_agent: Optional[CharacterAgent] = None,
        llm_client: Optional[LLMClient] = None,
    ) -> None:
        self.world_agent = world_agent
        self.character_agent = character_agent
        self.logger = _get_logger()
        try:
            if llm_client:
                self.llm_client = llm_client
            elif world_agent and getattr(world_agent, "llm_client", None):
                self.llm_client = world_agent.llm_client
            elif character_agent and getattr(character_agent, "llm_client", None):
                self.llm_client = character_agent.llm_client
            else:
                self.llm_client = LLMClient()
        except Exception:
            self.logger.exception(
                "init game_agent llm_client failed world=%s characters=%s",
                bool(world_agent),
                bool(character_agent),
            )
            raise
        self.logger.info(
            "init game_agent world=%s characters=%s",
            bool(world_agent),
            bool(character_agent),
        )

    def decide_updates(self, update_info: str) -> GameUpdateDecision:
        response = ""
        try:
            prompt = self._build_decision_prompt(update_info)
            response = self._chat_once(
                prompt, system_prompt=self._system_prompt(), log_label="GAME_DECIDE"
            )
            update_world, update_characters, reason = self._parse_decision(
                response, update_info
            )
            decision = GameUpdateDecision(
                update_world=update_world,
                update_characters=update_characters,
                raw=response,
                reason=reason,
            )
            self.logger.info(
                "decide_updates world=%s characters=%s info_len=%s",
                update_world,
                update_characters,
                len(update_info),
            )
            return decision
        except Exception:
            self.logger.exception(
                "decide_updates failed info_len=%s response=%s",
                len(update_info),
                _truncate_text(response),
            )
            raise

    def apply_update(self, update_info: str) -> GameUpdateResult:
        decision = self.decide_updates(update_info)
        result = GameUpdateResult(decision=decision)
        if decision.update_world:
            if not self.world_agent:
                raise ValueError("World agent is required for world updates")
            if hasattr(self.world_agent, "collect_actions"):
                world_decisions = self.world_agent.collect_actions(update_info)
            else:
                world_decisions = self.world_agent.decide_actions(update_info)
            world_nodes = self.world_agent.apply_updates(world_decisions, update_info)
            result.world_decisions = world_decisions
            result.world_nodes = world_nodes
            result.world_decision = world_decisions[0] if world_decisions else None
            result.world_node = world_nodes[0] if world_nodes else None
        if decision.update_characters:
            if not self.character_agent:
                raise ValueError("Character agent is required for character updates")
            if hasattr(self.character_agent, "collect_actions"):
                char_decisions = self.character_agent.collect_actions(update_info)
            else:
                char_decisions = self.character_agent.decide_actions(update_info)
            char_records = self.character_agent.apply_updates(char_decisions, update_info)
            result.character_decisions = char_decisions
            result.character_records = char_records
            result.character_decision = char_decisions[0] if char_decisions else None
            result.character_record = char_records[0] if char_records else None
        return result

    # Prompt builders -----------------------------------------------------
    def _build_decision_prompt(self, update_info: str) -> str:
        lines = [
            "【任务】判断是否需要更新世界设定或角色档案",
            "世界更新：当剧情涉及地理、势力、政权、制度、重大事件变化。",
            "角色更新：当剧情涉及角色状态、关系、动机、能力变化。",
            "输出必须包含两处冗余，且只输出两行：",
            "1) WORLD=YES/NO; CHARACTER=YES/NO",
            '2) {"update_world":true|false,"update_characters":true|false,"reason":"..."}',
            f"剧情信息：{update_info.strip()}",
        ]
        if self.character_agent and self.character_agent.engine.records:
            lines.append("现有角色：")
            for record in self.character_agent.engine.records:
                lines.append(self._summarize_character(record))
        if self.world_agent and self.world_agent.engine.nodes:
            lines.append("现有世界节点：")
            nodes = sorted(
                self.world_agent.engine.nodes.values(),
                key=lambda item: item.identifier,
            )
            for node in nodes:
                lines.append(self._summarize_world_node(node))
        return "\n".join(lines)

    # Helpers -------------------------------------------------------------
    def _summarize_character(self, record: CharacterRecord) -> str:
        name = ""
        summary = ""
        if isinstance(record.profile, dict):
            name = str(record.profile.get("name", "")).strip()
            summary = str(record.profile.get("summary", "")).strip()
        parts = [record.identifier]
        if name:
            parts.append(name)
        label = f"简述:{summary}" if summary else ""
        return f"- {' '.join(parts)} {label}".strip()

    def _summarize_world_node(self, node: WorldNode) -> str:
        value = (node.value or "").strip()
        summary = _truncate_text(value, limit=240) if value else ""
        label = f": {summary}" if summary else ""
        return f"- {node.identifier} {node.key}{label}".strip()

    def _parse_decision(self, response: str, update_info: str) -> tuple[bool, bool, str]:
        for match in re.finditer(r"\{.*?\}", response, flags=re.DOTALL):
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                continue
            if "update_world" in data and "update_characters" in data:
                update_world = self._coerce_bool(data.get("update_world"))
                update_characters = self._coerce_bool(data.get("update_characters"))
                if update_world is not None and update_characters is not None:
                    reason = str(data.get("reason", "")).strip()
                    return update_world, update_characters, reason

        world_match = re.search(r"WORLD\s*[:=]\s*([A-Za-z0-9]+)", response, re.IGNORECASE)
        char_match = re.search(
            r"CHARACTER\s*[:=]\s*([A-Za-z0-9]+)", response, re.IGNORECASE
        )
        if world_match and char_match:
            update_world = self._coerce_bool(world_match.group(1))
            update_characters = self._coerce_bool(char_match.group(1))
            if update_world is not None and update_characters is not None:
                return update_world, update_characters, ""

        return self._heuristic_decision(update_info)

    def _heuristic_decision(self, update_info: str) -> tuple[bool, bool, str]:
        info = update_info.strip()
        world_keywords = [
            "世界",
            "地区",
            "城市",
            "王国",
            "政权",
            "势力",
            "战争",
            "灾难",
            "制度",
            "法律",
            "资源",
            "科技",
        ]
        character_keywords = [
            "角色",
            "人物",
            "主角",
            "同伴",
            "敌人",
            "盟友",
        ]
        update_world = any(keyword in info for keyword in world_keywords)
        update_characters = any(keyword in info for keyword in character_keywords)

        if self.character_agent:
            for record in self.character_agent.engine.records:
                if record.identifier and record.identifier in info:
                    update_characters = True
                    break
                if isinstance(record.profile, dict):
                    name = str(record.profile.get("name", "")).strip()
                    if name and name in info:
                        update_characters = True
                        break

        if self.world_agent and not update_world:
            for node in list(self.world_agent.engine.nodes.values())[:50]:
                key = node.key.strip()
                if key and key in info:
                    update_world = True
                    break

        if not update_world and not update_characters and len(info) > 120:
            update_world = True
            update_characters = True

        return update_world, update_characters, "heuristic"

    def _coerce_bool(self, value: object) -> Optional[bool]:
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value != 0
        if isinstance(value, str):
            cleaned = value.strip().lower()
            if cleaned in {"true", "yes", "y", "1"}:
                return True
            if cleaned in {"false", "no", "n", "0"}:
                return False
        return None

    def _system_prompt(self) -> str:
        return (
            "You are a precise game-master assistant. "
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
