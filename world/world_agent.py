from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable, Optional

from llm_api.llm_client import LLMClient
from world.world_engine import WorldEngine, WorldNode

ADD_TAG = "<|ADD_NODE|>"
UPDATE_TAG = "<|UPDATE_NODE|>"


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
        self.llm_client = llm_client or engine.llm_client or LLMClient()

    def extract_info(self, query: str) -> str:
        prompt = self._build_extract_prompt(query)
        response = self.llm_client.chat_once(prompt, system_prompt=self._system_prompt())
        key = self._parse_query_key(response)
        if not key:
            return "无相关信息"
        node = self._find_node_by_key(key)
        if not node or not node.value.strip():
            return "无相关信息"
        return node.value

    def decide_action(self, update_info: str) -> ActionDecision:
        prompt = self._build_decision_prompt(update_info)
        response = self.llm_client.chat_once(prompt, system_prompt=self._system_prompt())
        flag, index = self._parse_decision(response)
        if index not in self.engine.nodes:
            raise ValueError(f"Node {index} not found for decision")
        return ActionDecision(flag=flag, index=index, raw=response)

    def apply_update(self, flag: str, index: str, update_info: str) -> WorldNode:
        normalized = self._normalize_flag(flag)
        if normalized == ADD_TAG:
            return self._apply_add(index, update_info)
        if normalized == UPDATE_TAG:
            return self._apply_update(index, update_info)
        raise ValueError(f"Unknown flag: {flag}")

    # Prompt builders -----------------------------------------------------
    def _build_extract_prompt(self, query: str) -> str:
        lines = [
            "【任务】选择查询节点",
            "从下列 key 中选择最相关的一项。",
            "只输出 key 本身，不要输出其他内容。",
            "如果没有相关信息，只输出：无相关信息。",
            f"查询：{query.strip()}",
            "可用 key：",
        ]
        for node in self._iter_nodes():
            lines.append(f"- {node.key}")
        return "\n".join(lines)

    def _parse_query_key(self, response: str) -> str:
        cleaned = response.strip().strip("\"'")
        if cleaned in {"无相关信息", "无"}:
            return ""
        keys = [node.key for node in self._iter_nodes()]
        if cleaned in keys:
            return cleaned
        for key in keys:
            if key and key in cleaned:
                return key
        return ""

    def _find_node_by_key(self, key: str) -> Optional[WorldNode]:
        for node in self._iter_nodes():
            if node.key == key:
                return node
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
                "只输出更新后的节点内容，不要解释。",
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
        response = self.llm_client.chat_once(prompt, system_prompt=self._system_prompt())
        content = response.strip()
        self.engine.update_node_content(index, content)
        return node

    def _apply_add(self, index: str, update_info: str) -> WorldNode:
        parent = self.engine.view_node(index)
        prompt = self._build_add_prompt(parent, update_info)
        response = self.llm_client.chat_once(prompt, system_prompt=self._system_prompt())
        key, content = self._parse_key_and_value(response, update_info)
        child_key = self._choose_child_key(parent)
        node = self.engine.add_child(parent.identifier, child_key, key)
        node.value = content
        return node

    # Helpers -------------------------------------------------------------
    def _iter_nodes(self) -> Iterable[WorldNode]:
        return sorted(self.engine.nodes.values(), key=lambda item: item.identifier)

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
