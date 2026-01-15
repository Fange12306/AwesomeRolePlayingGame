from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from llm_api.llm_client import LLMClient
from world.world_engine import WorldEngine, WorldNode
from world.world_prompt import MICRO_POLITY_ASPECTS

ADD_TAG = "<|ADD_NODE|>"
UPDATE_TAG = "<|UPDATE_NODE|>"
DEFAULT_LOG_PATH = Path("log") / "world_agent.log"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(filename)s:%(lineno)d %(message)s"


def _truncate_text(text: str, limit: int = 800) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...<truncated {len(text) - limit} chars>"


def _get_logger() -> logging.Logger:
    logger = logging.getLogger("world_agent")
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
class ActionDecision:
    flag: str
    index: str
    raw: str


class WorldAgent:
    def __init__(
        self, engine: WorldEngine, llm_client: Optional[LLMClient] = None
    ) -> None:
        self.engine = engine
        self.logger = _get_logger()
        try:
            self.llm_client = llm_client or engine.llm_client or LLMClient()
        except Exception:
            self.logger.exception("init world_agent llm_client failed nodes=%s", len(engine.nodes))
            raise
        self.logger.info("init world_agent nodes=%s", len(self.engine.nodes))

    def extract_info(self, query: str) -> str:
        try:
            prompt = self._build_extract_prompt(query)
            response = self._chat_once(
                prompt, system_prompt=self._system_prompt(), log_label="EXTRACT"
            )
            identifier = self._parse_query_identifier(response)
            if not identifier:
                self.logger.info("extract_info miss query_len=%s", len(query))
                return "无相关信息"
            node = self.engine.nodes.get(identifier)
            if not node or not node.value.strip():
                self.logger.info("extract_info empty id=%s", identifier)
                return "无相关信息"
            self.logger.info(
                "extract_info hit id=%s value_len=%s", identifier, len(node.value)
            )
            return node.value
        except Exception:
            self.logger.exception("extract_info failed query_len=%s", len(query))
            raise

    def decide_action(self, update_info: str) -> ActionDecision:
        response = ""
        try:
            prompt = self._build_decision_prompt(update_info)
            response = self._chat_once(
                prompt, system_prompt=self._system_prompt(), log_label="DECIDE"
            )
            flag, index = self._parse_decision(response)
            if index not in self.engine.nodes:
                raise ValueError(f"Node {index} not found for decision")
            self.logger.info(
                "decide_action flag=%s index=%s info_len=%s",
                flag,
                index,
                len(update_info),
            )
            return ActionDecision(flag=flag, index=index, raw=response)
        except Exception:
            self.logger.exception(
                "decide_action failed info_len=%s response=%s",
                len(update_info),
                _truncate_text(response),
            )
            raise

    def apply_update(self, flag: str, index: str, update_info: str) -> WorldNode:
        try:
            normalized = self._normalize_flag(flag)
            if normalized == ADD_TAG:
                node = self._apply_add(index, update_info)
                self.logger.info(
                    "apply_update add parent=%s child=%s", index, node.identifier
                )
                return node
            if normalized == UPDATE_TAG:
                node = self._apply_update(index, update_info)
                self.logger.info("apply_update update index=%s", index)
                return node
            raise ValueError(f"Unknown flag: {flag}")
        except Exception:
            self.logger.exception(
                "apply_update failed flag=%s index=%s info_len=%s",
                flag,
                index,
                len(update_info),
            )
            raise

    def add_polity(self, region_identifier: str, polity_name: str) -> WorldNode:
        try:
            name = polity_name.strip()
            if not name:
                raise ValueError("polity_name is required")
            region_id = self._resolve_region_id(region_identifier)
            region = self.engine.view_node(region_id)
            self._require_micro_region(region)
            polity_key = self._choose_polity_key(region)
            polity = self.engine.add_child(region.identifier, polity_key, name)
            for aspect_id, aspect_key in MICRO_POLITY_ASPECTS:
                self.engine.add_child(polity.identifier, aspect_id, aspect_key)
            self.logger.info(
                "add_polity region=%s polity=%s id=%s",
                region.identifier,
                polity.key,
                polity.identifier,
            )
            return polity
        except Exception:
            self.logger.exception(
                "add_polity failed region=%s polity=%s",
                region_identifier,
                polity_name,
            )
            raise

    def remove_polity(
        self, polity_identifier: str, region_identifier: Optional[str] = None
    ) -> list[str]:
        try:
            polity_id = self._resolve_polity_id(polity_identifier, region_identifier)
            polity = self.engine.view_node(polity_id)
            if not self._is_micro_polity(polity):
                raise ValueError(f"Node {polity_id} is not a micro polity")
            removed = self.engine.remove_node(polity_id)
            self.logger.info(
                "remove_polity id=%s removed=%s", polity_id, len(removed)
            )
            return removed
        except Exception:
            self.logger.exception(
                "remove_polity failed polity=%s region=%s",
                polity_identifier,
                region_identifier,
            )
            raise

    # Prompt builders -----------------------------------------------------
    def _build_extract_prompt(self, query: str) -> str:
        lines = [
            "【任务】选择查询节点",
            "从下列编号中选择最相关的一项。",
            "只输出编号本身，不要输出其他内容。",
            "如果没有相关信息，只输出：无相关信息。",
            f"查询：{query.strip()}",
            "可用编号：",
        ]
        for node in self._iter_nodes():
            lines.append(f"- {node.identifier} {node.key}")
        return "\n".join(lines)

    def _parse_query_identifier(self, response: str) -> str:
        cleaned = response.strip().strip("\"'")
        if cleaned in {"无相关信息", "无"}:
            return ""
        identifiers = [node.identifier for node in self._iter_nodes()]
        if cleaned in identifiers:
            return cleaned
        for identifier in identifiers:
            if identifier and identifier in cleaned:
                return identifier
        return None

    def _build_decision_prompt(self, update_info: str) -> str:
        lines = [
            "【任务】判断更新操作",
            "你需要决定是新增节点还是修改节点。",
            "输出必须包含两处冗余，且只输出两行：",
            f"1) {ADD_TAG}:INDEX 或 {UPDATE_TAG}:INDEX",
            '2) {"action":"ADD_NODE"|"UPDATE_NODE","index":"INDEX"}',
            "INDEX 必须是已有节点的标识。",
            f"剧情信息：{update_info.strip()}",
            "可用节点：",
        ]
        for node in self._iter_nodes():
            lines.append(f"- {node.identifier} {node.key}")
        return "\n".join(lines)

    def _build_update_prompt(self, node: WorldNode, update_info: str) -> str:
        original = (node.value or "").strip() or "无"
        return "\n".join(
            [
                "【任务】更新节点内容",
                "根据相关信息，重新编写节点内容，只输出更新后的节点内容，不要解释。",
                f"节点：{node.identifier} {node.key}",
                f"剧情信息：{update_info.strip()}",
                f"原节点内容：{original}",
            ]
        )

    def _build_add_prompt(self, parent: WorldNode, update_info: str) -> str:
        parent_content = (parent.value or "").strip() or "无"
        siblings = [child.key for child in parent.children.values()]
        sibling_text = "、".join(siblings) if siblings else "无"
        return "\n".join(
            [
                "【任务】新增子节点内容",
                "只输出两行，格式如下：",
                "<|KEY|>:新节点名称",
                "<|VALUE|>:新节点内容",
                f"父节点：{parent.identifier} {parent.key}",
                f"父节点内容：{parent_content}",
                f"已有子节点名称：{sibling_text}",
                f"剧情信息：{update_info.strip()}",
            ]
        )

    # Core actions --------------------------------------------------------
    def _apply_update(self, index: str, update_info: str) -> WorldNode:
        node = self.engine.view_node(index)
        prompt = self._build_update_prompt(node, update_info)
        response = self._chat_once(
            prompt, system_prompt=self._system_prompt(), log_label="UPDATE_NODE"
        )
        content = response.strip()
        self.engine.update_node_content(index, content)
        return node

    def _apply_add(self, index: str, update_info: str) -> WorldNode:
        parent = self.engine.view_node(index)
        prompt = self._build_add_prompt(parent, update_info)
        response = self._chat_once(
            prompt, system_prompt=self._system_prompt(), log_label="ADD_NODE"
        )
        key, content = self._parse_key_and_value(response, update_info)
        child_key = self._choose_child_key(parent)
        node = self.engine.add_child(parent.identifier, child_key, key)
        node.value = content
        return node

    # Helpers -------------------------------------------------------------
    def _iter_nodes(self) -> Iterable[WorldNode]:
        return sorted(self.engine.nodes.values(), key=lambda item: item.identifier)

    def _resolve_region_id(self, region_identifier: str) -> str:
        if region_identifier in self.engine.nodes:
            return region_identifier
        if "micro" not in self.engine.nodes:
            raise KeyError("micro root not found")
        for region in self.engine.view_children("micro"):
            if region.key == region_identifier:
                return region.identifier
        raise KeyError(f"Region {region_identifier} not found")

    def _resolve_polity_id(
        self, polity_identifier: str, region_identifier: Optional[str]
    ) -> str:
        if polity_identifier in self.engine.nodes:
            return polity_identifier
        name = polity_identifier.strip()
        if not name:
            raise ValueError("polity_identifier is required")
        if "micro" not in self.engine.nodes:
            raise KeyError("micro root not found")
        regions: list[WorldNode]
        if region_identifier:
            region_id = self._resolve_region_id(region_identifier)
            region = self.engine.view_node(region_id)
            self._require_micro_region(region)
            regions = [region]
        else:
            regions = self.engine.view_children("micro")
        matches = [
            child.identifier
            for region in regions
            for child in region.children.values()
            if child.key == name
        ]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise KeyError(f"Polity {polity_identifier} not found")
        raise ValueError(
            f"Multiple polities named {polity_identifier}; specify region_identifier"
        )

    def _require_micro_region(self, region: WorldNode) -> None:
        if region.identifier == "micro":
            raise ValueError("Region identifier must not be micro root")
        if not region.parent or region.parent.identifier != "micro":
            raise ValueError(f"Node {region.identifier} is not a micro region")

    def _is_micro_polity(self, polity: WorldNode) -> bool:
        if not polity.parent:
            return False
        return bool(polity.parent.parent and polity.parent.parent.identifier == "micro")

    def _normalize_flag(self, flag: str) -> str:
        candidate = flag.strip()
        if candidate in {ADD_TAG, "ADD_NODE"}:
            return ADD_TAG
        if candidate in {UPDATE_TAG, "UPDATE_NODE"}:
            return UPDATE_TAG
        return candidate

    def _parse_decision(self, response: str) -> tuple[str, str]:
        tag_match = re.search(
            r"<\|(ADD_NODE|UPDATE_NODE)\|>\s*[:：]\s*([^\s]+)",
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
            index = str(data.get("index", "")).strip()
            if action in {"ADD_NODE", "UPDATE_NODE"} and index:
                return f"<|{action}|>", index

        raise ValueError(f"Unable to parse decision from response: {response}")

    def _parse_key_and_value(
        self, response: str, update_info: str
    ) -> tuple[str, str]:
        key = None
        content_lines: list[str] = []
        capture_content = False
        for line in response.splitlines():
            key_match = re.match(r"<\|KEY\|>\s*[:：]?\s*(.*)", line)
            if key_match and key is None:
                key = key_match.group(1).strip()
                continue
            content_match = re.match(r"<\|VALUE\|>\s*[:：]?\s*(.*)", line)
            if content_match:
                capture_content = True
                content_lines.append(content_match.group(1).strip())
                continue
            if capture_content:
                content_lines.append(line.strip())

        content = "\n".join(line for line in content_lines if line).strip()
        if not content:
            content = response.strip()

        if not key:
            key = self._infer_key(update_info) or "新节点"

        return key, content

    def _infer_key(self, update_info: str) -> str:
        match = re.search(
            r"(?:名称|name|节点名称|节点名|key)[:：]\s*([^\n]+)",
            update_info,
            re.IGNORECASE,
        )
        if match:
            return match.group(1).strip()[:30]
        hint = update_info.strip().splitlines()[0] if update_info.strip() else ""
        if hint:
            return hint[:30]
        return "新节点"

    def _choose_child_key(self, parent: WorldNode) -> str:
        existing = {child.identifier.split(".")[-1] for child in parent.children.values()}
        if not existing:
            return "1"

        numeric = [int(key) for key in existing if key.isdigit()]
        if numeric:
            return self._increment_key(str(max(numeric) + 1), existing)

        prefixed: dict[str, dict[str, int]] = {}
        for key in existing:
            match = re.match(r"([A-Za-z_]+)(\d+)$", key)
            if not match:
                continue
            prefix, number = match.groups()
            stats = prefixed.setdefault(prefix, {"count": 0, "max": 0})
            stats["count"] += 1
            stats["max"] = max(stats["max"], int(number))

        if prefixed:
            prefix = max(
                prefixed,
                key=lambda item: (prefixed[item]["count"], prefixed[item]["max"]),
            )
            base = f"{prefix}{prefixed[prefix]['max'] + 1}"
            return self._increment_key(base, existing)

        base = "new1"
        return self._increment_key(base, existing)

    def _choose_polity_key(self, region: WorldNode) -> str:
        existing = {child.identifier.split(".")[-1] for child in region.children.values()}
        return self._increment_key("p1", existing)

    def _increment_key(self, base: str, existing: set[str]) -> str:
        if base.isdigit():
            number = int(base)
            while str(number) in existing:
                number += 1
            return str(number)

        match = re.match(r"([A-Za-z_]+)(\d+)$", base)
        if match:
            prefix, number = match.groups()
            counter = int(number)
            while f"{prefix}{counter}" in existing:
                counter += 1
            return f"{prefix}{counter}"

        candidate = base
        counter = 1
        while candidate in existing:
            candidate = f"{base}{counter}"
            counter += 1
        return candidate

    def _system_prompt(self) -> str:
        return (
            "You are a precise world-building assistant. "
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
