from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

from llm_api.llm_client import LLMClient
from world.world_agent import ADD_TAG, WorldAgent
from world.world_engine import WorldEngine
from world.world_prompt import MICRO_POLITY_ASPECTS


ORG_NAMES = [
    "监察",
    "联络",
    "外务",
    "后勤",
    "安全",
    "资源",
    "财政",
    "情报",
    "行政",
]

CHANGES = [
    "人员调整",
    "权力重组",
    "预算削减",
    "战略转向",
    "合并撤销",
    "内部改革",
    "职能扩编",
]


@dataclass
class TestResult:
    name: str
    success: bool
    detail: str = ""
    expected_output: str = ""
    actual_output: str = ""


class RecordingLLMClient:
    def __init__(self, inner: LLMClient) -> None:
        self.inner = inner
        self.calls: list[dict[str, str]] = []

    def chat_once(
        self, prompt: str, system_prompt: str = "", log_label: str | None = None
    ) -> str:
        output = self.inner.chat_once(
            prompt, system_prompt=system_prompt, log_label=log_label
        )
        self.calls.append(
            {
                "label": log_label or "",
                "prompt": prompt,
                "system_prompt": system_prompt,
                "output": output,
            }
        )
        return output

    def last_output(self, label: str) -> str:
        for call in reversed(self.calls):
            if call["label"] == label:
                return call["output"]
        return ""


def _snippet(text: str, limit: int = 200) -> str:
    cleaned = text.replace("\n", " ").strip()
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[:limit - 3]}..."


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
        if not result.success and result.detail:
            print(f"   reason: {result.detail}")
        if not result.success and (result.expected_output or result.actual_output):
            expected = _snippet(result.expected_output) if result.expected_output else "N/A"
            actual = _snippet(result.actual_output) if result.actual_output else "N/A"
            print(f"   expected_llm: {expected}")
            print(f"   actual_llm: {actual}")


def choose_query_targets(engine: WorldEngine) -> List[Tuple[str, str, str]]:
    regions = engine.view_children("micro") if "micro" in engine.nodes else []
    if not regions:
        return []

    targets: List[Tuple[str, str, str]] = []
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
                targets.append((query, expected, aspect.identifier))
        if targets:
            break

    if not targets:
        return []
    random.shuffle(targets)
    return targets[:3]


def pick_random_micro_keys(engine: WorldEngine) -> Tuple[str, str, str] | None:
    if "micro" not in engine.nodes:
        return None
    candidates: List[Tuple[str, str, str]] = []
    regions = engine.view_children("micro")
    for region in regions:
        polities = engine.view_children(region.identifier)
        if not polities:
            continue
        for polity in polities:
            aspects = engine.view_children(polity.identifier)
            if not aspects:
                continue
            for aspect in aspects:
                candidates.append((region.key, polity.key, aspect.key))
    if not candidates:
        return None
    return random.choice(candidates)


def run_query_tests(
    agent: WorldAgent, engine: WorldEngine, recorder: RecordingLLMClient
) -> List[TestResult]:
    results: List[TestResult] = []
    targets = choose_query_targets(engine)
    if not targets:
        return [TestResult("query", False, "no_query_targets")]

    for query, expected, expected_id in targets:
        try:
            response = agent.extract_info(query).strip()
        except Exception as exc:
            results.append(
                TestResult(
                    query,
                    False,
                    f"exception: {exc}",
                    expected_output=expected_id,
                    actual_output=recorder.last_output("EXTRACT"),
                )
            )
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
        results.append(
            TestResult(
                query,
                success,
                detail,
                expected_output=expected_id,
                actual_output=recorder.last_output("EXTRACT"),
            )
        )
    return results


def run_add_tests(
    agent: WorldAgent, engine: WorldEngine, recorder: RecordingLLMClient
) -> List[TestResult]:
    results: List[TestResult] = []
    if "micro" not in engine.nodes:
        return [TestResult("add", False, "missing_micro_root")]
    existing_ids = set(engine.nodes)
    for index in range(1, 4):
        context = pick_random_micro_keys(engine)
        if not context:
            results.append(
                TestResult(
                    f"add-{index}",
                    False,
                    "missing_micro_keys",
                    expected_output="<|KEY|>:<name>\\n<|VALUE|>:<content>",
                    actual_output="N/A",
                )
            )
            continue
        region_key, polity_key, _ = context
        org_name = random.choice(ORG_NAMES)
        update_info = f"{region_key}地区{polity_key}政权新增了{org_name}机构"
        try:
            node = agent.apply_update(
                ADD_TAG,
                "micro",
                update_info,
            )
        except Exception as exc:
            results.append(TestResult(f"add-{index}", False, f"exception: {exc}"))
            continue
        is_new = node.identifier not in existing_ids
        has_value = bool(node.value.strip())
        success = is_new and has_value
        if success:
            detail = f"node={node.identifier}; region={region_key}; polity={polity_key}; org={org_name}"
        else:
            reason = "duplicate_id" if not is_new else "empty_value"
            detail = (
                f"{reason}; node={node.identifier}; key='{node.key}'; "
                f"value_len={len(node.value.strip())}; region={region_key}; "
                f"polity={polity_key}; org={org_name}"
            )
        existing_ids.add(node.identifier)
        results.append(
            TestResult(
                f"add-{index}",
                success,
                detail,
                expected_output="<|KEY|>:<name>\\n<|VALUE|>:<content>",
                actual_output=recorder.last_output("ADD_NODE"),
            )
        )
    return results


def run_polity_tests(agent: WorldAgent, engine: WorldEngine) -> List[TestResult]:
    results: List[TestResult] = []
    if "micro" not in engine.nodes:
        return [TestResult("polity", False, "missing_micro_root")]
    regions = engine.view_children("micro")
    if not regions:
        return [TestResult("polity", False, "no_regions")]

    region = random.choice(regions)
    polity_name = f"新政权{random.randint(1000, 9999)}"
    existing_ids = set(engine.nodes)
    try:
        polity = agent.add_polity(region.identifier, polity_name)
    except Exception as exc:
        results.append(TestResult("polity-add", False, f"exception: {exc}"))
        results.append(TestResult("polity-remove", False, "skip_add_failed"))
        return results

    aspects = engine.view_children(polity.identifier)
    aspect_ids = {child.identifier for child in aspects}
    expected_ids = {
        f"{polity.identifier}.{aspect_id}" for aspect_id, _ in MICRO_POLITY_ASPECTS
    }
    missing_ids = expected_ids - aspect_ids
    extra_ids = aspect_ids - expected_ids
    parent_ok = bool(polity.parent and polity.parent.identifier == region.identifier)
    success = (
        polity.identifier not in existing_ids
        and polity.identifier in engine.nodes
        and polity.key == polity_name
        and parent_ok
        and not missing_ids
        and not extra_ids
    )
    if success:
        detail = (
            f"id={polity.identifier}; region={region.identifier}; "
            f"aspects={len(aspects)}"
        )
    else:
        detail = (
            f"id={polity.identifier}; region={region.identifier}; "
            f"missing={len(missing_ids)} extra={len(extra_ids)} parent_ok={parent_ok}"
        )
    results.append(TestResult("polity-add", success, detail))

    try:
        removed = agent.remove_polity(polity.identifier)
    except Exception as exc:
        results.append(TestResult("polity-remove", False, f"exception: {exc}"))
        return results

    removed_ids = set(removed)
    expected_removed = expected_ids | {polity.identifier}
    missing_removed = expected_removed - removed_ids
    still_present = any(node_id in engine.nodes for node_id in expected_removed)
    parent_has_child = polity.identifier in region.children
    success = not missing_removed and not still_present and not parent_has_child
    detail = (
        f"removed={len(removed)} missing={len(missing_removed)} "
        f"still_present={still_present}"
    )
    results.append(TestResult("polity-remove", success, detail))
    return results


def run_update_tests(
    agent: WorldAgent, engine: WorldEngine, recorder: RecordingLLMClient
) -> List[TestResult]:
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
        context = pick_random_micro_keys(engine)
        if not context:
            results.append(
                TestResult(
                    f"update-{index}",
                    False,
                    "missing_micro_keys",
                    expected_output="non-empty updated content",
                    actual_output="N/A",
                )
            )
            continue
        region_key, polity_key, aspect_key = context
        change = random.choice(CHANGES)
        update_info = f"{region_key}地区{polity_key}政权的{aspect_key}机构发生了{change}"
        try:
            decision = agent.decide_action(update_info)
            node = agent.apply_update(
                decision.flag,
                decision.index,
                f"{region_key}地区{polity_key}政权的{aspect_key}机构发生了{change}",
            )
        except Exception as exc:
            results.append(
                TestResult(
                    f"update-{index}",
                    False,
                    f"exception: {exc}",
                    expected_output="non-empty updated content",
                    actual_output=recorder.last_output("UPDATE_NODE"),
                )
            )
            continue
        success = bool(node.value.strip())
        if success:
            detail = (
                f"node={node.identifier}; region={region_key}; polity={polity_key}; "
                f"aspect={aspect_key}; change={change}"
            )
        else:
            detail = (
                f"empty_value; node={node.identifier}; key='{node.key}'; "
                f"value_len={len(node.value.strip())}; region={region_key}; "
                f"polity={polity_key}; aspect={aspect_key}; change={change}"
            )
        results.append(
            TestResult(
                f"update-{index}",
                success,
                detail,
                expected_output="non-empty updated content",
                actual_output=recorder.last_output("UPDATE_NODE"),
            )
        )
    return results


def run_demo() -> None:
    snapshot = find_latest_snapshot()
    if not snapshot:
        print("未找到现有存档，请先生成世界快照。")
        return

    recorder = RecordingLLMClient(LLMClient())
    engine = WorldEngine.from_snapshot(snapshot, llm_client=recorder)
    agent = WorldAgent(engine, llm_client=recorder)

    print(f"使用存档：{snapshot}")

    query_results = run_query_tests(agent, engine, recorder)
    add_results = run_add_tests(agent, engine, recorder)
    polity_results = run_polity_tests(agent, engine)
    update_results = run_update_tests(agent, engine, recorder)

    summarize_results("查询测试", query_results)
    summarize_results("新增测试", add_results)
    summarize_results("政权增删测试", polity_results)
    summarize_results("更新测试", update_results)

    overall = query_results + add_results + polity_results + update_results
    summarize_results("总体成功率", overall)


if __name__ == "__main__":
    run_demo()
