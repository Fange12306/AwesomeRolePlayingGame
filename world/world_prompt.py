from __future__ import annotations


DEFAULT_WORLD_SPEC = """
第一维度：世界基石 (World Foundation)
决定世界的总体运行逻辑

1.1 核心设定
世界由哪些最基本的假设构成？（科技/魔法/神话/模拟等）
1.2 绝对法则
世界中不可违背的底线是什么？（生命、能量、因果等）
1.3 代价与限制
力量或资源的使用代价与极限是什么？

第二维度：空间与舞台 (World Stage)
决定世界的总体样貌与边界

2.1 世界形态
世界是行星、群岛、位面、巨构，还是其他结构？
2.2 主要地貌
最典型的环境是什么？（海洋/荒漠/都市/森林等）
2.3 边界与未知
阻挡探索的边界或未知区域是什么？

第三维度：文明与历史 (Civilization & History)
决定世界的时代感与厚度

3.1 文明阶段
整体处于原始、扩张、繁荣还是衰退？
3.2 历史关键点
决定现状的历史事件是什么？
3.3 遗产与创伤
这些事件留下的遗产与伤痕是什么？

第四维度：权力与结构 (Power & Structure)
决定统治秩序与冲突来源

4.1 主导势力
谁在掌权？如何维持统治？
4.2 对立力量
反对者或挑战者是谁？他们的诉求是什么？
4.3 资源与分配
最稀缺的资源是什么？社会如何分配？

第五维度：文化与日常 (Culture & Daily Life)
决定世界的氛围与真实感

5.1 核心价值观
社会普遍崇尚或畏惧的是什么？
5.2 日常生活
人们如何生活、工作、娱乐与交流？
5.3 禁忌与仪式
有哪些禁忌、仪式或传统塑造行为？

第六维度：主题与冲突 (Themes & Conflicts)
决定世界的故事张力

6.1 主线冲突
最核心的冲突是什么？
6.2 次级冲突
围绕主冲突产生的次级矛盾有哪些？
6.3 未来走向
世界正在走向何种方向？
""".strip()

MICRO_POLITY_ASPECTS = [
    ("culture", "文化"),
    ("economy", "经济"),
    ("politics", "政治"),
    ("population", "人口"),
    ("geography", "地理"),
    ("technology", "技术"),
    ("resources", "资源"),
]


class WorldPromptBuilder:
    @staticmethod
    def system_prompt() -> str:
        return (
            "You are an experienced narrative architect who turns loose ideas into "
            "coherent world-building notes. Keep answers concise and concrete."
        )

    @staticmethod
    def build_macro_prompt(
        user_pitch: str,
        node_identifier: str,
        node_key: str,
        hint: str = "",
        parent_value: str = "",
    ) -> str:
        hint_text = hint.strip() or "无"
        parent_block = f"\n父节点内容:\n{parent_value.strip()}\n" if parent_value.strip() else ""
        return (
            "【任务】生成宏观设定\n"
            f"世界观初稿：{user_pitch.strip()}\n\n"
            f"目标节点：{node_identifier} {node_key}\n"
            f"节点提示：{hint_text}\n"
            f"{parent_block}"
            "输出要求：生成内容要基于设定，但若无明确说明时则严格按现实世界情况填写，不能虚构内容。"
            "直接输出该节点的设定内容，不要加标题或解释。"
        )

    @staticmethod
    def build_region_list_prompt(
        user_pitch: str,
        macro_summary: str,
        min_count: int,
        max_count: int,
        retry_note: str = "",
    ) -> str:
        retry_block = f"\n注意：{retry_note}\n" if retry_note else ""
        return (
            "【任务】生成微观地区名称列表\n"
            f"世界观初稿：{user_pitch.strip()}\n\n"
            f"宏观设定摘要：\n{macro_summary.strip()}\n\n"
            f"要求：生成 {min_count}-{max_count} 个地区名称，地区需为大洲/大陆级别。\n"
            "约束：生成内容要基于设定，但若无明确说明时则严格按现实世界情况填写，不能虚构内容。\n"
            "输出格式：严格 JSON 数组，例如 [\"地区A\", \"地区B\"]。\n"
            f"{retry_block}"
            "只输出 JSON 数组，不要附加说明。"
        )

    @staticmethod
    def build_polity_list_prompt(
        user_pitch: str,
        macro_summary: str,
        region_key: str,
        all_regions: list[str],
        min_count: int,
        max_count: int,
        retry_note: str = "",
    ) -> str:
        retry_block = f"\n注意：{retry_note}\n" if retry_note else ""
        region_text = "、".join(all_regions) if all_regions else "无"
        return (
            "【任务】生成地区内的政权名称列表\n"
            f"世界观初稿：{user_pitch.strip()}\n\n"
            f"宏观设定摘要：\n{macro_summary.strip()}\n\n"
            f"已生成地区：{region_text}\n"
            f"目标地区：{region_key}\n\n"
            f"要求：生成 {min_count}-{max_count} 个政权名称。\n"
            "约束：生成内容要基于设定，但若无明确说明时则严格按现实世界情况填写，不能虚构内容。\n"
            "输出格式：严格 JSON 数组，例如 [\"政权A\", \"政权B\"]。\n"
            f"{retry_block}"
            "只输出 JSON 数组，不要附加说明。"
        )

    @staticmethod
    def build_macro_summary_prompt(user_pitch: str, macro_outline: str) -> str:
        return (
            "【任务】生成宏观总结\n"
            f"世界观初稿：{user_pitch.strip()}\n\n"
            f"宏观设定详情：\n{macro_outline.strip()}\n\n"
            "输出要求：生成内容要基于设定，但若无明确说明时则严格按现实世界情况填写，不能虚构内容。"
            "总结为简短条目或短段落，用于微观生成上下文，不要添加标题或解释。"
        )

    @staticmethod
    def build_micro_value_prompt(
        macro_summary: str,
        parent_keys_context: str,
        target_path: str,
        target_key: str,
    ) -> str:
        return (
            "【任务】生成微观节点内容\n"
            f"宏观设定摘要：\n{macro_summary.strip()}\n\n"
            f"{parent_keys_context.strip()}\n\n"
            f"需要生成：{target_path} 中 {target_key} 对应的 value\n"
            "说明：同一地区内不同政权的内容允许存在相似之处，但需保持一致性。\n"
            "输出要求：生成内容要基于设定，但若无明确说明时则严格按现实世界情况填写，不能虚构内容。"
            "仅生成该目标 key 对应的 value（描述），不要输出其他节点内容。"
            "直接输出该节点的具体内容，不要添加标题或解释。"
        )
