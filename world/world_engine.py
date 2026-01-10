from __future__ import annotations

import json
import re
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from llm_api.llm_client import LLMClient
from world.world_prompt import WorldPromptBuilder


DEFAULT_WORLD_SPEC = """
第一维度：世界基石 (The Foundation & Stage)
——决定了“我们在哪里”以及“规则是什么”

这是世界的物理容器和运行法则，是一切模拟的前提。

1.1 现实定性

1.1.1 核心法则
纯科技侧：严谨的物理规则（如：现实地球、硬科幻、末日废土）。
高魔/超凡侧：唯心力量主导（如：修仙、神话、魔法中世纪）。
混合侧：科技与神秘共存（如：赛博修真、蒸汽朋克+克苏鲁）。
1.1.2 铁律/代价
世界中绝对不可违背的一条规则是什么？（例如：人死不能复生、魔法必须等价交换、光速不可逾越）。
1.2 地理容器

1.2.1 世界形态
行星级：常规星球（如：地球、火星）。
破碎/特殊：非星球结构（如：漂浮空岛群、无尽平面、巨大宇宙飞船内部、地底空洞）。
1.2.2 主导地貌
地图上占比最大的环境是什么？（如：90%是海洋、全境沙漠化、无尽的钢铁都市、被森林覆盖）。
1.2.3 物理边界
阻挡人们探索世界尽头的是什么？（如：致命辐射带、深海、无法穿越的迷雾、宇宙真空）。
1.3 核心驱动力

1.3.1 能量来源
世界运转靠什么？（石油/电能、灵气/魔力、晶石、太阳能）。
1.3.2 资源状态
该能量是无限且廉价（冲突在于谁更强），还是枯竭且昂贵（冲突在于生存）？
第二维度：文明进程 (Civilization & History)
——决定了“世界目前处于什么阶段”

这部分决定了模拟的背景故事厚度和技术/魔法水平。

2.1 文明所处阶段

2.1.1 发展水平
原始/蒙昧：部落制，生存为主。
发展/扩张：国家建立，正在探索世界。
巅峰/繁荣：技术或魔法高度发达，生活富足。
衰退/末世：文明崩溃后，在废墟上苟延残喘。
2.1.2 技术/魔法普及度
精英垄断：只有少部分人掌握核心力量。
全民普及：力量或技术融入了普通人的日常生活。
2.2 历史转折点

2.2.1 创伤记忆
近百年内发生过的最大灾难或战争是什么？（这解释了现在的格局）。
2.2.2 遗留产物
那次事件留下了什么？（如：禁区、仇恨、某种变异生物、被遗忘的黑科技）。
第三维度：社会权力 (Power & Structure)
——决定了“谁在统治”以及“谁在受苦”

这部分构建了模拟中的NPC关系网和主要势力。

3.1 统治实体

3.1.1 最高权力机构
形式：帝国皇室、企业联盟、宗教教廷、AI中枢、军阀割据。
控制手段：依靠武力镇压、信仰洗脑、还是经济控制？
3.1.2 反对势力
谁在试图推翻统治者？（如：革命军、地下结社、异教徒）。
3.2 阶级与分配

3.2.1 阶级鸿沟
上层阶级拥有什么特权？（如：永生、飞行、无尽的资源）。
底层阶级面临什么困境？（如：饥饿、疾病、被奴役）。
3.2.2 经济媒介
一般等价物（货币）：人们用什么交易？（金银、信用点、电池、瓶盖、寿命）。
硬通货：除了钱，什么东西最值钱？（水、抗生素、武器、信息）。
第四维度：人文生态 (Culture & Lifestyle)
——决定了“世界的真实氛围”

这部分决定了模拟的沉浸感，涉及普通人的衣食住行。

4.1 信仰与禁忌

4.1.1 核心价值观
社会普遍崇尚什么？（力量、金钱、神明、理性、荣誉）。
4.1.2 绝对禁忌
普通人绝对不敢做的事是什么？（如：直呼神名、夜晚出门、进入某个区域）。
4.2 生存画风

4.2.1 建筑与美学
城市/居住地长什么样？（高耸的赛博高楼、破败的帐篷、宏伟的石质神庙、生物质生长的房屋）。
4.2.2 饮食来源
人们主要吃什么？（合成膏、自然作物、狩猎怪兽、能量块）。
""".strip()


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
        self.nodes: Dict[str, WorldNode] = {"world": self.root}
        self.llm_client = llm_client or LLMClient()

        spec_text = self._load_spec_text(world_spec_text)
        self._load_world_spec(spec_text)

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

    def generate_world(self, user_pitch: str, regenerate: bool = False) -> Dict[str, str]:
        self.root.value = user_pitch
        generated: Dict[str, str] = {}
        for node in self._iter_nodes(skip_root=True):
            if node.value and not regenerate:
                generated[node.identifier] = node.value
                continue

            parent_value = node.parent.value if node.parent else ""
            prompt = WorldPromptBuilder.build_node_prompt(
                user_pitch=user_pitch,
                node=node,
                parent_value=parent_value,
            )
            node.value = self.llm_client.chat_once(
                prompt, system_prompt=WorldPromptBuilder.system_prompt()
            )
            self._log_llm_call(prompt, node.value)
            generated[node.identifier] = node.value

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

    def _log_llm_call(self, prompt: str, output: str) -> None:
        log_path = Path("log") / "llm.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().isoformat(timespec="seconds")
        entry = (
            f"---{timestamp}---\n"
            "PROMPT：\n"
            f"{prompt}\n"
            "OUTPUT：\n"
            f"{output}\n"
        )
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(entry)

    def _parse_line_as_node(self, line: str) -> Optional[tuple[str, str]]:
        cn_match = re.match(r"^第([一二三四五六七八九十]+)维度[:：]?\s*(.*)$", line)
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
            return "world"
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
