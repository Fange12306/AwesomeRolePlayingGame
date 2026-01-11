from __future__ import annotations

from pathlib import Path

from llm_api.llm_client import LLMClient
from world.world_agent import WorldAgent
from world.world_engine import WorldEngine


def find_latest_snapshot() -> Path | None:
    root = Path("save")
    if not root.exists():
        return None
    snapshots = list(root.rglob("*.json"))
    if not snapshots:
        return None
    return max(snapshots, key=lambda item: item.stat().st_mtime)


def run_demo() -> None:
    snapshot = find_latest_snapshot()
    if not snapshot:
        print("未找到现有存档，请先生成世界快照。")
        return

    client = LLMClient()
    engine = WorldEngine.from_snapshot(snapshot, llm_client=client)
    agent = WorldAgent(engine, llm_client=client)

    print(f"使用存档：{snapshot}")

    print("\n[提取信息]")
    extract = agent.extract_info("主导势力")
    print(extract)

    print("\n[判断操作 + 新增节点]")
    decision_add = agent.decide_action("剧情信息：新增一个关于未来走向的节点，标题：未来走向。")
    new_node = agent.apply_update(
        decision_add.flag,
        decision_add.index,
        "剧情信息：世界经历能源枯竭，社会开始分裂并形成新的迁徙潮。",
    )
    print(f"新增节点：{new_node.identifier} {new_node.title} -> {new_node.value}")

    print("\n[判断操作 + 修改节点]")
    decision_update = agent.decide_action("剧情信息：补充核心设定的细节")
    updated_node = agent.apply_update(
        decision_update.flag,
        decision_update.index,
        "剧情信息：核心力量会消耗持有者寿命，且必须以特定仪式启动。",
    )
    print(f"修改节点：{updated_node.identifier} {updated_node.title} -> {updated_node.value}")


if __name__ == "__main__":
    run_demo()
