from __future__ import annotations

import copy
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from character.character_agent import (
    CharacterActionDecision,
    CharacterAgent,
    UPDATE_TAG as CHARACTER_UPDATE_TAG,
)
from character.character_engine import CharacterRecord
from game.history_engine import HistoryChange, HistoryEngine
from llm_api.llm_client import LLMClient
from world.world_agent import ActionDecision, WorldAgent, UPDATE_TAG as WORLD_UPDATE_TAG
from world.world_engine import WorldNode

DEFAULT_LOG_PATH = Path("log") / "game_agent.log"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(filename)s:%(lineno)d %(message)s"
DEFAULT_SEARCH_ROUNDS = 2
DEFAULT_SEARCH_LIMIT = 4
DEFAULT_SEARCH_CONTEXT_LIMIT = 320
DEFAULT_COMMAND_VALIDATE_ROUNDS = 2
DEFAULT_POLITY_MERGE_KEYWORDS = ("合并", "并入", "吞并", "并吞", "并为", "归并")


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


@dataclass
class SearchReadState:
    world: dict[str, WorldNode] = field(default_factory=dict)
    characters: dict[str, CharacterRecord] = field(default_factory=dict)


class GameAgent:
    def __init__(
        self,
        world_agent: Optional[WorldAgent] = None,
        character_agent: Optional[CharacterAgent] = None,
        history_engine: Optional[HistoryEngine] = None,
        llm_client: Optional[LLMClient] = None,
    ) -> None:
        self.world_agent = world_agent
        self.character_agent = character_agent
        self.history_engine = history_engine
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

    def decide_updates(
        self, update_info: str, read_context: Optional[list[str]] = None
    ) -> GameUpdateDecision:
        response = ""
        try:
            if read_context is None:
                read_context = self._search_and_read(update_info)
            prompt = self._build_decision_prompt(update_info, read_context)
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
        world_snapshot = self._snapshot_world()
        character_snapshot = self._snapshot_characters()
        merge_result = self._apply_polity_merge(update_info)
        if merge_result:
            self._record_history(update_info, merge_result, world_snapshot, character_snapshot)
            return merge_result
        search_state = self._run_search_and_read(update_info)
        read_context = self._build_read_context_lines(
            search_state.world, search_state.characters, DEFAULT_SEARCH_CONTEXT_LIMIT
        )
        decision = self.decide_updates(update_info, read_context=read_context)
        result = GameUpdateResult(decision=decision)
        world_decisions: list[ActionDecision] = []
        char_decisions: list[CharacterActionDecision] = []

        for round_index in range(DEFAULT_COMMAND_VALIDATE_ROUNDS):
            world_decisions = []
            char_decisions = []
            if decision.update_world:
                if not self.world_agent:
                    raise ValueError("World agent is required for world updates")
                if hasattr(self.world_agent, "collect_actions"):
                    world_decisions = self.world_agent.collect_actions(update_info)
                else:
                    world_decisions = self.world_agent.decide_actions(update_info)
            if decision.update_characters:
                if not self.character_agent:
                    raise ValueError("Character agent is required for character updates")
                if hasattr(self.character_agent, "collect_actions"):
                    char_decisions = self.character_agent.collect_actions(update_info)
                else:
                    char_decisions = self.character_agent.decide_actions(update_info)

            if not world_decisions and not char_decisions:
                break

            valid, reason = self._validate_commands(
                update_info,
                read_context,
                decision,
                world_decisions,
                char_decisions,
                round_index + 1,
            )
            if valid:
                break
            if round_index >= DEFAULT_COMMAND_VALIDATE_ROUNDS - 1:
                self.logger.warning(
                    "command_validation_failed rounds=%s reason=%s",
                    DEFAULT_COMMAND_VALIDATE_ROUNDS,
                    reason,
                )
                break
            search_state = self._run_search_and_read(
                update_info,
                state=search_state,
                max_rounds=DEFAULT_SEARCH_ROUNDS,
                search_hint=reason,
            )
            read_context = self._build_read_context_lines(
                search_state.world, search_state.characters, DEFAULT_SEARCH_CONTEXT_LIMIT
            )
            decision = self.decide_updates(update_info, read_context=read_context)
            result.decision = decision

        if decision.update_world and world_decisions:
            world_nodes: list[WorldNode] = []
            world_nodes = self.world_agent.apply_updates(world_decisions, update_info)
            result.world_decisions = world_decisions
            result.world_nodes = world_nodes
            result.world_decision = world_decisions[0] if world_decisions else None
            result.world_node = world_nodes[0] if world_nodes else None
            region_decisions, region_nodes = self._maybe_update_children_for_region_updates(
                update_info, world_decisions, world_nodes, world_snapshot
            )
            if region_decisions:
                result.world_decisions.extend(region_decisions)
                result.world_nodes.extend(region_nodes)
                world_decisions = result.world_decisions
                world_nodes = result.world_nodes
        else:
            world_nodes = []
        if decision.update_characters and char_decisions:
            char_records = self.character_agent.apply_updates(char_decisions, update_info)
            result.character_decisions = char_decisions
            result.character_records = char_records
            result.character_decision = (
                char_decisions[0] if char_decisions else None
            )
            result.character_record = char_records[0] if char_records else None

        extra_decisions: list[CharacterActionDecision] = []
        extra_records: list[CharacterRecord] = []
        if world_decisions and world_nodes:
            updated_ids = {item.identifier for item in char_decisions}
            extra_decisions, extra_records = self._maybe_update_characters_for_polity_updates(
                update_info,
                world_decisions,
                world_nodes,
                updated_ids,
            )
            updated_ids.update(item.identifier for item in extra_decisions)
            removal_decisions, removal_records = (
                self._maybe_update_characters_for_polity_removals(
                    update_info,
                    world_decisions,
                    updated_ids,
                )
            )
            extra_decisions.extend(removal_decisions)
            extra_records.extend(removal_records)
        if extra_decisions:
            result.character_decisions.extend(extra_decisions)
            result.character_records.extend(extra_records)
            if not result.character_decision:
                result.character_decision = extra_decisions[0]
            if not result.character_record and extra_records:
                result.character_record = extra_records[0]
            if not result.decision.update_characters:
                result.decision = GameUpdateDecision(
                    update_world=result.decision.update_world,
                    update_characters=True,
                    raw=result.decision.raw,
                    reason=(result.decision.reason + ";polity_check").strip(";"),
                )
        self._record_history(update_info, result, world_snapshot, character_snapshot)
        return result

    # Search & read ------------------------------------------------------
    def _search_and_read(self, update_info: str) -> list[str]:
        state = self._run_search_and_read(update_info)
        return self._build_read_context_lines(
            state.world, state.characters, DEFAULT_SEARCH_CONTEXT_LIMIT
        )

    def _run_search_and_read(
        self,
        update_info: str,
        state: Optional[SearchReadState] = None,
        max_rounds: int = DEFAULT_SEARCH_ROUNDS,
        search_hint: str = "",
    ) -> SearchReadState:
        text = update_info.strip()
        if not text:
            return state or SearchReadState()
        if not self.world_agent and not self.character_agent:
            return state or SearchReadState()
        current = state or SearchReadState()
        read_world = current.world
        read_characters = current.characters
        for round_index in range(max_rounds):
            prompt = self._build_search_prompt(
                update_info,
                read_world,
                read_characters,
                search_hint=search_hint,
            )
            response = self._chat_once(
                prompt,
                system_prompt=self._system_prompt(),
                log_label=f"GAME_SEARCH_{round_index + 1}",
            )
            world_ids, character_ids = self._parse_search_response(response)
            world_ids = self._resolve_world_identifiers(world_ids)
            character_ids = self._resolve_character_identifiers(character_ids)
            if not world_ids and not character_ids:
                world_ids, character_ids = self._heuristic_search(update_info)
            new_world = self._read_world_nodes(world_ids, read_world)
            new_characters = self._read_character_records(character_ids, read_characters)

            decision_prompt = self._build_search_decision_prompt(
                update_info, read_world, read_characters
            )
            decision_response = self._chat_once(
                decision_prompt,
                system_prompt=self._system_prompt(),
                log_label=f"GAME_SEARCH_DECIDE_{round_index + 1}",
            )
            should_continue = self._parse_search_decision(decision_response)
            self.logger.info(
                "search_round=%s world_added=%s characters_added=%s continue=%s",
                round_index + 1,
                len(new_world),
                len(new_characters),
                should_continue,
            )
            if not should_continue:
                break
            if not new_world and not new_characters:
                self.logger.info("search_round_no_new_items round=%s", round_index + 1)
                break
        return current

    # Prompt builders -----------------------------------------------------
    def _build_decision_prompt(
        self, update_info: str, read_context: Optional[list[str]] = None
    ) -> str:
        lines = [
            "【任务】判断是否需要更新世界设定或角色档案",
            "世界更新：当剧情涉及地理、势力、政权、制度、重大事件变化。",
            "角色更新：当剧情涉及角色状态、关系、动机、能力变化。",
            "如果剧情涉及地区/国家/政权/城市等具体实体，应判定世界更新。",
            "输出必须包含两处冗余，且只输出两行：",
            "1) WORLD=YES/NO; CHARACTER=YES/NO",
            '2) {"update_world":true|false,"update_characters":true|false,"reason":"..."}',
            f"剧情信息：{update_info.strip()}",
        ]
        if read_context:
            lines.append("已读取内容：")
            lines.extend(read_context)
        if not read_context and self.character_agent and self.character_agent.engine.records:
            lines.append("现有角色：")
            records = sorted(
                self.character_agent.engine.records, key=lambda item: item.identifier
            )
            items = [
                self._format_character_context_item(
                    record, limit=DEFAULT_SEARCH_CONTEXT_LIMIT
                )
                for record in records
            ]
            lines.extend(
                self._pack_items(
                    f"C({len(items)})", items, DEFAULT_SEARCH_CONTEXT_LIMIT
                )
            )
        if not read_context and self.world_agent and self.world_agent.engine.nodes:
            lines.append("现有世界节点：")
            nodes = self._iter_world_nodes_prefer_micro()
            items = [
                self._format_world_context_item(
                    node, limit=DEFAULT_SEARCH_CONTEXT_LIMIT
                )
                for node in nodes
            ]
            lines.extend(
                self._pack_items(
                    f"W({len(items)})", items, DEFAULT_SEARCH_CONTEXT_LIMIT
                )
            )
        return "\n".join(lines)

    def _build_search_prompt(
        self,
        update_info: str,
        read_world: dict[str, WorldNode],
        read_characters: dict[str, CharacterRecord],
        search_hint: str = "",
    ) -> str:
        max_items = DEFAULT_SEARCH_LIMIT
        read_world_ids = "、".join(read_world.keys()) if read_world else "无"
        read_character_ids = "、".join(read_characters.keys()) if read_characters else "无"
        lines = [
            "【任务】搜索需要读取的世界节点与角色",
            f"每轮最多选择 {max_items} 个世界节点、{max_items} 个角色。",
            "仅选择确实需要读取的条目，用于后续决策。",
            "若涉及地区/国家/政权/城市等具体实体，优先选择 micro.* 节点。",
            "宏观节点仅用于世界法则、文明阶段、宏观主题等变更。",
            "输出必须包含两处冗余，且只输出两行：",
            "1) WORLD=id1,id2; CHARACTER=c1,c2",
            '2) {"world":["id1","id2"],"characters":["c1","c2"],"reason":"..."}',
            f"剧情信息：{update_info.strip()}",
            f"已读取世界节点：{read_world_ids}",
            f"已读取角色：{read_character_ids}",
        ]
        if search_hint:
            lines.append(f"需要补充的上下文：{search_hint}")
        if self.world_agent and self.world_agent.engine.nodes:
            lines.append("可用世界节点：")
            nodes = self._iter_world_nodes_prefer_micro()
            items = [self._format_world_list_item(node) for node in nodes]
            lines.extend(
                self._pack_items(
                    f"W({len(items)})", items, DEFAULT_SEARCH_CONTEXT_LIMIT
                )
            )
        else:
            lines.append("可用世界节点：无")
        if self.character_agent and self.character_agent.engine.records:
            lines.append("可用角色：")
            records = sorted(
                self.character_agent.engine.records, key=lambda item: item.identifier
            )
            items = [self._format_character_list_item(record) for record in records]
            lines.extend(
                self._pack_items(
                    f"C({len(items)})", items, DEFAULT_SEARCH_CONTEXT_LIMIT
                )
            )
        else:
            lines.append("可用角色：无")
        return "\n".join(lines)

    def _build_search_decision_prompt(
        self,
        update_info: str,
        read_world: dict[str, WorldNode],
        read_characters: dict[str, CharacterRecord],
    ) -> str:
        lines = [
            "【任务】判断是否继续搜索与读取",
            "如果已有足够信息用于后续判断，则回答 NO。",
            "输出必须包含两处冗余，且只输出两行：",
            "1) CONTINUE=YES/NO",
            '2) {"continue":true|false,"reason":"..."}',
            f"剧情信息：{update_info.strip()}",
        ]
        read_context = self._build_read_context_lines(
            read_world, read_characters, DEFAULT_SEARCH_CONTEXT_LIMIT
        )
        if read_context:
            lines.append("已读取内容：")
            lines.extend(read_context)
        else:
            lines.append("已读取内容：无")
        return "\n".join(lines)

    def _build_command_validation_prompt(
        self,
        update_info: str,
        read_context: list[str],
        decision: GameUpdateDecision,
        world_decisions: list[ActionDecision],
        character_decisions: list[CharacterActionDecision],
    ) -> str:
        lines = [
            "【任务】判断调用命令是否合理",
            "根据剧情信息与已读取内容判断即将调用的更新命令是否合理。",
            "如果不合理，请回答 NO，并说明需要补充的上下文。",
            "输出必须包含两处冗余，且只输出两行：",
            "1) VALID=YES/NO",
            '2) {"valid":true|false,"reason":"..."}',
            f"剧情信息：{update_info.strip()}",
            f"调用决策：world={decision.update_world}; characters={decision.update_characters}",
        ]
        if read_context:
            lines.append("已读取内容：")
            lines.extend(read_context)
        else:
            lines.append("已读取内容：无")

        lines.append("调用命令：")
        if world_decisions:
            lines.append("世界：")
            for item in world_decisions:
                lines.append(self._summarize_world_command(item))
        else:
            lines.append("世界：- 无")
        if character_decisions:
            lines.append("角色：")
            for item in character_decisions:
                lines.append(self._summarize_character_command(item))
        else:
            lines.append("角色：- 无")
        return "\n".join(lines)

    def _build_polity_character_decision_prompt(
        self,
        update_info: str,
        polities: list[WorldNode],
        candidates: list[CharacterRecord],
    ) -> str:
        lines = [
            "【任务】判断是否需要更新角色档案",
            "根据剧情信息与政权更新，选择需要更新的角色ID。",
            "仅在角色会受到该政权变化影响时选择。",
            "输出必须包含两处冗余，且只输出两行：",
            "1) UPDATE=ID1,ID2 或 NONE",
            '2) {"update":["c1","c2"],"reason":"..."}',
            f"剧情信息：{update_info.strip()}",
            "已更新政权：",
        ]
        polity_items = [
            self._format_polity_context_item(node, limit=DEFAULT_SEARCH_CONTEXT_LIMIT)
            for node in polities
        ]
        lines.extend(
            self._pack_items(
                f"P({len(polity_items)})",
                polity_items,
                DEFAULT_SEARCH_CONTEXT_LIMIT,
            )
        )
        lines.append("候选角色：")
        character_items = [
            self._format_character_context_item(record, limit=DEFAULT_SEARCH_CONTEXT_LIMIT)
            for record in candidates
        ]
        lines.extend(
            self._pack_items(
                f"C({len(character_items)})",
                character_items,
                DEFAULT_SEARCH_CONTEXT_LIMIT,
            )
        )
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

    def _summarize_world_node(self, node: WorldNode, limit: int = 240) -> str:
        value = self._compact_text((node.value or "").strip())
        summary = _truncate_text(value, limit=limit) if value else ""
        label = f": {summary}" if summary else ""
        return f"- {node.identifier} {node.key}{label}".strip()

    def _summarize_world_node_search(self, node: WorldNode) -> str:
        return f"- {node.identifier} {node.key}".strip()

    def _compact_text(self, text: str) -> str:
        return " ".join(text.split())

    def _is_micro_identifier(self, identifier: str) -> bool:
        return identifier == "micro" or identifier.startswith("micro.")

    def _is_macro_identifier(self, identifier: str) -> bool:
        return identifier == "macro" or (identifier and identifier[0].isdigit())

    def _iter_world_nodes_prefer_micro(self) -> list[WorldNode]:
        if not self.world_agent:
            return []
        nodes = list(self.world_agent.engine.nodes.values())
        return sorted(
            nodes,
            key=lambda node: (
                0
                if self._is_micro_identifier(node.identifier)
                else 2
                if self._is_macro_identifier(node.identifier)
                else 1,
                node.identifier,
            ),
        )

    def _pack_items(self, label: str, items: list[str], max_line_len: int) -> list[str]:
        lines: list[str] = []
        prefix = f"{label}: "
        current = prefix
        for item in items:
            cleaned = item.strip()
            if not cleaned:
                continue
            if current == prefix:
                candidate = f"{prefix}{cleaned}"
            else:
                candidate = f"{current} | {cleaned}"
            if len(candidate) > max_line_len and current != prefix:
                lines.append(current)
                current = f"{prefix}{cleaned}"
            else:
                current = candidate
        if current != prefix:
            lines.append(current)
        return lines

    def _format_world_context_item(self, node: WorldNode, limit: int) -> str:
        value = self._compact_text((node.value or "").strip())
        summary = _truncate_text(value, limit=limit) if value else ""
        if summary:
            return f"{node.identifier}/{node.key}={summary}"
        return f"{node.identifier}/{node.key}"

    def _format_world_list_item(self, node: WorldNode) -> str:
        return f"{node.identifier}/{node.key}".strip()

    def _format_character_list_item(self, record: CharacterRecord) -> str:
        name = ""
        if isinstance(record.profile, dict):
            name = str(record.profile.get("name", "")).strip()
        label = f"/{name}" if name else ""
        return f"{record.identifier}{label}".strip()

    def _format_character_context_item(
        self, record: CharacterRecord, limit: int
    ) -> str:
        name = ""
        summary = ""
        extras: list[str] = []
        if isinstance(record.profile, dict):
            name = str(record.profile.get("name", "")).strip()
            summary = self._compact_text(str(record.profile.get("summary", "")).strip())
            for key in ("profession", "faction", "species", "tier"):
                value = str(record.profile.get(key, "")).strip()
                if value:
                    extras.append(f"{key}={self._compact_text(value)}")
        base = f"{record.identifier}/{name}" if name else record.identifier
        detail_parts: list[str] = []
        if summary:
            detail_parts.append(summary)
        if extras:
            detail_parts.append(",".join(extras))
        detail = " | ".join(detail_parts).strip()
        if detail:
            detail = _truncate_text(detail, limit=limit)
            return f"{base}={detail}"
        return base

    def _format_polity_context_item(self, node: WorldNode, limit: int) -> str:
        value = self._compact_text((node.value or "").strip())
        summary = _truncate_text(value, limit=limit) if value else ""
        if summary:
            return f"{node.identifier}/{node.key}={summary}"
        return f"{node.identifier}/{node.key}"

    def _build_polity_update_context(
        self, polities: list[WorldNode], limit: int
    ) -> str:
        if not polities:
            return ""
        items = [
            self._format_polity_context_item(node, limit=limit)
            for node in polities
        ]
        joined = "；".join(item for item in items if item)
        if not joined:
            return ""
        return f"关联政权更新：{joined}"

    def _is_micro_polity_identifier(self, identifier: str) -> bool:
        if not identifier.startswith("micro."):
            return False
        parts = identifier.split(".")
        return len(parts) >= 3 and parts[2].startswith("p")

    def _resolve_polity_identifier(self, identifier: str) -> Optional[str]:
        if not identifier.startswith("micro."):
            return None
        parts = identifier.split(".")
        if len(parts) >= 3 and parts[2].startswith("p"):
            return ".".join(parts[:3])
        return None

    def _collect_removed_polity_ids(
        self, world_decisions: list[ActionDecision]
    ) -> list[str]:
        removed: list[str] = []
        for decision in world_decisions:
            if self._normalize_action_name(decision.flag) != "REMOVE_NODE":
                continue
            polity_id = self._resolve_polity_identifier(decision.index)
            if polity_id and self._is_micro_polity_identifier(polity_id):
                removed.append(polity_id)
        seen: set[str] = set()
        ordered: list[str] = []
        for polity_id in removed:
            if polity_id in seen:
                continue
            ordered.append(polity_id)
            seen.add(polity_id)
        return ordered

    def _build_polity_removal_context(self, polity_ids: list[str]) -> str:
        if not polity_ids:
            return ""
        items = "、".join(polity_ids)
        return f"关联政权删除：{items}"

    def _apply_polity_merge(self, update_info: str) -> Optional[GameUpdateResult]:
        if not self.world_agent or not self.character_agent:
            return None
        polities = self._list_micro_polities()
        if len(polities) < 2:
            return None
        if not self._is_polity_merge_candidate(update_info, polities):
            return None
        prompt = self._build_polity_merge_prompt(update_info, polities)
        response = self._chat_once(
            prompt,
            system_prompt=self._system_prompt(),
            log_label="GAME_POLITY_MERGE",
        )
        keep_raw, remove_raw = self._parse_polity_merge_response(response)
        if not keep_raw or not remove_raw:
            return None
        polity_lookup = {node.identifier: node for node in polities}
        keep_id = self._resolve_polity_identifier(keep_raw, polity_lookup)
        remove_id = self._resolve_polity_identifier(remove_raw, polity_lookup)
        if not keep_id or not remove_id or keep_id == remove_id:
            return None
        keep_node = self.world_agent.engine.nodes.get(keep_id)
        remove_node = self.world_agent.engine.nodes.get(remove_id)
        if not keep_node or not remove_node:
            return None

        update_payload = self._build_polity_merge_update_payload(
            update_info, keep_node, remove_node
        )
        updated_node = self.world_agent.apply_update(
            WORLD_UPDATE_TAG, keep_id, update_payload
        )
        aspect_nodes = self._apply_polity_merge_aspect_updates(
            update_info, keep_node, remove_node
        )
        removed = self.world_agent.remove_polity(remove_id)

        decision = GameUpdateDecision(
            update_world=True,
            update_characters=True,
            raw=response,
            reason="polity_merge",
        )
        result = GameUpdateResult(decision=decision)
        result.world_decisions = [
            ActionDecision(flag=WORLD_UPDATE_TAG, index=keep_id, raw=response)
        ]
        result.world_nodes = [updated_node]
        if aspect_nodes:
            for node in aspect_nodes:
                result.world_decisions.append(
                    ActionDecision(
                        flag=WORLD_UPDATE_TAG,
                        index=node.identifier,
                        raw="polity_merge_aspect",
                    )
                )
                result.world_nodes.append(node)
        result.world_decision = result.world_decisions[0]
        result.world_node = updated_node

        char_decisions, char_records = self._apply_polity_merge_character_updates(
            update_info, keep_node, remove_node, keep_id, remove_id
        )
        result.character_decisions = char_decisions
        result.character_records = char_records
        result.character_decision = char_decisions[0] if char_decisions else None
        result.character_record = char_records[0] if char_records else None

        self.logger.info(
            "polity_merge keep=%s remove=%s removed=%s characters=%s",
            keep_id,
            remove_id,
            len(removed),
            len(char_records),
        )
        return result

    def _apply_polity_merge_character_updates(
        self,
        update_info: str,
        keep_node: WorldNode,
        remove_node: WorldNode,
        keep_id: str,
        remove_id: str,
    ) -> tuple[list[CharacterActionDecision], list[CharacterRecord]]:
        if not self.character_agent:
            return [], []
        impacted: list[tuple[CharacterRecord, Optional[str]]] = []
        for record in self.character_agent.engine.records:
            if record.polity_id in {keep_id, remove_id}:
                impacted.append((record, record.polity_id))
        if not impacted:
            return [], []

        keep_region_id = keep_node.parent.identifier if keep_node.parent else None
        merge_context = self._build_polity_merge_context(keep_node, remove_node)
        decisions: list[CharacterActionDecision] = []
        records: list[CharacterRecord] = []
        for record, original_polity_id in impacted:
            if original_polity_id == remove_id:
                record.polity_id = keep_id
                if keep_region_id:
                    record.region_id = keep_region_id
            update_payload = self._build_polity_merge_character_payload(
                update_info,
                merge_context,
                keep_node,
                remove_node,
                original_polity_id or "",
            )
            decision = CharacterActionDecision(
                flag=CHARACTER_UPDATE_TAG,
                identifier=record.identifier,
                raw="polity_merge",
            )
            updated = self.character_agent.apply_update(
                decision.flag, decision.identifier, update_payload
            )
            decisions.append(decision)
            records.append(updated)
        return decisions, records

    def _apply_polity_merge_aspect_updates(
        self,
        update_info: str,
        keep_node: WorldNode,
        remove_node: WorldNode,
    ) -> list[WorldNode]:
        if not self.world_agent or not keep_node.children:
            return []
        updated: list[WorldNode] = []
        for child in keep_node.children.values():
            payload = self._build_polity_merge_aspect_payload(
                update_info, keep_node, remove_node, child
            )
            node = self.world_agent.apply_update(
                WORLD_UPDATE_TAG, child.identifier, payload
            )
            updated.append(node)
        return updated

    def _list_micro_polities(self) -> list[WorldNode]:
        if not self.world_agent:
            return []
        polities = [
            node
            for node in self.world_agent.engine.nodes.values()
            if self._is_micro_polity(node)
        ]
        return sorted(polities, key=lambda item: item.identifier)

    def _is_polity_merge_candidate(
        self, update_info: str, polities: list[WorldNode]
    ) -> bool:
        text = update_info.strip()
        if not text:
            return False
        if not any(keyword in text for keyword in DEFAULT_POLITY_MERGE_KEYWORDS):
            return False
        mentioned: set[str] = set()
        for node in polities:
            key = node.key.strip()
            if node.identifier and node.identifier in text:
                mentioned.add(node.identifier)
            if key and key in text:
                mentioned.add(node.identifier)
            if len(mentioned) >= 2:
                return True
        return False

    def _build_polity_merge_prompt(
        self, update_info: str, polities: list[WorldNode]
    ) -> str:
        lines = [
            "【任务】判断是否为政权合并，并给出保留与删除的政权ID。",
            "仅从可用政权列表中选择。",
            "如果不是政权合并或无法判断，请输出 NONE。",
            "输出必须包含两处冗余，且只输出两行：",
            "1) MERGE=KEEP_ID; REMOVE_ID 或 MERGE=NONE",
            '2) {"keep":"ID","remove":"ID","reason":"..."} 或 {"merge":false,"reason":"..."}',
            f"剧情信息：{update_info.strip()}",
            "可用政权：",
        ]
        for node in polities:
            region_label = ""
            if node.parent:
                region_label = node.parent.key.strip()
            label = f"{node.identifier} {node.key}"
            if region_label:
                label = f"{label} (region: {region_label})"
            lines.append(f"- {label}")
        return "\n".join(lines)

    def _parse_polity_merge_response(self, response: str) -> tuple[str, str]:
        for match in re.finditer(r"\{.*?\}", response, flags=re.DOTALL):
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            if "merge" in data:
                merge_flag = self._coerce_bool(data.get("merge"))
                if merge_flag is False:
                    return "", ""
            keep = data.get("keep") or data.get("keep_id") or data.get("target")
            remove = data.get("remove") or data.get("remove_id") or data.get("source")
            keep_text = str(keep or "").strip()
            remove_text = str(remove or "").strip()
            if keep_text.upper() in {"NONE", "NO", "无"}:
                keep_text = ""
            if remove_text.upper() in {"NONE", "NO", "无"}:
                remove_text = ""
            if keep_text or remove_text:
                return keep_text, remove_text

        merge_match = re.search(r"MERGE\s*[:=]\s*([^\n]+)", response, re.IGNORECASE)
        if merge_match:
            raw = merge_match.group(1).strip()
            if raw.upper() in {"NONE", "NO", "无"}:
                return "", ""
            parts = re.split(r"[;,，]+", raw)
            if len(parts) >= 2:
                return parts[0].strip(), parts[1].strip()
        return "", ""

    def _resolve_polity_identifier(
        self, token: str, polity_lookup: dict[str, WorldNode]
    ) -> Optional[str]:
        cleaned = token.strip()
        if not cleaned:
            return None
        if cleaned in polity_lookup:
            return cleaned
        if "/" in cleaned:
            head = cleaned.split("/", 1)[0].strip()
            if head in polity_lookup:
                return head
        cleaned = cleaned.strip("()（）")
        matches = [
            identifier
            for identifier, node in polity_lookup.items()
            if node.key.strip() == cleaned
        ]
        if len(matches) == 1:
            return matches[0]
        for identifier, node in polity_lookup.items():
            if identifier and identifier in cleaned:
                return identifier
        key_matches = [
            identifier
            for identifier, node in polity_lookup.items()
            if node.key.strip() and node.key.strip() in cleaned
        ]
        if len(key_matches) == 1:
            return key_matches[0]
        return None

    def _build_polity_merge_context(
        self, keep_node: WorldNode, remove_node: WorldNode
    ) -> str:
        merge_line = (
            f"政权合并: {remove_node.identifier} {remove_node.key} 并入 "
            f"{keep_node.identifier} {keep_node.key}"
        )
        details = self._build_polity_update_context(
            [keep_node, remove_node],
            limit=DEFAULT_SEARCH_CONTEXT_LIMIT,
        )
        if details:
            return f"{merge_line}\n{details}"
        return merge_line

    def _build_polity_merge_update_payload(
        self, update_info: str, keep_node: WorldNode, remove_node: WorldNode
    ) -> str:
        context = self._build_polity_merge_context(keep_node, remove_node)
        return (
            f"{update_info.strip()}\n{context}\n"
            "请根据合并结果更新保留政权的描述。"
        )

    def _build_polity_merge_character_payload(
        self,
        update_info: str,
        merge_context: str,
        keep_node: WorldNode,
        remove_node: WorldNode,
        original_polity_id: str,
    ) -> str:
        lines = [
            update_info.strip(),
            merge_context,
            "请更新角色档案以反映政权合并影响。",
        ]
        if original_polity_id == remove_node.identifier:
            lines.append(
                f"角色原属政权: {remove_node.identifier} {remove_node.key}；"
                f"现归属: {keep_node.identifier} {keep_node.key}"
            )
        else:
            lines.append(
                f"角色所属政权: {keep_node.identifier} {keep_node.key} 吸收 "
                f"{remove_node.identifier} {remove_node.key}"
            )
        return "\n".join(line for line in lines if line)

    def _build_polity_merge_aspect_payload(
        self,
        update_info: str,
        keep_node: WorldNode,
        remove_node: WorldNode,
        aspect_node: WorldNode,
    ) -> str:
        context = self._build_polity_merge_context(keep_node, remove_node)
        return (
            f"{update_info.strip()}\n{context}\n"
            f"请更新该政权子节点「{aspect_node.key}」的描述，反映合并后的变化。"
        )

    def _is_micro_polity(self, node: WorldNode) -> bool:
        return bool(node.parent and node.parent.parent and node.parent.parent.identifier == "micro")

    def _is_micro_region(self, node: WorldNode) -> bool:
        return bool(node.parent and node.parent.identifier == "micro")

    def _resolve_polity_from_node(self, node: WorldNode) -> Optional[WorldNode]:
        if self._is_micro_polity(node):
            return node
        if node.parent and self._is_micro_polity(node.parent):
            return node.parent
        return None

    def _collect_polity_nodes_from_updates(
        self,
        world_decisions: list[ActionDecision],
        world_nodes: list[WorldNode],
    ) -> list[WorldNode]:
        polities: dict[str, WorldNode] = {}
        for idx, decision in enumerate(world_decisions):
            if self._normalize_action_name(decision.flag) != "UPDATE_NODE":
                continue
            node = world_nodes[idx] if idx < len(world_nodes) else None
            if not node:
                continue
            polity = self._resolve_polity_from_node(node)
            if polity:
                polities[polity.identifier] = polity
        return list(polities.values())

    def _collect_updated_regions(
        self,
        world_decisions: list[ActionDecision],
        world_nodes: list[WorldNode],
        world_snapshot: dict[str, Dict[str, object]],
    ) -> list[WorldNode]:
        regions: dict[str, WorldNode] = {}
        for idx, decision in enumerate(world_decisions):
            if self._normalize_action_name(decision.flag) != "UPDATE_NODE":
                continue
            node = world_nodes[idx] if idx < len(world_nodes) else None
            if not node or not self._is_micro_region(node):
                continue
            before = world_snapshot.get(node.identifier)
            if not self._region_changed(node, before):
                continue
            regions[node.identifier] = node
        return list(regions.values())

    def _region_changed(
        self, node: WorldNode, before: Optional[Dict[str, object]]
    ) -> bool:
        if not before:
            return True
        old_key = str(before.get("key", "")).strip()
        old_value = str(before.get("value", "")).strip()
        new_key = str(node.key or "").strip()
        new_value = str(node.value or "").strip()
        return old_key != new_key or old_value != new_value

    def _maybe_update_children_for_region_updates(
        self,
        update_info: str,
        world_decisions: list[ActionDecision],
        world_nodes: list[WorldNode],
        world_snapshot: dict[str, Dict[str, object]],
    ) -> tuple[list[ActionDecision], list[WorldNode]]:
        if not self.world_agent:
            return [], []
        regions = self._collect_updated_regions(
            world_decisions, world_nodes, world_snapshot
        )
        if not regions:
            return [], []
        skip_ids = {decision.index for decision in world_decisions}
        decisions: list[ActionDecision] = []
        nodes: list[WorldNode] = []
        for region in regions:
            if not region.children:
                continue
            prompt = self._build_region_children_decision_prompt(
                update_info, region, world_snapshot
            )
            response = self._chat_once(
                prompt,
                system_prompt=self._system_prompt(),
                log_label="GAME_REGION_CHILDREN_DECIDE",
            )
            should_update, reason = self._parse_region_children_decision(response)
            if not should_update:
                self.logger.info(
                    "region_children_skip region=%s reason=%s",
                    region.identifier,
                    reason,
                )
                continue
            payload = self._build_region_child_update_payload(
                update_info, region, world_snapshot
            )
            updated_count = 0
            for child in region.children.values():
                if child.identifier in skip_ids:
                    continue
                decision = ActionDecision(
                    flag=WORLD_UPDATE_TAG,
                    index=child.identifier,
                    raw="region_children",
                )
                node = self.world_agent.apply_update(
                    decision.flag, decision.index, payload
                )
                decisions.append(decision)
                nodes.append(node)
                updated_count += 1
            if updated_count:
                self.logger.info(
                    "region_children_update region=%s children=%s",
                    region.identifier,
                    updated_count,
                )
        return decisions, nodes

    def _build_region_children_decision_prompt(
        self,
        update_info: str,
        region: WorldNode,
        world_snapshot: dict[str, Dict[str, object]],
    ) -> str:
        before = world_snapshot.get(region.identifier, {})
        before_value = _truncate_text(str(before.get("value", "")).strip(), limit=240)
        after_value = _truncate_text(str(region.value or "").strip(), limit=240)
        children = [
            f"{child.identifier} {child.key}".strip()
            for child in region.children.values()
        ]
        child_text = "、".join(children) if children else "无"
        return "\n".join(
            [
                "【任务】判断是否需要更新地区子节点",
                "地区节点发生变化时，判断是否需要把变化同步到所有子节点。",
                "只输出一行JSON：",
                '{"update_children":true|false,"reason":"..."}',
                f"地区节点：{region.identifier} {region.key}",
                f"原内容：{before_value or '无'}",
                f"新内容：{after_value or '无'}",
                f"子节点：{child_text}",
                f"剧情信息：{update_info.strip()}",
            ]
        )

    def _parse_region_children_decision(self, response: str) -> tuple[bool, str]:
        for line in response.splitlines():
            cleaned = line.strip()
            if not cleaned:
                continue
            if cleaned.startswith("{") and cleaned.endswith("}"):
                try:
                    data = json.loads(cleaned)
                except json.JSONDecodeError:
                    continue
                decision = self._coerce_bool(data.get("update_children"))
                if decision is not None:
                    return decision, str(data.get("reason", "")).strip()
        for token in response.replace(",", " ").replace(";", " ").split():
            decision = self._coerce_bool(token)
            if decision is not None:
                return decision, ""
        return False, ""

    def _build_region_child_update_payload(
        self,
        update_info: str,
        region: WorldNode,
        world_snapshot: dict[str, Dict[str, object]],
    ) -> str:
        before = world_snapshot.get(region.identifier, {})
        before_key = str(before.get("key", "")).strip()
        before_value = _truncate_text(str(before.get("value", "")).strip(), limit=320)
        after_value = _truncate_text(str(region.value or "").strip(), limit=320)
        if before_key and before_key != region.key:
            key_line = f"地区节点变化：{before_key} -> {region.key}"
        else:
            key_line = f"地区节点：{region.key}"
        lines = [
            update_info.strip(),
            key_line,
            f"原内容：{before_value or '无'}",
            f"新内容：{after_value or '无'}",
            "请根据地区变化更新该子节点内容。",
        ]
        return "\n".join(line for line in lines if line)

    def _find_characters_for_polities(
        self, polities: list[WorldNode]
    ) -> list[CharacterRecord]:
        if not self.character_agent or not polities:
            return []
        polity_ids = {node.identifier for node in polities}
        candidates: list[CharacterRecord] = []
        for record in self.character_agent.engine.records:
            if record.polity_id and record.polity_id in polity_ids:
                candidates.append(record)
        return candidates

    def _summarize_world_command(self, decision: ActionDecision) -> str:
        action = self._normalize_action_name(decision.flag)
        label = ""
        if self.world_agent:
            node = self.world_agent.engine.nodes.get(decision.index)
            if node:
                label = node.key.strip()
        suffix = f" {label}" if label else ""
        return f"- {action} {decision.index}{suffix}".strip()

    def _summarize_character_command(
        self, decision: CharacterActionDecision
    ) -> str:
        action = self._normalize_action_name(decision.flag)
        name = ""
        if self.character_agent:
            for record in self.character_agent.engine.records:
                if record.identifier == decision.identifier:
                    if isinstance(record.profile, dict):
                        name = str(record.profile.get("name", "")).strip()
                    break
        suffix = f" {name}" if name else ""
        return f"- {action} {decision.identifier}{suffix}".strip()

    def _summarize_character_profile(
        self, record: CharacterRecord, limit: int = DEFAULT_SEARCH_CONTEXT_LIMIT
    ) -> str:
        item = self._format_character_context_item(record, limit=limit)
        if not item:
            return f"- {record.identifier}".strip()
        return f"- {item}".strip()

    def _snapshot_world(self) -> dict[str, Dict[str, object]]:
        if not self.world_agent:
            return {}
        snapshot: dict[str, Dict[str, object]] = {}
        for node in self.world_agent.engine.nodes.values():
            snapshot[node.identifier] = {
                "key": node.key,
                "value": node.value,
                "children": sorted(node.children.keys()),
            }
        return snapshot

    def _snapshot_characters(self) -> dict[str, Dict[str, object] | str]:
        if not self.character_agent:
            return {}
        snapshot: dict[str, Dict[str, object] | str] = {}
        for record in self.character_agent.engine.records:
            profile = record.profile
            if isinstance(profile, dict):
                snapshot[record.identifier] = copy.deepcopy(profile)
            else:
                snapshot[record.identifier] = str(profile or "")
        return snapshot

    def _build_world_changes(
        self,
        decisions: list[ActionDecision],
        nodes: list[WorldNode],
        snapshot: dict[str, Dict[str, object]],
    ) -> list[HistoryChange]:
        changes: list[HistoryChange] = []
        for idx, decision in enumerate(decisions):
            action = self._normalize_action_name(decision.flag)
            node = nodes[idx] if idx < len(nodes) else None
            identifier = node.identifier if node else decision.index
            before = snapshot.get(identifier)
            after: Dict[str, object] | None = None
            if node and action != "REMOVE_NODE":
                after = {
                    "key": node.key,
                    "value": node.value,
                    "children": sorted(node.children.keys()),
                }
            change = HistoryChange(
                kind="world",
                action=action,
                identifier=identifier,
                before=before,
                after=after,
            )
            changes.append(change)
        return changes

    def _build_character_changes(
        self,
        decisions: list[CharacterActionDecision],
        records: list[CharacterRecord],
        snapshot: dict[str, Dict[str, object] | str],
    ) -> list[HistoryChange]:
        changes: list[HistoryChange] = []
        for idx, decision in enumerate(decisions):
            action = self._normalize_action_name(decision.flag)
            record = records[idx] if idx < len(records) else None
            identifier = record.identifier if record else decision.identifier
            before = snapshot.get(identifier)
            after: Dict[str, object] | str | None = None
            if record:
                if isinstance(record.profile, dict):
                    after = copy.deepcopy(record.profile)
                else:
                    after = str(record.profile or "")
            change = HistoryChange(
                kind="character",
                action=action,
                identifier=identifier,
                before=before if isinstance(before, dict) else {"raw": before} if before else None,
                after=after if isinstance(after, dict) else {"raw": after} if after else None,
            )
            changes.append(change)
        return changes

    def _record_history(
        self,
        update_info: str,
        result: GameUpdateResult,
        world_snapshot: dict[str, Dict[str, object]],
        character_snapshot: dict[str, Dict[str, object] | str],
    ) -> None:
        if not self.history_engine:
            return
        decision_payload = {
            "update_world": result.decision.update_world,
            "update_characters": result.decision.update_characters,
            "reason": result.decision.reason,
        }
        world_changes = self._build_world_changes(
            result.world_decisions, result.world_nodes, world_snapshot
        )
        character_changes = self._build_character_changes(
            result.character_decisions, result.character_records, character_snapshot
        )
        self.history_engine.record(
            update_info,
            decision_payload,
            world_changes,
            character_changes,
        )

    def _normalize_action_name(self, flag: str) -> str:
        cleaned = flag.strip()
        if "ADD_NODE" in cleaned:
            return "ADD_NODE"
        if "UPDATE_NODE" in cleaned:
            return "UPDATE_NODE"
        if "REMOVE_NODE" in cleaned:
            return "REMOVE_NODE"
        if "ADD_CHARACTER" in cleaned:
            return "ADD_CHARACTER"
        if "UPDATE_CHARACTER" in cleaned:
            return "UPDATE_CHARACTER"
        return cleaned.strip("<|>").strip()

    def _format_character_profile(self, profile: Dict[str, object] | str) -> str:
        if isinstance(profile, dict):
            return json.dumps(profile, ensure_ascii=False, separators=(",", ":"))
        return str(profile or "")

    def _build_read_context_lines(
        self,
        read_world: dict[str, WorldNode],
        read_characters: dict[str, CharacterRecord],
        limit: int,
    ) -> list[str]:
        lines: list[str] = []
        if read_world:
            items = [
                self._format_world_context_item(node, limit=limit)
                for node in sorted(read_world.values(), key=lambda item: item.identifier)
            ]
            lines.extend(self._pack_items(f"W({len(items)})", items, limit))
        if read_characters:
            items = [
                self._format_character_context_item(record, limit=limit)
                for record in sorted(
                    read_characters.values(), key=lambda item: item.identifier
                )
            ]
            lines.extend(self._pack_items(f"C({len(items)})", items, limit))
        return lines

    def _parse_search_response(self, response: str) -> tuple[list[str], list[str]]:
        world_ids: list[str] = []
        character_ids: list[str] = []
        for match in re.finditer(r"\{.*?\}", response, flags=re.DOTALL):
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            world_ids.extend(self._coerce_id_list(data.get("world")))
            character_ids.extend(
                self._coerce_id_list(data.get("characters") or data.get("character"))
            )

        if not world_ids and not character_ids:
            world_match = re.search(
                r"WORLD\s*[:=]\s*([^\n;]+)", response, re.IGNORECASE
            )
            char_match = re.search(
                r"CHARACTER\s*[:=]\s*([^\n;]+)", response, re.IGNORECASE
            )
            if world_match:
                world_ids.extend(self._split_identifiers(world_match.group(1)))
            if char_match:
                character_ids.extend(self._split_identifiers(char_match.group(1)))

        return (
            world_ids[:DEFAULT_SEARCH_LIMIT],
            character_ids[:DEFAULT_SEARCH_LIMIT],
        )

    def _parse_search_decision(self, response: str) -> bool:
        for match in re.finditer(r"\{.*?\}", response, flags=re.DOTALL):
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                continue
            if "continue" in data:
                decision = self._coerce_bool(data.get("continue"))
                if decision is not None:
                    return decision
        continue_match = re.search(
            r"CONTINUE\s*[:=]\s*([A-Za-z0-9]+)", response, re.IGNORECASE
        )
        if continue_match:
            decision = self._coerce_bool(continue_match.group(1))
            if decision is not None:
                return decision
        return False

    def _parse_character_update_ids(
        self, response: str, candidate_ids: set[str]
    ) -> list[str]:
        updates: list[str] = []
        for match in re.finditer(r"\{.*?\}", response, flags=re.DOTALL):
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            updates.extend(self._coerce_id_list(data.get("update")))

        if not updates:
            update_match = re.search(
                r"UPDATE\s*[:=]\s*([^\n]+)", response, re.IGNORECASE
            )
            if update_match:
                raw = update_match.group(1).strip()
                if raw.upper() not in {"NONE", "NO", "无"}:
                    updates.extend(self._split_identifiers(raw))

        seen: set[str] = set()
        filtered: list[str] = []
        for identifier in updates:
            if identifier in candidate_ids and identifier not in seen:
                filtered.append(identifier)
                seen.add(identifier)
        return filtered

    def _parse_command_validation(self, response: str) -> tuple[bool, str]:
        for match in re.finditer(r"\{.*?\}", response, flags=re.DOTALL):
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                continue
            if "valid" in data:
                decision = self._coerce_bool(data.get("valid"))
                if decision is not None:
                    reason = str(data.get("reason", "")).strip()
                    return decision, reason
        valid_match = re.search(r"VALID\s*[:=]\s*([A-Za-z0-9]+)", response, re.IGNORECASE)
        if valid_match:
            decision = self._coerce_bool(valid_match.group(1))
            if decision is not None:
                return decision, ""
        return False, ""

    def _validate_commands(
        self,
        update_info: str,
        read_context: list[str],
        decision: GameUpdateDecision,
        world_decisions: list[ActionDecision],
        character_decisions: list[CharacterActionDecision],
        round_index: int,
    ) -> tuple[bool, str]:
        prompt = self._build_command_validation_prompt(
            update_info,
            read_context,
            decision,
            world_decisions,
            character_decisions,
        )
        response = self._chat_once(
            prompt,
            system_prompt=self._system_prompt(),
            log_label=f"GAME_COMMAND_VALIDATE_{round_index}",
        )
        valid, reason = self._parse_command_validation(response)
        self.logger.info(
            "command_validation round=%s valid=%s reason=%s",
            round_index,
            valid,
            reason,
        )
        return valid, reason

    def _maybe_update_characters_for_polity_updates(
        self,
        update_info: str,
        world_decisions: list[ActionDecision],
        world_nodes: list[WorldNode],
        skip_ids: set[str],
    ) -> tuple[list[CharacterActionDecision], list[CharacterRecord]]:
        if not self.character_agent:
            return [], []
        polities = self._collect_polity_nodes_from_updates(world_decisions, world_nodes)
        if not polities:
            return [], []
        candidates = self._find_characters_for_polities(polities)
        if not candidates:
            return [], []
        candidate_ids = {record.identifier for record in candidates}
        prompt = self._build_polity_character_decision_prompt(
            update_info, polities, candidates
        )
        response = self._chat_once(
            prompt,
            system_prompt=self._system_prompt(),
            log_label="GAME_POLITY_CHARACTER_DECIDE",
        )
        update_ids = self._parse_character_update_ids(response, candidate_ids)
        update_ids = [item for item in update_ids if item not in skip_ids]
        if not update_ids:
            return [], []
        context = self._build_polity_update_context(
            polities, limit=DEFAULT_SEARCH_CONTEXT_LIMIT
        )
        if context:
            update_payload = f"{update_info.strip()}\n{context}"
        else:
            update_payload = update_info
        decisions: list[CharacterActionDecision] = []
        records: list[CharacterRecord] = []
        for identifier in update_ids:
            decision = CharacterActionDecision(
                flag=CHARACTER_UPDATE_TAG,
                identifier=identifier,
                raw="polity_check",
            )
            record = self.character_agent.apply_update(
                decision.flag, decision.identifier, update_payload
            )
            decisions.append(decision)
            records.append(record)
        self.logger.info(
            "polity_character_updates polities=%s candidates=%s updated=%s",
            len(polities),
            len(candidates),
            len(decisions),
        )
        return decisions, records

    def _maybe_update_characters_for_polity_removals(
        self,
        update_info: str,
        world_decisions: list[ActionDecision],
        skip_ids: set[str],
    ) -> tuple[list[CharacterActionDecision], list[CharacterRecord]]:
        if not self.character_agent:
            return [], []
        removed_polity_ids = self._collect_removed_polity_ids(world_decisions)
        if not removed_polity_ids:
            return [], []
        candidates = [
            record
            for record in self.character_agent.engine.records
            if record.polity_id in removed_polity_ids
        ]
        if not candidates:
            return [], []
        context = self._build_polity_removal_context(removed_polity_ids)
        update_payload = (
            f"{update_info.strip()}\n{context}\n"
            "请更新角色档案以反映政权被删除后的影响。"
        )
        decisions: list[CharacterActionDecision] = []
        records: list[CharacterRecord] = []
        for record in candidates:
            if record.identifier in skip_ids:
                continue
            record.polity_id = None
            decision = CharacterActionDecision(
                flag=CHARACTER_UPDATE_TAG,
                identifier=record.identifier,
                raw="polity_remove",
            )
            updated = self.character_agent.apply_update(
                decision.flag, decision.identifier, update_payload
            )
            decisions.append(decision)
            records.append(updated)
        self.logger.info(
            "polity_character_removals polities=%s candidates=%s updated=%s",
            len(removed_polity_ids),
            len(candidates),
            len(decisions),
        )
        return decisions, records

    def _read_world_nodes(
        self,
        identifiers: list[str],
        read_world: dict[str, WorldNode],
    ) -> list[WorldNode]:
        if not identifiers or not self.world_agent:
            return []
        added: list[WorldNode] = []
        for identifier in identifiers:
            if identifier in read_world:
                continue
            node = self.world_agent.engine.nodes.get(identifier)
            if not node:
                continue
            read_world[identifier] = node
            added.append(node)
        return added

    def _read_character_records(
        self,
        identifiers: list[str],
        read_characters: dict[str, CharacterRecord],
    ) -> list[CharacterRecord]:
        if not identifiers or not self.character_agent:
            return []
        added: list[CharacterRecord] = []
        record_lookup = {record.identifier: record for record in self.character_agent.engine.records}
        for identifier in identifiers:
            if identifier in read_characters:
                continue
            record = record_lookup.get(identifier)
            if not record:
                continue
            read_characters[identifier] = record
            added.append(record)
        return added

    def _resolve_world_identifiers(self, identifiers: list[str]) -> list[str]:
        if not identifiers or not self.world_agent:
            return []
        nodes = self.world_agent.engine.nodes
        resolved: list[str] = []
        key_lookup: dict[str, list[str]] = {}
        for node in nodes.values():
            key = node.key.strip()
            if not key:
                continue
            key_lookup.setdefault(key, []).append(node.identifier)
        for raw in identifiers:
            token = raw.strip()
            if not token:
                continue
            if token in nodes:
                resolved.append(token)
                continue
            matches = key_lookup.get(token) or []
            if len(matches) == 1:
                resolved.append(matches[0])
                continue
            micro_matches = [item for item in matches if self._is_micro_identifier(item)]
            if len(micro_matches) == 1:
                resolved.append(micro_matches[0])
        return resolved[:DEFAULT_SEARCH_LIMIT]

    def _resolve_character_identifiers(self, identifiers: list[str]) -> list[str]:
        if not identifiers or not self.character_agent:
            return []
        records = self.character_agent.engine.records
        id_lookup = {record.identifier: record.identifier for record in records}
        name_lookup: dict[str, list[str]] = {}
        for record in records:
            if isinstance(record.profile, dict):
                name = str(record.profile.get("name", "")).strip()
                if name:
                    name_lookup.setdefault(name, []).append(record.identifier)
        resolved: list[str] = []
        for raw in identifiers:
            token = raw.strip()
            if not token:
                continue
            if token in id_lookup:
                resolved.append(token)
                continue
            matches = name_lookup.get(token) or []
            if len(matches) == 1:
                resolved.append(matches[0])
        return resolved[:DEFAULT_SEARCH_LIMIT]

    def _coerce_id_list(self, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            return self._split_identifiers(value)
        return []

    def _split_identifiers(self, raw: str) -> list[str]:
        if not raw:
            return []
        tokens = re.split(r"[,\s，;]+", raw.strip())
        return [token for token in (item.strip() for item in tokens) if token]

    def _heuristic_search(self, update_info: str) -> tuple[list[str], list[str]]:
        text = update_info.strip()
        world_ids: list[str] = []
        if self.world_agent:
            nodes = self._iter_world_nodes_prefer_micro()
            for node in nodes:
                key = node.key.strip()
                if key and key in text:
                    world_ids.append(node.identifier)
                    if len(world_ids) >= DEFAULT_SEARCH_LIMIT:
                        break
        character_ids: list[str] = []
        if self.character_agent:
            for record in self.character_agent.engine.records:
                if record.identifier and record.identifier in text:
                    character_ids.append(record.identifier)
                    if len(character_ids) >= DEFAULT_SEARCH_LIMIT:
                        break
                if isinstance(record.profile, dict):
                    name = str(record.profile.get("name", "")).strip()
                    if name and name in text:
                        character_ids.append(record.identifier)
                        if len(character_ids) >= DEFAULT_SEARCH_LIMIT:
                            break
        return world_ids, character_ids

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
            "区域",
            "国家",
            "城市",
            "城邦",
            "王国",
            "政权",
            "势力",
            "政体",
            "战争",
            "灾难",
            "制度",
            "法律",
            "资源",
            "科技",
            "共和国",
            "联邦",
            "帝国",
            "公国",
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
