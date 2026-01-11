from __future__ import annotations

from typing import Optional


DEFAULT_CHARACTER_SPEC = """
第一维度：身份与定位 (Identity & Role)
- 名称、外号、年龄段、性别或性别认同
- 种族/族群、职业/头衔、社会阶层/组织归属

第二维度：背景与经历 (Backstory)
- 出身与成长环境
- 关键事件/转折点

第三维度：动机与目标 (Motivation)
- 核心欲望与价值观
- 短期目标/长期目标
- 不能触碰的底线

第四维度：能力与限制 (Capabilities & Limits)
- 核心能力/技能/资源
- 明显弱点/限制/代价

第五维度：关系与立场 (Relations & Stance)
- 重要关系类型（导师/同伴/敌对）
- 对世界主冲突的立场

第六维度：性格与表现 (Personality)
- 性格关键词、说话风格、行为习惯
- 外观标识/携带物

第七维度：剧情钩子 (Hooks)
- 当前处境与行动
- 可触发的剧情线索或秘密
""".strip()

RELATION_SPEC = """
关系边表字段约束:
- source_id: 关系发起者ID
- target_id: 关系指向者ID
- type: 关系类型（mentor/ally/rival/guardian/debtor 等）
- stance: 关系立场（supportive/neutral/hostile）
- intensity: 关系强度，0-1 之间的小数
- note: 简短背景说明（不超过 20 字）
""".strip()

RELATION_EXAMPLE = """
示例:
[
  {
    "source_id": "c1",
    "target_id": "c2",
    "type": "mentor",
    "stance": "supportive",
    "intensity": 0.7,
    "note": "边境战役救过她"
  }
]
""".strip()

LOCATION_RELATION_SPEC = """
角色-地点边表字段约束:
- character_id: 角色ID
- location_id: 地点ID（必须来自地点清单）
- location_type: region/polity/subregion/settlement 之一
- relation_type: origin/residence/affiliation/mission/travel/territory
- intensity: 0-1 之间的小数
- since: 时间或阶段（短文本）
- cause: 关联原因（不超过 20 字）
""".strip()

LOCATION_RELATION_EXAMPLE = """
示例:
[
  {
    "character_id": "c1",
    "location_id": "micro.r1",
    "location_type": "region",
    "relation_type": "origin",
    "intensity": 0.8,
    "since": "少年时期",
    "cause": "出生于矿业城邦"
  }
]
""".strip()


class CharacterPromptBuilder:
    @staticmethod
    def system_prompt() -> str:
        return (
            "你是资深角色设定设计师，擅长将世界观与角色动机融合成具体设定。"
            "输出简洁、具体，不要扩写为剧情。"
        )

    @staticmethod
    def build_prompt(
        world_outline: str,
        blueprint: "CharacterBlueprint",
        mount_point: Optional["MountPoint"] = None,
    ) -> str:
        location_lines = []
        if mount_point:
            if mount_point.region_id:
                location_lines.append(
                    f"- 区域: {mount_point.region_id} {mount_point.region_title}"
                )
            if mount_point.region_value:
                location_lines.append(f"  区域说明: {mount_point.region_value}")
            if mount_point.polity_id:
                location_lines.append(
                    f"- 政体: {mount_point.polity_id} {mount_point.polity_title}"
                )
            if mount_point.polity_value:
                location_lines.append(f"  政体说明: {mount_point.polity_value}")
        location_text = "\n".join(location_lines) if location_lines else "无"

        return (
            "世界纲要（供约束与风格参考）:\n"
            f"{world_outline}\n\n"
            "角色挂载位置（region/polity）:\n"
            f"{location_text}\n\n"
            "角色标识:\n"
            f"- 角色ID: {blueprint.identifier}\n\n"
            "角色设定维度参考:\n"
            f"{DEFAULT_CHARACTER_SPEC}\n\n"
            "生成要求:\n"
            "1) 与世界设定与挂载位置保持一致，不要违背已知信息。\n"
            "2) 覆盖上述维度，内容具体但简短。\n"
            "3) 仅输出严格 JSON，不要 Markdown 或多余文本。\n"
            "4) JSON 字段固定为: name, summary, background, motivation, conflict, "
            "abilities, weaknesses, relationships, hooks, faction, profession, species, tier。\n"
            "5) tier 表示主次层级（如 main/support/extra），请合理填写。\n"
            "6) relationships 仅描述关系倾向/社交方式，不要写具体角色ID。\n"
            "7) 具体关系边表由后续流程生成，此处不输出 relations。\n"
        )


class RelationPromptBuilder:
    @staticmethod
    def system_prompt() -> str:
        return (
            "你是资深世界设定助手，负责为角色生成一致的关系网络。"
            "输出必须是严格 JSON 数组。"
        )

    @staticmethod
    def build_prompt(character_lines: list[str]) -> str:
        roster_text = "\n".join(character_lines) if character_lines else "无"
        return (
            "角色清单（仅限以下 ID）:\n"
            f"{roster_text}\n\n"
            "生成要求:\n"
            "1) 仅使用提供的角色ID，不能出现新角色。\n"
            "2) 关系为有向边，避免 self-loop。\n"
            "3) 总关系数量约为角色数量到 2 倍之间。\n"
            "4) 关系类型与立场需符合角色阵营与背景。\n"
            "5) 仅输出 JSON 数组，不要附加文本。\n\n"
            f"{RELATION_SPEC}\n\n"
            f"{RELATION_EXAMPLE}\n"
        )


class LocationRelationPromptBuilder:
    @staticmethod
    def system_prompt() -> str:
        return (
            "你是资深世界设定助手，负责生成角色与地点的关联关系。"
            "输出必须是严格 JSON 数组。"
        )

    @staticmethod
    def build_prompt(
        character_lines: list[str],
        location_lines: list[str],
        base_relation_lines: list[str],
    ) -> str:
        roster_text = "\n".join(character_lines) if character_lines else "无"
        location_text = "\n".join(location_lines) if location_lines else "无"
        base_text = "\n".join(base_relation_lines) if base_relation_lines else "无"
        return (
            "角色清单（仅限以下 ID）:\n"
            f"{roster_text}\n\n"
            "地点清单（仅限以下 ID）:\n"
            f"{location_text}\n\n"
            "已确定基础关系（不要重复）：\n"
            f"{base_text}\n\n"
            "生成要求:\n"
            "1) 仅使用提供的角色ID与地点ID。\n"
            "2) 关系为角色 -> 地点的有向边。\n"
            "3) 避免 self-loop 与重复关系。\n"
            "4) 每个角色补充 1-2 条关系即可。\n"
            "5) 仅输出 JSON 数组，不要附加文本。\n\n"
            f"{LOCATION_RELATION_SPEC}\n\n"
            f"{LOCATION_RELATION_EXAMPLE}\n"
        )
