from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from world.world_engine import WorldEngine


class DummyLLMClient:
    """Lightweight stub for offline testing of world generation."""

    def __init__(self) -> None:
        self.calls = []
        self.region_attempts = 0

    def chat_once(
        self, prompt: str, system_prompt: str = "", log_label: str | None = None
    ) -> str:
        self.calls.append(log_label or "")
        if log_label and log_label.startswith("MACRO_"):
            return f"{log_label} 设定内容"
        if log_label == "MACRO_SUMMARY":
            return "宏观总结：该世界以大陆尺度划分，气候与资源分布明确。"
        if log_label == "MICRO_REGIONS":
            return json.dumps(["北大陆", "南大陆"], ensure_ascii=False)
        if log_label and log_label.startswith("MICRO_POLITIES_"):
            return json.dumps(["北境议会", "林雾教团"], ensure_ascii=False)
        if log_label and log_label.startswith("MICRO_VALUE_"):
            return f"{log_label} 设定内容"
        head = prompt.splitlines()[0][:60]
        return f"[dummy] {head}"


def choose_llm_client():
    use_real = input("是否使用真实 LLM? (默认: 否) [y/N]: ").strip().lower()
    if use_real == "y":
        from llm_api.llm_client import LLMClient

        return LLMClient()
    return DummyLLMClient()


def run_demo() -> None:
    if _load_existing_world():
        return

    pitch = input("请输入一段世界观初稿：").strip()
    if not pitch:
        pitch = "一个漂浮空岛组成的蒸汽朋克世界，能源稀缺。"
        print(f"未输入，使用默认设定：{pitch}")

    client = choose_llm_client()
    engine = WorldEngine(
        llm_client=client,
        user_pitch=pitch,
        auto_generate=True,
    )
    _run_node_tests(engine)
    _write_mindmap(engine)
    _save_snapshot(engine)

    print("\n示例节点输出：")
    for node_id in ("1", "1.1", "1.2", "2.1"):
        if node_id not in engine.nodes:
            continue
        node = engine.view_node(node_id)
        print(f"{node_id} - {node.key}: {node.value}")


def _write_mindmap(engine: WorldEngine) -> None:
    lines = ["mindmap", "  root((World))"]

    if engine.root.value:
        lines.append(f"    初始设定: {engine.root.value}")

    def walk(node, depth: int) -> None:
        indent = "  " * depth
        label = f"{node.identifier} {node.key}".strip()
        lines.append(f"{indent}{label}")

        if node.value:
            for value_line in node.value.splitlines():
                value_line = value_line.strip()
                if not value_line:
                    continue
                lines.append(f"{indent}  {value_line}")

        for child in sorted(node.children.values(), key=lambda item: item.identifier):
            walk(child, depth + 1)

    for child in sorted(engine.root.children.values(), key=lambda item: item.identifier):
        walk(child, 2)

    content = "# World Mindmap\n\n```mermaid\n" + "\n".join(lines) + "\n```\n"
    output_path = Path("docs") / "world_mindmap.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    print(f"\n已生成思维导图文档：{output_path}")


def _run_node_tests(engine: WorldEngine) -> None:
    print("\n节点操作测试：")
    sample_id = _choose_sample_node(engine)
    sample_node = engine.view_node(sample_id)
    print(f"查看节点: {sample_node.identifier} - {sample_node.key}")

    engine.update_node_content(
        sample_id, "手动设定：核心法则由蒸汽科技驱动，能量必须付出代价。"
    )
    updated_node = engine.view_node(sample_id)
    print(f"编辑节点内容: {updated_node.identifier} - {updated_node.value}")

    new_key = "4"
    new_identifier = f"2.{new_key}"
    if new_identifier in engine.nodes:
        new_key = "5"
        new_identifier = f"2.{new_key}"
    new_node = engine.add_child("2", new_key, "未来冲突")
    print(f"新增节点: {new_node.identifier} - {new_node.key}")

    children = engine.view_children("2")
    child_ids = [child.identifier for child in children]
    print(f"查看子节点(2): {child_ids}")


def _choose_sample_node(engine: WorldEngine) -> str:
    preferred = [
        "1.1",
        "1.2",
        "2.1",
        "3.1",
    ]
    for node_id in preferred:
        if node_id in engine.nodes:
            return node_id

    candidates = [
        node_id
        for node_id in engine.nodes
        if node_id not in {"world", "macro", "micro"}
    ]
    if not candidates:
        return "world"
    return sorted(candidates)[0]


def _save_snapshot(engine: WorldEngine) -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path("save") / "world" / f"world_{timestamp}.json"
    engine.save_snapshot(output_path)
    print(f"\n已保存世界快照：{output_path}")


def _load_existing_world() -> bool:
    use_saved = input("是否读取已保存的世界? (默认: 否) [y/N]: ").strip().lower()
    if use_saved != "y":
        return False

    snapshots = _list_snapshots()
    if not snapshots:
        print("未找到已保存的世界，继续生成新世界。")
        return False

    snapshot = _choose_snapshot(snapshots)
    if not snapshot:
        print("未选择快照，继续生成新世界。")
        return False

    engine = WorldEngine.from_snapshot(snapshot, llm_client=DummyLLMClient())
    print(f"\n已读取世界快照：{snapshot}")
    _run_node_tests(engine)
    _write_mindmap(engine)
    return True


def _list_snapshots() -> list[Path]:
    folder = Path("save") / "world"
    if not folder.exists():
        return []
    snapshots = sorted(
        folder.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True
    )
    return snapshots


def _choose_snapshot(snapshots: list[Path]) -> Path | None:
    print("\n可用世界快照：")
    for index, path in enumerate(snapshots, start=1):
        print(f"{index}. {path}")

    choice = input("选择编号 (默认: 1): ").strip()
    if not choice:
        return snapshots[0]
    if not choice.isdigit():
        return None
    index = int(choice) - 1
    if index < 0 or index >= len(snapshots):
        return None
    return snapshots[index]


if __name__ == "__main__":
    run_demo()
