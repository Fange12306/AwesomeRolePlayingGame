from __future__ import annotations

from typing import Optional


class WorldPromptBuilder:
    @staticmethod
    def system_prompt() -> str:
        return (
            "You are an experienced narrative architect who turns loose ideas into "
            "coherent world-building notes. Keep answers concise and concrete."
        )

    @staticmethod
    def build_node_prompt(
        user_pitch: str,
        node: "WorldNode",
        parent_value: str = "",
        extra_context: Optional[str] = None,
    ) -> str:
        description = (node.description or "").strip() or "无明确说明则严格按现实世界情况填写。"
        parent_section = (
            f"\n父节点内容：\n{parent_value.strip()}\n" if parent_value.strip() else ""
        )
        extra_section = f"\n其他上下文：\n{extra_context.strip()}\n" if extra_context else ""

        return (
            f"初始世界观（用户输入）：\n{user_pitch}\n\n"
            f"你正在完善世界节点：{node.identifier} - {node.title}\n"
            f"节点设计提示：\n{description}\n"
            f"{parent_section}"
            f"{extra_section}"
            "生成该内容的具体设定，如果是问题，直接用最短的文字回答，否则生成简短描述。直接输出设定内容。"
        )
