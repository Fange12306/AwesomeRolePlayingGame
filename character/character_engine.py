from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from itertools import cycle
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from llm_api.llm_client import LLMClient
from character.character_prompt import (
    CharacterPromptBuilder,
    LocationRelationPromptBuilder,
    RelationPromptBuilder,
)


@dataclass
class CharacterRequest:
    total: int


@dataclass
class MountPoint:
    region_id: Optional[str]
    region_title: str = ""
    region_value: str = ""
    polity_id: Optional[str] = None
    polity_title: str = ""
    polity_value: str = ""


@dataclass
class CharacterBlueprint:
    identifier: str
    region_id: Optional[str]
    polity_id: Optional[str]


@dataclass
class CharacterRecord:
    identifier: str
    region_id: Optional[str]
    polity_id: Optional[str]
    profile: Dict[str, object] | str

    def to_dict(self) -> Dict[str, object]:
        return {
            "id": self.identifier,
            "region_id": self.region_id,
            "polity_id": self.polity_id,
            "profile": self.profile,
        }


class CharacterEngine:
    def __init__(
        self,
        world_snapshot: Optional[Dict[str, Dict[str, object]]] = None,
        world_snapshot_path: Optional[str | Path] = None,
        llm_client: Optional[LLMClient] = None,
    ) -> None:
        self.world_snapshot_path = Path(world_snapshot_path) if world_snapshot_path else None
        self.world_snapshot = self._load_world_snapshot(world_snapshot)
        self.llm_client = llm_client or LLMClient()
        self.records: List[CharacterRecord] = []
        self.relations: List[Dict[str, object]] = []
        self.location_edges: List[Dict[str, object]] = []

    @classmethod
    def from_world_snapshot(
        cls, snapshot_path: str | Path, llm_client: Optional[LLMClient] = None
    ) -> "CharacterEngine":
        return cls(world_snapshot_path=snapshot_path, llm_client=llm_client)

    def set_world_snapshot(self, snapshot: Dict[str, Dict[str, object]]) -> None:
        self.world_snapshot = snapshot

    def extract_mount_points(self) -> List[MountPoint]:
        micro = self.world_snapshot.get("micro")
        if not micro:
            return []

        mount_points: List[MountPoint] = []
        region_ids = [
            child_id
            for child_id in micro.get("children", [])
            if child_id in self.world_snapshot
        ]
        for region_id in region_ids:
            region_node = self.world_snapshot.get(region_id, {})
            region_title = str(region_node.get("title", ""))
            region_value = str(region_node.get("value", ""))
            polity_ids = [
                child_id
                for child_id in region_node.get("children", [])
                if child_id in self.world_snapshot
            ]

            if not polity_ids:
                mount_points.append(
                    MountPoint(
                        region_id=region_id,
                        region_title=region_title,
                        region_value=region_value,
                    )
                )
                continue

            for polity_id in polity_ids:
                polity_node = self.world_snapshot.get(polity_id, {})
                mount_points.append(
                    MountPoint(
                        region_id=region_id,
                        region_title=region_title,
                        region_value=region_value,
                        polity_id=polity_id,
                        polity_title=str(polity_node.get("title", "")),
                        polity_value=str(polity_node.get("value", "")),
                    )
                )
        return mount_points

    def build_blueprints(
        self, request: CharacterRequest, mount_points: Optional[List[MountPoint]] = None
    ) -> List[CharacterBlueprint]:
        if request.total <= 0:
            return []

        mount_points = mount_points or self.extract_mount_points()

        mount_cycle: Iterable[Optional[MountPoint]]
        if mount_points:
            mount_cycle = cycle(mount_points)
        else:
            mount_cycle = cycle([None])

        blueprints: List[CharacterBlueprint] = []
        for index in range(request.total):
            mount = next(mount_cycle)
            blueprints.append(
                CharacterBlueprint(
                    identifier=f"c{index + 1}",
                    region_id=mount.region_id if mount else None,
                    polity_id=mount.polity_id if mount else None,
                )
            )
        return blueprints

    def generate_characters(
        self, request: CharacterRequest, regenerate: bool = False
    ) -> List[CharacterRecord]:
        if self.records and not regenerate:
            return self.records

        mount_points = self.extract_mount_points()
        mount_lookup = {
            (mount.region_id, mount.polity_id): mount for mount in mount_points
        }
        world_outline = self._build_world_outline()
        blueprints = self.build_blueprints(request, mount_points)

        records: List[CharacterRecord] = []
        for blueprint in blueprints:
            mount_key = (blueprint.region_id, blueprint.polity_id)
            mount_point = mount_lookup.get(mount_key)
            prompt = CharacterPromptBuilder.build_prompt(
                world_outline, blueprint, mount_point=mount_point
            )
            output = self.llm_client.chat_once(
                prompt, system_prompt=CharacterPromptBuilder.system_prompt()
            )
            self._log_llm_call(prompt, output, label="CHARACTER")
            profile = self._parse_profile(output)
            record = CharacterRecord(
                identifier=blueprint.identifier,
                region_id=blueprint.region_id,
                polity_id=blueprint.polity_id,
                profile=profile,
            )
            records.append(record)

        self.records = records
        return records

    def generate_relations(
        self, records: Optional[List[CharacterRecord]] = None
    ) -> List[Dict[str, object]]:
        records = records or self.records
        if not records:
            return []

        character_lines = [self._summarize_character(record) for record in records]
        prompt = RelationPromptBuilder.build_prompt(character_lines)
        output = self.llm_client.chat_once(
            prompt, system_prompt=RelationPromptBuilder.system_prompt()
        )
        self._log_llm_call(prompt, output, label="RELATION")
        relations = self._parse_relations(output)
        self.relations = relations
        return relations

    def save_snapshot(
        self, output_path: str | Path, records: Optional[List[CharacterRecord]] = None
    ) -> None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "world_snapshot_path": str(self.world_snapshot_path)
            if self.world_snapshot_path
            else "",
            "characters": [record.to_dict() for record in (records or self.records)],
            "relations": self.relations,
            "character_location_edges": self.location_edges,
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )

    def _load_world_snapshot(
        self, override: Optional[Dict[str, Dict[str, object]]]
    ) -> Dict[str, Dict[str, object]]:
        if override is not None:
            return override
        if self.world_snapshot_path and self.world_snapshot_path.exists():
            return json.loads(self.world_snapshot_path.read_text(encoding="utf-8"))
        return {}

    def _build_world_outline(self) -> str:
        if not self.world_snapshot:
            return "未提供世界快照。"

        lines: List[str] = []
        world_node = self.world_snapshot.get("world", {})
        world_value = str(world_node.get("value", "")).strip()
        if world_value:
            lines.append(f"世界初始设定：{world_value}")

        macro_node = self.world_snapshot.get("macro", {})
        macro_children = macro_node.get("children", []) if macro_node else []
        for child_id in macro_children:
            child = self.world_snapshot.get(child_id, {})
            title = str(child.get("title", "")).strip()
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

    def _log_llm_call(self, prompt: str, output: str, label: str) -> None:
        log_path = Path("log") / "llm.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().isoformat(timespec="seconds")
        entry = (
            f"---{timestamp}---\n"
            f"TYPE: {label}\n"
            "PROMPT:\n"
            f"{prompt}\n"
            "OUTPUT:\n"
            f"{output}\n"
        )
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(entry)

    def generate_location_edges(
        self, records: Optional[List[CharacterRecord]] = None, regenerate: bool = False
    ) -> List[Dict[str, object]]:
        if self.location_edges and not regenerate:
            return self.location_edges

        records = records or self.records
        if not records:
            return []

        locations = self._collect_location_nodes()
        location_lookup = {item["id"]: item for item in locations}
        base_edges = self._build_rule_location_edges(records, location_lookup)
        base_lines = [self._summarize_location_edge(edge) for edge in base_edges]
        character_lines = [self._summarize_character(record) for record in records]
        location_lines = [self._summarize_location(item) for item in locations]

        prompt = LocationRelationPromptBuilder.build_prompt(
            character_lines, location_lines, base_lines
        )
        output = self.llm_client.chat_once(
            prompt, system_prompt=LocationRelationPromptBuilder.system_prompt()
        )
        self._log_llm_call(prompt, output, label="LOCATION_RELATION")
        extra_edges = self._parse_location_relations(output)
        merged = self._merge_location_edges(
            base_edges, extra_edges, location_lookup, {r.identifier for r in records}
        )
        self.location_edges = merged
        return merged

    def _collect_location_nodes(self) -> List[Dict[str, str]]:
        micro = self.world_snapshot.get("micro")
        if not micro:
            return []

        locations: List[Dict[str, str]] = []
        queue = [
            child_id
            for child_id in micro.get("children", [])
            if child_id in self.world_snapshot
        ]
        seen = set(queue)
        while queue:
            node_id = queue.pop(0)
            node = self.world_snapshot.get(node_id, {})
            location_type = self._infer_location_type(node_id)
            if location_type:
                locations.append(
                    {
                        "id": node_id,
                        "title": str(node.get("title", "")),
                        "value": str(node.get("value", "")),
                        "location_type": location_type,
                    }
                )
            for child_id in node.get("children", []):
                if child_id in self.world_snapshot and child_id not in seen:
                    seen.add(child_id)
                    queue.append(child_id)
        return locations

    def _infer_location_type(self, identifier: str) -> Optional[str]:
        if not identifier.startswith("micro."):
            return None
        parts = identifier.split(".")
        if len(parts) < 2:
            return None
        if len(parts) == 2:
            if parts[1].startswith("r"):
                return "region"
            return "subregion"
        if len(parts) >= 3:
            last = parts[-1]
            if last in self._location_excluded_keys():
                return None
            if len(parts) == 3 and parts[2].startswith("p"):
                return "polity"
            return "subregion"
        return None

    def _location_excluded_keys(self) -> set[str]:
        return {
            "culture",
            "politics",
            "economy",
            "resources",
            "geography",
            "population",
        }

    def _build_rule_location_edges(
        self,
        records: List[CharacterRecord],
        location_lookup: Dict[str, Dict[str, str]],
    ) -> List[Dict[str, object]]:
        edges: List[Dict[str, object]] = []
        for record in records:
            if record.region_id:
                edges.append(
                    self._make_location_edge(
                        record.identifier,
                        record.region_id,
                        relation_type="origin",
                        intensity=0.8,
                        source="rule",
                        location_lookup=location_lookup,
                    )
                )
            if record.polity_id:
                edges.append(
                    self._make_location_edge(
                        record.identifier,
                        record.polity_id,
                        relation_type="affiliation",
                        intensity=0.6,
                        source="rule",
                        location_lookup=location_lookup,
                    )
                )
        return edges

    def _make_location_edge(
        self,
        character_id: str,
        location_id: str,
        relation_type: str,
        intensity: float,
        source: str,
        location_lookup: Dict[str, Dict[str, str]],
    ) -> Dict[str, object]:
        location = location_lookup.get(location_id)
        location_type = (
            location.get("location_type") if location else self._infer_location_type(location_id)
        )
        return {
            "character_id": character_id,
            "location_id": location_id,
            "location_type": location_type or "subregion",
            "relation_type": relation_type,
            "intensity": intensity,
            "since": "",
            "cause": "",
            "source": source,
        }

    def _parse_location_relations(self, output: str) -> List[Dict[str, object]]:
        cleaned = output.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`").strip()
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start >= 0 and end > start:
            fragment = cleaned[start : end + 1]
            try:
                data = json.loads(fragment)
                if isinstance(data, list):
                    return [item for item in data if isinstance(item, dict)]
                if isinstance(data, dict):
                    return [data]
            except json.JSONDecodeError:
                pass
        return [{"raw": output.strip()}]

    def _merge_location_edges(
        self,
        base_edges: List[Dict[str, object]],
        extra_edges: List[Dict[str, object]],
        location_lookup: Dict[str, Dict[str, str]],
        valid_character_ids: set[str],
    ) -> List[Dict[str, object]]:
        merged: List[Dict[str, object]] = []
        seen: set[tuple[str, str, str]] = set()

        for edge in base_edges:
            key = (
                str(edge.get("character_id")),
                str(edge.get("location_id")),
                str(edge.get("relation_type")),
            )
            merged.append(edge)
            seen.add(key)

        for edge in extra_edges:
            character_id = str(edge.get("character_id", "")).strip()
            location_id = str(edge.get("location_id", "")).strip()
            relation_type = str(edge.get("relation_type", "")).strip()
            if not character_id or not location_id or not relation_type:
                continue
            if character_id not in valid_character_ids:
                continue
            if location_id not in location_lookup:
                continue
            key = (character_id, location_id, relation_type)
            if key in seen:
                continue
            location = location_lookup.get(location_id, {})
            edge["character_id"] = character_id
            edge["location_id"] = location_id
            edge.setdefault("location_type", location.get("location_type", "subregion"))
            edge.setdefault("source", "llm")
            merged.append(edge)
            seen.add(key)

        return merged

    def _summarize_location(self, item: Dict[str, str]) -> str:
        summary = item.get("value", "").strip()
        if summary and len(summary) > 80:
            summary = summary[:77] + "..."
        title = item.get("title", "").strip()
        parts = [item.get("id", ""), title]
        label = f"类型:{item.get('location_type', '')}"
        if summary:
            label = f"{label} | 简述:{summary}"
        return f"- {' '.join(part for part in parts if part)} | {label}"

    def _summarize_location_edge(self, edge: Dict[str, object]) -> str:
        return (
            f"- {edge.get('character_id')} -> {edge.get('location_id')} "
            f"{edge.get('relation_type')} ({edge.get('source', '')})"
        )

    def _parse_relations(self, output: str) -> List[Dict[str, object]]:
        cleaned = output.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`").strip()
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start >= 0 and end > start:
            fragment = cleaned[start : end + 1]
            try:
                data = json.loads(fragment)
                if isinstance(data, list):
                    return [item for item in data if isinstance(item, dict)]
                if isinstance(data, dict):
                    return [data]
            except json.JSONDecodeError:
                pass
        return [{"raw": output.strip()}]

    def _summarize_character(self, record: CharacterRecord) -> str:
        name = ""
        summary = ""
        faction = ""
        profession = ""
        species = ""
        tier = ""
        if isinstance(record.profile, dict):
            name = str(record.profile.get("name", "")).strip()
            summary = str(record.profile.get("summary", "")).strip()
            faction = str(record.profile.get("faction", "")).strip()
            profession = str(record.profile.get("profession", "")).strip()
            species = str(record.profile.get("species", "")).strip()
            tier = str(record.profile.get("tier", "")).strip()

        parts = [record.identifier]
        if name:
            parts.append(name)
        labels = []
        if faction:
            labels.append(f"阵营:{faction}")
        if profession:
            labels.append(f"职业:{profession}")
        if species:
            labels.append(f"种族:{species}")
        if tier:
            labels.append(f"层级:{tier}")
        if record.region_id:
            labels.append(f"区域:{record.region_id}")
        if record.polity_id:
            labels.append(f"政体:{record.polity_id}")
        if summary:
            labels.append(f"简述:{summary}")
        label_text = " | ".join(labels)
        return f"- {' '.join(parts)} | {label_text}" if label_text else f"- {' '.join(parts)}"
