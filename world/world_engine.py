from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from llm_api.llm_client import LLMClient
from world.world_prompt import (
    DEFAULT_WORLD_SPEC,
    MICRO_POLITY_ASPECTS,
    WorldPromptBuilder,
)

DEFAULT_SPEC_PATH = Path(__file__).resolve().parent / "world_spec.md"
DEFAULT_LOG_PATH = Path("log") / "world_engine.log"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(filename)s:%(lineno)d %(message)s"


def _truncate_text(text: str, limit: int = 800) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...<truncated {len(text) - limit} chars>"


def _get_logger() -> logging.Logger:
    logger = logging.getLogger("world_engine")
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


@dataclass
class WorldNode:
    identifier: str
    key: str
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
        world_spec_path: Optional[str] = None,
        world_spec_text: Optional[str] = None,
        user_pitch: Optional[str] = None,
        llm_client: Optional[LLMClient] = None,
        auto_generate: bool = True,
        progress_callback: Optional[Callable[[WorldNode, int, int], None]] = None,
        max_retries: int = 2,
    ) -> None:
        self.logger = _get_logger()
        self.world_spec_path = (
            Path(world_spec_path)
            if world_spec_path
            else DEFAULT_SPEC_PATH
        )
        self.root = WorldNode(identifier="world", key="World")
        self.macro = WorldNode(identifier="macro", key="Macro")
        self.micro = WorldNode(identifier="micro", key="Micro")
        self.root.add_child(self.macro)
        self.root.add_child(self.micro)
        self.nodes: Dict[str, WorldNode] = {
            "world": self.root,
            "macro": self.macro,
            "micro": self.micro,
        }
        try:
            self.llm_client = llm_client or LLMClient()
        except Exception:
            self.logger.exception(
                "init world_engine llm_client failed world_spec_path=%s user_pitch_len=%s",
                self.world_spec_path,
                len(user_pitch or ""),
            )
            raise
        self.user_pitch = user_pitch or ""
        self.max_retries = max_retries
        self.macro_summary = ""
        if self.user_pitch:
            self.root.value = self.user_pitch

        self.logger.info(
            "init world_engine auto_generate=%s user_pitch_len=%s",
            auto_generate,
            len(self.user_pitch),
        )

        spec_text = self._load_spec_text(world_spec_text)
        self.spec_nodes, self.spec_hints = self._parse_world_spec(spec_text)
        self._load_macro_nodes(self.spec_nodes)

        if auto_generate and self.user_pitch:
            self.generate_world(
                self.user_pitch,
                progress_callback=progress_callback,
            )

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
        key: str,
    ) -> WorldNode:
        parent = self._require_node(parent_identifier)
        if parent_identifier in {"world", "macro"}:
            child_identifier = child_key
        else:
            child_identifier = f"{parent_identifier}.{child_key}"
        if child_identifier in self.nodes:
            raise ValueError(f"Node {child_identifier} already exists")

        child_node = WorldNode(identifier=child_identifier, key=key)
        parent.add_child(child_node)
        self.nodes[child_identifier] = child_node
        return child_node

    def add_node(
        self,
        identifier: str,
        key: str,
        parent_identifier: Optional[str] = None,
    ) -> WorldNode:
        if identifier in self.nodes:
            raise ValueError(f"Node {identifier} already exists")

        parent_id = parent_identifier or self._infer_parent_id(identifier)
        parent_node = self._ensure_node(parent_id)
        new_node = WorldNode(identifier=identifier, key=key)
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
        max_retries: Optional[int] = None,
    ) -> Dict[str, str]:
        self.user_pitch = user_pitch
        self.root.value = user_pitch
        retries = max_retries if max_retries is not None else self.max_retries
        try:
            self.logger.info(
                "generate_world start regenerate=%s retries=%s pitch_len=%s",
                regenerate,
                retries,
                len(user_pitch),
            )

            generated: Dict[str, str] = {}
            completed = 0

            macro_nodes = self._iter_macro_nodes()
            macro_total = len(macro_nodes)
            for node in macro_nodes:
                if node.value.strip() and not regenerate:
                    completed += 1
                    if progress_callback:
                        progress_callback(node, completed, macro_total)
                    continue
                parent_value = node.parent.value if node.parent else ""
                prompt = WorldPromptBuilder.build_macro_prompt(
                    user_pitch=user_pitch,
                    node_identifier=node.identifier,
                    node_key=node.key,
                    hint=self.spec_hints.get(node.identifier, ""),
                    parent_value=parent_value,
                )
                node.value = self._generate_text_with_retry(
                    prompt,
                    system_prompt=WorldPromptBuilder.system_prompt(),
                    log_label=f"MACRO_{node.identifier}",
                    max_retries=retries,
                )
                generated[node.identifier] = node.value
                completed += 1
                if progress_callback:
                    progress_callback(node, completed, macro_total)

            if not self.macro_summary or regenerate:
                self.macro_summary = self._generate_macro_summary(retries=retries)

            self._generate_micro_structure(
                macro_summary=self.macro_summary,
                retries=retries,
            )

            micro_nodes = self._iter_micro_nodes()
            self.logger.info(
                "generate_world macro_total=%s micro_total=%s",
                macro_total,
                len(micro_nodes),
            )
            total = len(macro_nodes) + len(micro_nodes)
            for node in micro_nodes:
                if node.value.strip() and not regenerate:
                    completed += 1
                    if progress_callback:
                        progress_callback(node, completed, total)
                    continue
                prompt = self._build_micro_value_prompt(node)
                node.value = self._generate_text_with_retry(
                    prompt,
                    system_prompt=WorldPromptBuilder.system_prompt(),
                    log_label=f"MICRO_VALUE_{node.identifier}",
                    max_retries=retries,
                )
                generated[node.identifier] = node.value
                completed += 1
                if progress_callback:
                    progress_callback(node, completed, total)

            self.logger.info(
                "generate_world done generated=%s total=%s",
                len(generated),
                total,
            )
            return generated
        except Exception:
            self.logger.exception(
                "generate_world failed regenerate=%s retries=%s pitch_len=%s",
                regenerate,
                retries,
                len(user_pitch),
            )
            raise

    def save_snapshot(self, output_path: str | Path) -> None:
        path = Path(output_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = self.as_dict()
            path.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            self.logger.info("save_snapshot path=%s nodes=%s", path, len(payload))
        except Exception:
            self.logger.exception("save_snapshot failed path=%s", path)
            raise

    def apply_snapshot(self, snapshot: Dict[str, Dict[str, str]]) -> None:
        try:
            for identifier, node_data in snapshot.items():
                key = (
                    str(node_data.get("key", ""))
                    or str(node_data.get("title", ""))
                    or identifier
                )
                value = str(node_data.get("value", ""))

                if identifier == "world":
                    self.root.key = key
                    self.root.value = value
                    self.user_pitch = value
                    continue
                if identifier == "macro":
                    self.macro.key = key
                    self.macro.value = value
                    continue
                if identifier == "micro":
                    self.micro.key = key
                    self.micro.value = value
                    continue

                if identifier in self.nodes:
                    node = self.nodes[identifier]
                    node.key = key
                    node.value = value
                else:
                    node = self.add_node(identifier, key)
                    node.value = value
            self.logger.info("apply_snapshot nodes=%s", len(snapshot))
        except Exception:
            self.logger.exception("apply_snapshot failed nodes=%s", len(snapshot))
            raise

    @classmethod
    def from_snapshot(
        cls, snapshot_path: str | Path, llm_client: Optional[LLMClient] = None
    ) -> "WorldEngine":
        path = Path(snapshot_path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger = _get_logger()
            logger.exception("from_snapshot read failed path=%s", path)
            raise
        engine = cls(world_spec_path=None, llm_client=llm_client, auto_generate=False)
        engine.apply_snapshot(payload)
        engine.logger.info("from_snapshot path=%s nodes=%s", path, len(payload))
        return engine

    def as_dict(self) -> Dict[str, Dict[str, str | List[str]]]:
        payload: Dict[str, Dict[str, str | List[str]]] = {}
        for node in self._iter_nodes():
            payload[node.identifier] = {
                "key": node.key,
                "value": node.value,
                "children": sorted(node.children.keys()),
            }
        return payload

    # Internal helpers -----------------------------------------------------------
    def _load_spec_text(self, override: Optional[str]) -> str:
        if override:
            return override
        if self.world_spec_path and self.world_spec_path.exists():
            return self.world_spec_path.read_text(encoding="utf-8")
        return DEFAULT_WORLD_SPEC

    def _parse_world_spec(
        self, spec_text: str
    ) -> tuple[List[tuple[str, str]], Dict[str, str]]:
        lines = [line.strip() for line in spec_text.splitlines()]
        nodes: List[tuple[str, str]] = []
        hints: Dict[str, str] = {}
        current_id: Optional[str] = None
        for line in lines:
            if not line:
                continue
            parsed = self._parse_line_as_node(line)
            if parsed:
                identifier, key = parsed
                nodes.append((identifier, key))
                current_id = identifier
                continue
            if current_id:
                if current_id in hints:
                    hints[current_id] = f"{hints[current_id]}\n{line}"
                else:
                    hints[current_id] = line
        return nodes, hints

    def _load_macro_nodes(self, spec_nodes: List[tuple[str, str]]) -> None:
        for identifier, key in spec_nodes:
            parent_id = self._infer_parent_id(identifier)
            parent_node = self._ensure_node(parent_id)
            node = self.nodes.get(identifier)
            if node:
                node.key = key
                if node.parent is None or node.parent.identifier != parent_node.identifier:
                    parent_node.add_child(node)
                continue
            new_node = WorldNode(identifier=identifier, key=key)
            parent_node.add_child(new_node)
            self.nodes[identifier] = new_node

    def _generate_micro_structure(self, macro_summary: str, retries: int) -> None:
        if self.micro.children:
            return
        summary = macro_summary.strip() or self._build_macro_outline(skip_empty=True)
        if not summary:
            summary = "无"
        region_names = self._generate_name_list(
            prompt_builder=lambda retry_note="": WorldPromptBuilder.build_region_list_prompt(
                user_pitch=self.user_pitch,
                macro_summary=summary,
                min_count=2,
                max_count=7,
                retry_note=retry_note,
            ),
            log_label="MICRO_REGIONS",
            retries=retries,
        )

        for index, region_name in enumerate(region_names, start=1):
            region_key = f"r{index}"
            region_node = self.add_child("micro", region_key, region_name)
            polity_names = self._generate_name_list(
                prompt_builder=lambda retry_note="", region=region_name, regions=region_names: WorldPromptBuilder.build_polity_list_prompt(
                    user_pitch=self.user_pitch,
                    macro_summary=summary,
                    region_key=region,
                    all_regions=regions,
                    min_count=2,
                    max_count=7,
                    retry_note=retry_note,
                ),
                log_label=f"MICRO_POLITIES_{region_key}",
                retries=retries,
            )
            for polity_index, polity_name in enumerate(polity_names, start=1):
                polity_key = f"p{polity_index}"
                polity_node = self.add_child(region_node.identifier, polity_key, polity_name)
                for aspect_id, aspect_key in MICRO_POLITY_ASPECTS:
                    self.add_child(polity_node.identifier, aspect_id, aspect_key)

    def _generate_name_list(
        self,
        prompt_builder: Callable[[str], str],
        log_label: str,
        retries: int,
    ) -> List[str]:
        last_error = ""
        for attempt in range(retries + 1):
            prompt = prompt_builder(last_error if attempt > 0 else "")
            response = self._chat_once(
                prompt,
                system_prompt=WorldPromptBuilder.system_prompt(),
                log_label=log_label if attempt == 0 else f"{log_label}_RETRY_{attempt}",
            )
            try:
                return self._parse_name_list(response)
            except ValueError as exc:
                last_error = str(exc)
                self.logger.warning(
                    "parse_name_list failed label=%s attempt=%s error=%s response=%s",
                    log_label,
                    attempt,
                    last_error,
                    _truncate_text(response),
                )
                continue
        raise ValueError(f"Unable to parse name list for {log_label}: {last_error}")

    def _parse_name_list(self, response: str) -> List[str]:
        cleaned = response.strip()
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start < 0 or end <= start:
            raise ValueError("missing_json_array")
        fragment = cleaned[start : end + 1]
        try:
            data = json.loads(fragment)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid_json: {exc}") from exc
        if not isinstance(data, list):
            raise ValueError("not_list")
        names = []
        seen = set()
        for item in data:
            if not isinstance(item, str):
                continue
            name = self._clean_name(item)
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            names.append(name)
        if len(names) < 2 or len(names) > 7:
            raise ValueError(f"invalid_count:{len(names)}")
        return names

    def _clean_name(self, raw: str) -> str:
        name = raw.strip()
        name = re.sub(r"^[\s\d\-\)\(\.、]+", "", name)
        return name.strip()

    def _build_macro_outline(self, skip_empty: bool = False) -> str:
        lines: List[str] = []
        def dfs(node: WorldNode) -> None:
            if node.identifier != "macro":
                value = node.value.strip()
                if value or not skip_empty:
                    label = f"{node.identifier} {node.key}".strip()
                    if value:
                        lines.append(f"- {label}: {value}")
                    else:
                        lines.append(f"- {label}")
            for child in sorted(node.children.values(), key=lambda item: item.identifier):
                dfs(child)

        dfs(self.macro)
        return "\n".join(lines) if lines else "无"

    def _build_micro_outline(self) -> str:
        lines: List[str] = []

        def dfs(node: WorldNode, depth: int) -> None:
            if node.identifier != "micro":
                value = node.value.strip()
                label = f"{node.identifier} {node.key}".strip()
                prefix = "  " * depth + "- "
                if value:
                    lines.append(f"{prefix}{label}: {value}")
                else:
                    lines.append(f"{prefix}{label}")
            for child in sorted(node.children.values(), key=lambda item: item.identifier):
                dfs(child, depth + 1)

        dfs(self.micro, 0)
        return "\n".join(lines) if lines else "无"

    def _build_micro_value_prompt(self, node: WorldNode) -> str:
        macro_summary = self.macro_summary.strip() or self._build_macro_outline(skip_empty=True)
        if not macro_summary:
            macro_summary = "无"
        parent_keys_context = self._build_micro_parent_key_context(node)
        target_path = self._build_node_path(node)
        return WorldPromptBuilder.build_micro_value_prompt(
            macro_summary=macro_summary,
            parent_keys_context=parent_keys_context,
            target_path=target_path,
            target_key=node.key,
        )

    def _build_micro_parent_key_context(self, node: WorldNode) -> str:
        parts: List[str] = []
        regions = self.view_children("micro")
        if regions:
            region_line = " ".join(
                f"{index}. {region.key}" for index, region in enumerate(regions, start=1)
            )
            parts.append("地区：")
            parts.append(region_line)

            for index, region in enumerate(regions, start=1):
                polities = self.view_children(region.identifier)
                polity_names = [polity.key for polity in polities]
                if polity_names:
                    polity_text = "; ".join(polity_names) + ";"
                else:
                    polity_text = "无;"
                parts.append(f"地区{index}政权：{polity_text}")

        parent = node.parent
        if parent and parent.identifier != "micro":
            parent_value = parent.value.strip() or "无"
            parts.append(f"{parent.key}：{parent_value}")

        if not parts:
            return "无"
        return "\n".join(parts)

    def _generate_macro_summary(self, retries: int) -> str:
        macro_outline = self._build_macro_outline(skip_empty=True)
        if not macro_outline or macro_outline == "无":
            return ""
        prompt = WorldPromptBuilder.build_macro_summary_prompt(
            user_pitch=self.user_pitch,
            macro_outline=macro_outline,
        )
        return self._generate_text_with_retry(
            prompt,
            system_prompt=WorldPromptBuilder.system_prompt(),
            log_label="MACRO_SUMMARY",
            max_retries=retries,
        )

    def _build_node_path(self, node: WorldNode) -> str:
        parts: List[str] = []
        current = node
        while current and current.identifier != "micro":
            parts.append(current.key)
            current = current.parent
        return " > ".join(reversed(parts))

    def _generate_text_with_retry(
        self,
        prompt: str,
        system_prompt: str,
        log_label: str,
        max_retries: int,
    ) -> str:
        output = self._chat_once(
            prompt,
            system_prompt=system_prompt,
            log_label=log_label,
        )
        if self._is_valid_value(output):
            return output.strip()
        for attempt in range(max_retries):
            retry_prompt = (
                f"{prompt}\n\n"
                "上次输出无效或为空，请严格按要求生成内容，仅输出节点内容。"
            )
            output = self._chat_once(
                retry_prompt,
                system_prompt=system_prompt,
                log_label=f"{log_label}_RETRY_{attempt + 1}",
            )
            if self._is_valid_value(output):
                return output.strip()
        return output.strip()

    def _is_valid_value(self, text: str) -> bool:
        cleaned = text.strip()
        if not cleaned:
            return False
        if cleaned.startswith("Error in chat_once"):
            return False
        return True

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
        if text in mapping:
            return mapping[text]
        if len(text) == 2 and text[0] == "十" and text[1] in mapping:
            return 10 + mapping[text[1]]
        if len(text) == 2 and text[1] == "十" and text[0] in mapping:
            return mapping[text[0]] * 10
        if len(text) == 3 and text[1] == "十":
            first = mapping.get(text[0])
            last = mapping.get(text[2])
            if first and last:
                return first * 10 + last
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
        if identifier == "macro":
            return self.macro
        if identifier == "micro":
            return self.micro

        parent_id = self._infer_parent_id(identifier)
        parent_node = self._ensure_node(parent_id)
        placeholder = WorldNode(identifier=identifier, key=f"Placeholder {identifier}")
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

    def _iter_macro_nodes(self) -> List[WorldNode]:
        nodes: List[WorldNode] = []
        for identifier, _ in self.spec_nodes:
            node = self.nodes.get(identifier)
            if node:
                nodes.append(node)
        return nodes

    def _iter_micro_nodes(self) -> List[WorldNode]:
        nodes: List[WorldNode] = []

        def dfs(node: WorldNode) -> None:
            if node.identifier != "micro":
                nodes.append(node)
            for child in sorted(node.children.values(), key=lambda item: item.identifier):
                dfs(child)

        dfs(self.micro)
        return nodes

    def _require_node(self, identifier: str) -> WorldNode:
        if identifier not in self.nodes:
            raise KeyError(f"Node {identifier} not found")
        return self.nodes[identifier]
