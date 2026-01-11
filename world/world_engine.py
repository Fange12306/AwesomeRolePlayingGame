from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from llm_api.llm_client import LLMClient
from world.world_prompt import (
    DEFAULT_WORLD_SPEC,
    MICRO_POLITY_ASPECTS,
    MICRO_POLITY_DESCRIPTION,
    MICRO_REGION_DESCRIPTION,
    MICRO_REGIONS,
    WorldPromptBuilder,
)


@dataclass
class WorldNode:
    identifier: str
    title: str
    description: str = ""
    value: str = ""
    parent: Optional["WorldNode"] = None
    children: Dict[str, "WorldNode"] = field(default_factory=dict)

    def add_child(self, node: "WorldNode") -> None:
        self.children[node.identifier] = node
        node.parent = self

    @property
    def has_children(self) -> bool:
        return bool(self.children)


class WorldEngine:
    def __init__(
        self,
        world_md_path: Optional[str] = None,
        world_spec_text: Optional[str] = None,
        llm_client: Optional[LLMClient] = None,
    ) -> None:
        self.world_md_path = Path(world_md_path) if world_md_path else None
        self.root = WorldNode(identifier="world", title="World")
        self.macro = WorldNode(identifier="macro", title="Macro")
        self.micro = WorldNode(identifier="micro", title="Micro")
        self.root.add_child(self.macro)
        self.root.add_child(self.micro)
        self.nodes: Dict[str, WorldNode] = {
            "world": self.root,
            "macro": self.macro,
            "micro": self.micro,
        }
        self.llm_client = llm_client or LLMClient()

        spec_text = self._load_spec_text(world_spec_text)
        self._load_world_spec(spec_text)
        self._seed_micro_structure()

    # Public API -----------------------------------------------------------------
    def view_node(self, identifier: str) -> WorldNode:
        return self._require_node(identifier)

    def view_children(self, identifier: str) -> List[WorldNode]:
        node = self._require_node(identifier)
        return sorted(node.children.values(), key=lambda item: item.identifier)

    def add_child(
        self,
        parent_identifier: str,
        child_key: str,
        title: str,
        description: str = "",
    ) -> WorldNode:
        parent = self._require_node(parent_identifier)
        child_identifier = (
            child_key
            if parent_identifier == "world"
            else f"{parent_identifier}.{child_key}"
        )
        if child_identifier in self.nodes:
            raise ValueError(f"Node {child_identifier} already exists")

        child_node = WorldNode(
            identifier=child_identifier, title=title, description=description
        )
        parent.add_child(child_node)
        self.nodes[child_identifier] = child_node
        return child_node

    def add_node(
        self,
        identifier: str,
        title: str,
        parent_identifier: Optional[str] = None,
        description: str = "",
    ) -> WorldNode:
        if identifier in self.nodes:
            raise ValueError(f"Node {identifier} already exists")

        parent_id = parent_identifier or self._infer_parent_id(identifier)
        parent_node = self._ensure_node(parent_id)
        new_node = WorldNode(
            identifier=identifier, title=title, description=description
        )
        parent_node.add_child(new_node)
        self.nodes[identifier] = new_node
        return new_node

    def update_node_content(self, identifier: str, value: str) -> None:
        node = self._require_node(identifier)
        node.value = value

    def generate_world(
        self,
        user_pitch: str,
        regenerate: bool = False,
        progress_callback: Optional[Callable[[WorldNode, int, int], None]] = None,
    ) -> Dict[str, str]:
        self.root.value = user_pitch
        generated: Dict[str, str] = {}
        nodes = self._iter_nodes(skip_root=True)
        total = len(nodes)
        completed = 0
        for node in nodes:
            if node.value and not regenerate:
                generated[node.identifier] = node.value
                completed += 1
                if progress_callback:
                    progress_callback(node, completed, total)
                continue

            parent_value = node.parent.value if node.parent else ""
            extra_context = self._build_micro_context(node)
            prompt = WorldPromptBuilder.build_node_prompt(
                user_pitch=user_pitch,
                node=node,
                parent_value=parent_value,
                extra_context=extra_context,
            )
            node.value = self.llm_client.chat_once(
                prompt,
                system_prompt=WorldPromptBuilder.system_prompt(),
                log_label="WORLD",
            )
            generated[node.identifier] = node.value
            completed += 1
            if progress_callback:
                progress_callback(node, completed, total)

        return generated

    def save_snapshot(self, output_path: str | Path) -> None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.as_dict()
        path.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )

    def apply_snapshot(self, snapshot: Dict[str, Dict[str, str]]) -> None:
        for identifier, node_data in snapshot.items():
            title = node_data.get("title", identifier)
            description = node_data.get("description", "")
            value = node_data.get("value", "")

            if identifier == "world":
                self.root.title = title
                self.root.description = description
                self.root.value = value
                continue

            if identifier in self.nodes:
                node = self.nodes[identifier]
                node.title = title
                node.description = description
                node.value = value
            else:
                node = self.add_node(identifier, title, description=description)
                node.value = value

    @classmethod
    def from_snapshot(
        cls, snapshot_path: str | Path, llm_client: Optional[LLMClient] = None
    ) -> "WorldEngine":
        path = Path(snapshot_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        engine = cls(world_md_path=None, llm_client=llm_client)
        engine.apply_snapshot(payload)
        return engine

    def as_dict(self) -> Dict[str, Dict[str, str]]:
        payload: Dict[str, Dict[str, str]] = {}
        for node in self._iter_nodes():
            payload[node.identifier] = {
                "title": node.title,
                "description": node.description,
                "value": node.value,
                "children": list(node.children.keys()),
            }
        return payload

    # Internal helpers -----------------------------------------------------------
    def _load_world_spec(self, spec_text: str) -> None:
        lines = [line.strip() for line in spec_text.splitlines()]
        current_node = self.root
        for line in lines:
            if not line:
                continue
            parsed = self._parse_line_as_node(line)
            if parsed:
                identifier, title = parsed
                parent_id = self._infer_parent_id(identifier)
                parent_node = self._ensure_node(parent_id)
                node = self.nodes.get(identifier)
                if node:
                    if node.title.startswith("Placeholder"):
                        node.title = title
                    elif node.title != title:
                        node.title = title
                        node.description = ""
                    if node.parent is None or node.parent.identifier != parent_node.identifier:
                        parent_node.add_child(node)
                    current_node = node
                    continue

                node = WorldNode(identifier=identifier, title=title, parent=parent_node)
                parent_node.add_child(node)
                self.nodes[identifier] = node
                current_node = node
            else:
                target = current_node if current_node else self.root
                target.description = (
                    f"{target.description}\n{line}"
                    if target.description
                    else line
                )

    def _load_spec_text(self, override: Optional[str]) -> str:
        if override:
            return override
        if self.world_md_path and self.world_md_path.exists():
            return self.world_md_path.read_text(encoding="utf-8")
        return DEFAULT_WORLD_SPEC

    def _build_micro_uniqueness_context(self, node: WorldNode) -> Optional[str]:
        if not node.identifier.startswith("micro."):
            return None
        if not node.parent:
            return None

        parts = node.identifier.split(".")
        label = None
        if len(parts) == 2:
            label = "已生成大地区"
        elif len(parts) == 3 and parts[2].startswith("p"):
            label = "已生成政权"
        else:
            return None

        siblings = [
            sibling
            for sibling in node.parent.children.values()
            if sibling.identifier != node.identifier and sibling.value
        ]
        if not siblings:
            return None

        lines = [f"{label}（避免重复）："]
        for sibling in sorted(siblings, key=lambda item: item.identifier):
            lines.append(f"- {sibling.identifier} {sibling.title}: {sibling.value}")
        return "\n".join(lines)

    def _build_macro_context(self) -> Optional[str]:
        if not self.macro.children:
            return None

        lines = ["Macro 内容（用于微观设定参考）："]

        def dfs(node: WorldNode) -> None:
            content = node.value or node.description
            if content:
                lines.append(f"- {node.identifier} {node.title}: {content}")
            for child in sorted(node.children.values(), key=lambda item: item.identifier):
                dfs(child)

        dfs(self.macro)
        return "\n".join(lines) if len(lines) > 1 else None

    def _build_micro_context(self, node: WorldNode) -> Optional[str]:
        if not node.identifier.startswith("micro."):
            return None

        parts: list[str] = []
        macro_context = self._build_macro_context()
        if macro_context:
            parts.append(macro_context)

        uniqueness_context = self._build_micro_uniqueness_context(node)
        if uniqueness_context:
            parts.append(uniqueness_context)

        return "\n\n".join(parts) if parts else None

    def _seed_micro_structure(self) -> None:
        if self.micro.children:
            return

        def add_if_missing(
            parent_id: str, child_key: str, title: str, description: str = ""
        ) -> WorldNode:
            identifier = (
                child_key if parent_id == "world" else f"{parent_id}.{child_key}"
            )
            if identifier in self.nodes:
                return self.nodes[identifier]
            return self.add_child(parent_id, child_key, title, description=description)

        for index, region_title in enumerate(MICRO_REGIONS, start=1):
            region_key = f"r{index}"
            region_node = add_if_missing(
                "micro",
                region_key,
                region_title,
                description=MICRO_REGION_DESCRIPTION,
            )
            for polity_index in range(1, 3):
                polity_key = f"p{polity_index}"
                polity_node = add_if_missing(
                    region_node.identifier,
                    polity_key,
                    f"政权{polity_index}",
                    description=MICRO_POLITY_DESCRIPTION,
                )
                for aspect_key, aspect_title, aspect_desc in MICRO_POLITY_ASPECTS:
                    add_if_missing(
                        polity_node.identifier,
                        aspect_key,
                        aspect_title,
                        description=aspect_desc,
                    )

    def _parse_line_as_node(self, line: str) -> Optional[tuple[str, str]]:
        cn_match = re.match(
            r"^[^0-9A-Za-z\u4e00-\u9fff]*第([一二三四五六七八九十]+)维度[:：]?\s*(.*)$",
            line,
        )
        if cn_match:
            number = self._chinese_numeral_to_int(cn_match.group(1))
            if number is not None:
                title = cn_match.group(2).strip() or f"维度{number}"
                return str(number), title

        numeric_match = re.match(r"^(\d+(?:\.\d+)*)\s+(.*)$", line)
        if numeric_match:
            identifier = numeric_match.group(1)
            title = numeric_match.group(2).strip()
            return identifier, title
        return None

    def _infer_parent_id(self, identifier: str) -> str:
        if "." not in identifier:
            if identifier in {"world", "macro", "micro"}:
                return "world"
            return "macro"
        return identifier.rsplit(".", 1)[0]

    def _ensure_node(self, identifier: str) -> WorldNode:
        if identifier in self.nodes:
            return self.nodes[identifier]
        if identifier == "world":
            return self.root

        parent_id = self._infer_parent_id(identifier)
        parent_node = self._ensure_node(parent_id)
        placeholder = WorldNode(
            identifier=identifier, title=f"Placeholder {identifier}"
        )
        parent_node.add_child(placeholder)
        self.nodes[identifier] = placeholder
        return placeholder

    def _iter_nodes(self, skip_root: bool = False) -> Iterable[WorldNode]:
        ordered: List[WorldNode] = []

        def dfs(node: WorldNode) -> None:
            ordered.append(node)
            for child in sorted(node.children.values(), key=lambda item: item.identifier):
                dfs(child)

        dfs(self.root)
        return [node for node in ordered if not (skip_root and node is self.root)]

    def _require_node(self, identifier: str) -> WorldNode:
        if identifier not in self.nodes:
            raise KeyError(f"Node {identifier} not found")
        return self.nodes[identifier]

    def _chinese_numeral_to_int(self, text: str) -> Optional[int]:
        mapping = {
            "一": 1,
            "二": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
            "十": 10,
        }
        if text == "十":
            return 10
        if len(text) == 1:
            return mapping.get(text)
        if text.startswith("十"):
            return 10 + mapping.get(text[1], 0)
        if text.endswith("十"):
            return mapping.get(text[0], 0) * 10
        if len(text) == 2:
            return mapping.get(text[0], 0) * 10 + mapping.get(text[1], 0)
        return None
