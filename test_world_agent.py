from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

from llm_api.llm_client import LLMClient
from world.world_agent import ADD_TAG, WorldAgent
from world.world_engine import WorldEngine


@dataclass
class TestResult:
    name: str
    success: bool
    detail: str = ""


def find_latest_snapshot() -> Path | None:
    world_root = Path("save") / "world"
    if world_root.exists():
        snapshots = list(world_root.glob("*.json"))
        if snapshots:
            return max(snapshots, key=lambda item: item.stat().st_mtime)

    root = Path("save")
    if not root.exists():
        return None
    snapshots = [
        path
        for path in root.glob("*.json")
        if path.name.startswith("world_")
    ]
    if not snapshots:
        return None
    return max(snapshots, key=lambda item: item.stat().st_mtime)


def summarize_results(title: str, results: List[TestResult]) -> None:
    total = len(results)
    passed = sum(1 for result in results if result.success)
    rate = (passed / total * 100) if total else 0.0
    print(f"\n[{title}] {passed}/{total} ({rate:.1f}%)")
    for index, result in enumerate(results, start=1):
        status = "PASS" if result.success else "FAIL"
        detail = f" - {result.detail}" if result.detail else ""
        print(f"{index}. {status}: {result.name}{detail}")


def choose_query_targets(engine: WorldEngine) -> List[Tuple[str, str]]:
    regions = engine.view_children("micro") if "micro" in engine.nodes else []
    if not regions:
        return []

    targets: List[Tuple[str, str]] = []
    for region in regions:
        polities = engine.view_children(region.identifier)
        if not polities:
            continue
        for polity in polities:
            aspects = engine.view_children(polity.identifier)
            if not aspects:
                continue
            for aspect in aspects:
                expected = (aspect.value or "").strip()
                if not expected:
                    continue
                query = f"查询{region.key}地区{polity.key}政权的{aspect.key}内容"
                targets.append((query, expected))
        if targets:
            break

    if not targets:
        return []
    random.shuffle(targets)
    return targets[:3]


def run_query_tests(agent: WorldAgent, engine: WorldEngine) -> List[TestResult]:
    results: List[TestResult] = []
    targets = choose_query_targets(engine)
    if not targets:
        return [TestResult("query", False, "no_query_targets")]

    for query, expected in targets:
        try:
            response = agent.extract_info(query).strip()
        except Exception as exc:
            results.append(TestResult(query, False, f"exception: {exc}"))
            continue
        success = bool(response and response == expected)
        if success:
            detail = "matched"
        else:
            resp_snip = response[:80].replace("\n", " ") if response else ""
            exp_snip = expected[:80].replace("\n", " ") if expected else ""
            detail = (
                "mismatch "
                f"(expected_len={len(expected)}, got_len={len(response)}) "
                f"expected='{exp_snip}' got='{resp_snip}'"
            )
        results.append(TestResult(query, success, detail))
    return results


def run_add_tests(agent: WorldAgent, engine: WorldEngine) -> List[TestResult]:
    results: List[TestResult] = []
    if "micro" not in engine.nodes:
        return [TestResult("add", False, "missing_micro_root")]
    for index in range(1, 4):
        update_info = f"新增节点测试{index}，名称：测试新增{index}"
        try:
            node = agent.apply_update(
                ADD_TAG,
                "micro",
                f"剧情信息：这是测试新增内容{index}。",
            )
        except Exception as exc:
            results.append(TestResult(f"add-{index}", False, f"exception: {exc}"))
            continue
        success = node.key.startswith("测试新增") and bool(node.value.strip())
        if success:
            detail = f"node={node.identifier}"
        else:
            reason = "key_mismatch" if node and not node.key.startswith("测试新增") else "empty_value"
            detail = f"{reason}; node={node.identifier}"
        results.append(TestResult(f"add-{index}", success, detail))
    return results


def run_update_tests(agent: WorldAgent, engine: WorldEngine) -> List[TestResult]:
    results: List[TestResult] = []
    updatable = [
        node
        for node in engine.nodes.values()
        if node.identifier not in {"world", "macro", "micro"} and node.value.strip()
    ]
    if not updatable:
        return [TestResult("update", False, "no_updatable_nodes")]

    random.shuffle(updatable)
    for index in range(1, 4):
        target = updatable[(index - 1) % len(updatable)]
        update_info = f"补充{target.key}细节 {index}"
        try:
            decision = agent.decide_action(update_info)
            node = agent.apply_update(
                decision.flag,
                decision.index,
                f"剧情信息：追加说明{index}。",
            )
        except Exception as exc:
            results.append(TestResult(f"update-{index}", False, f"exception: {exc}"))
            continue
        success = bool(node.value.strip())
        if success:
            detail = f"node={node.identifier}"
        else:
            detail = f"empty_value; node={node.identifier}"
        results.append(TestResult(f"update-{index}", success, detail))
    return results


def run_demo() -> None:
    snapshot = find_latest_snapshot()
    if not snapshot:
        print("未找到现有存档，请先生成世界快照。")
        return

    client = LLMClient()
    engine = WorldEngine.from_snapshot(snapshot, llm_client=client)
    agent = WorldAgent(engine, llm_client=client)

    print(f"使用存档：{snapshot}")

    query_results = run_query_tests(agent, engine)
    add_results = run_add_tests(agent, engine)
    update_results = run_update_tests(agent, engine)

    summarize_results("查询测试", query_results)
    summarize_results("新增测试", add_results)
    summarize_results("更新测试", update_results)

    overall = query_results + add_results + update_results
    summarize_results("总体成功率", overall)


if __name__ == "__main__":
    run_demo()
