from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from character.character_agent import CharacterAgent
from character.character_engine import CharacterEngine, CharacterRecord
from game.game_agent import GameAgent
from llm_api.llm_client import LLMClient
from world.world_agent import WorldAgent
from world.world_engine import WorldEngine


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


def find_latest_world_snapshot() -> Path | None:
    world_root = Path("save") / "world"
    if world_root.exists():
        snapshots = list(world_root.glob("*.json"))
        if snapshots:
            return max(snapshots, key=lambda item: item.stat().st_mtime)

    root = Path("save")
    if not root.exists():
        return None
    snapshots = [path for path in root.glob("world_*.json")]
    if not snapshots:
        return None
    return max(snapshots, key=lambda item: item.stat().st_mtime)


def find_latest_character_snapshot() -> Path | None:
    folder = Path("save") / "characters"
    if not folder.exists():
        return None
    snapshots = list(folder.glob("*.json"))
    if not snapshots:
        return None
    return max(snapshots, key=lambda item: item.stat().st_mtime)


def load_character_snapshot(
    snapshot_path: Path,
) -> tuple[list[CharacterRecord], Optional[Path]]:
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    records: list[CharacterRecord] = []
    for item in payload.get("characters", []):
        identifier = str(item.get("id", "")).strip()
        if not identifier:
            continue
        records.append(
            CharacterRecord(
                identifier=identifier,
                region_id=item.get("region_id"),
                polity_id=item.get("polity_id"),
                profile=item.get("profile", {}),
            )
        )
    world_path_text = str(payload.get("world_snapshot_path", "")).strip()
    world_path = Path(world_path_text) if world_path_text else None
    if world_path and not world_path.exists():
        world_path = None
    return records, world_path


def clone_records(records: list[CharacterRecord]) -> list[CharacterRecord]:
    cloned: list[CharacterRecord] = []
    for record in records:
        profile = record.profile
        if isinstance(profile, dict):
            profile = dict(profile)
        cloned.append(
            CharacterRecord(
                identifier=record.identifier,
                region_id=record.region_id,
                polity_id=record.polity_id,
                profile=profile,
            )
        )
    return cloned


def load_world_snapshot(snapshot_path: Path) -> dict[str, dict[str, object]]:
    return json.loads(snapshot_path.read_text(encoding="utf-8"))


def choose_world_targets(
    snapshot: dict[str, dict[str, object]], limit: int = 3
) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    for identifier, node in snapshot.items():
        if identifier in {"world", "macro", "micro"}:
            continue
        key = str(node.get("key", node.get("title", ""))).strip()
        if not key:
            continue
        candidates.append((identifier, key))
    if not candidates:
        return []
    random.shuffle(candidates)
    return candidates[:limit]


def choose_character_targets(
    records: list[CharacterRecord], limit: int = 3
) -> list[CharacterRecord]:
    candidates = [record for record in records if record.identifier]
    if not candidates:
        return []
    random.shuffle(candidates)
    return candidates[:limit]


def build_world_engine(
    world_snapshot: Path, llm_client: RecordingLLMClient
) -> WorldEngine:
    return WorldEngine.from_snapshot(world_snapshot, llm_client=llm_client)


def build_character_engine(
    world_snapshot: Path, records: list[CharacterRecord], llm_client: RecordingLLMClient
) -> CharacterEngine:
    engine = CharacterEngine.from_world_snapshot(world_snapshot, llm_client=llm_client)
    engine.records = clone_records(records)
    return engine


def build_agents(
    world_snapshot: Path,
    records: list[CharacterRecord],
    recorder: RecordingLLMClient,
) -> tuple[GameAgent, WorldEngine, CharacterEngine]:
    world_engine = build_world_engine(world_snapshot, recorder)
    character_engine = build_character_engine(world_snapshot, records, recorder)
    world_agent = WorldAgent(world_engine, llm_client=recorder)
    character_agent = CharacterAgent(character_engine, llm_client=recorder)
    game_agent = GameAgent(world_agent, character_agent, llm_client=recorder)
    return game_agent, world_engine, character_engine


def format_character_label(record: CharacterRecord) -> str:
    name = ""
    if isinstance(record.profile, dict):
        name = str(record.profile.get("name", "")).strip()
    return name or record.identifier


def _has_world_updates(result: GameUpdateResult) -> bool:
    nodes = result.world_nodes or ([result.world_node] if result.world_node else [])
    return any(node and node.value.strip() for node in nodes)


def _has_character_updates(result: GameUpdateResult) -> bool:
    records = (
        result.character_records
        or ([result.character_record] if result.character_record else [])
    )
    return any(record and str(record.profile).strip() for record in records)


def run_both_tests(
    world_snapshot: Path,
    world_data: dict[str, dict[str, object]],
    records: list[CharacterRecord],
    base_llm: LLMClient,
) -> list[TestResult]:
    results: list[TestResult] = []
    world_targets = choose_world_targets(world_data)
    character_targets = choose_character_targets(records)
    if not world_targets or not character_targets:
        return [TestResult("both", False, "missing_world_or_character_targets")]

    cases = list(zip(world_targets, character_targets))
    for index, ((node_id, node_key), record) in enumerate(cases, start=1):
        recorder = RecordingLLMClient(base_llm)
        game_agent, _, _ = build_agents(world_snapshot, records, recorder)
        label = format_character_label(record)
        update_info = (
            f"剧情更新：世界节点{node_id} {node_key}出现重大变化，"
            f"必须更新世界设定；角色{label}发生转折，必须更新角色档案。"
        )
        try:
            result = game_agent.apply_update(update_info)
        except Exception as exc:
            results.append(
                TestResult(
                    f"both-{index}",
                    False,
                    f"exception: {exc}",
                    expected_output="WORLD=YES; CHARACTER=YES",
                    actual_output=recorder.last_output("GAME_DECIDE"),
                )
            )
            continue
        decision = result.decision
        success = decision.update_world and decision.update_characters
        detail = (
            f"world={decision.update_world} characters={decision.update_characters}"
        )
        if success:
            if not _has_world_updates(result):
                success = False
                detail = f"world_empty; {detail}"
            if not _has_character_updates(result):
                success = False
                detail = f"character_empty; {detail}"
        results.append(
            TestResult(
                f"both-{index}",
                success,
                detail,
                expected_output="WORLD=YES; CHARACTER=YES",
                actual_output=recorder.last_output("GAME_DECIDE"),
            )
        )
    return results


def run_world_only_tests(
    world_snapshot: Path,
    world_data: dict[str, dict[str, object]],
    records: list[CharacterRecord],
    base_llm: LLMClient,
) -> list[TestResult]:
    results: list[TestResult] = []
    world_targets = choose_world_targets(world_data)
    if not world_targets:
        return [TestResult("world_only", False, "missing_world_targets")]

    for index, (node_id, node_key) in enumerate(world_targets, start=1):
        recorder = RecordingLLMClient(base_llm)
        game_agent, _, _ = build_agents(world_snapshot, records, recorder)
        update_info = (
            f"剧情更新：世界节点{node_id} {node_key}发生重大变化，"
            "需要更新世界设定，不涉及任何具体角色。"
        )
        try:
            result = game_agent.apply_update(update_info)
        except Exception as exc:
            results.append(
                TestResult(
                    f"world-only-{index}",
                    False,
                    f"exception: {exc}",
                    expected_output="WORLD=YES; CHARACTER=NO",
                    actual_output=recorder.last_output("GAME_DECIDE"),
                )
            )
            continue
        decision = result.decision
        success = decision.update_world and not decision.update_characters
        detail = (
            f"world={decision.update_world} characters={decision.update_characters}"
        )
        if success and not _has_world_updates(result):
            success = False
            detail = f"world_empty; {detail}"
        results.append(
            TestResult(
                f"world-only-{index}",
                success,
                detail,
                expected_output="WORLD=YES; CHARACTER=NO",
                actual_output=recorder.last_output("GAME_DECIDE"),
            )
        )
    return results


def run_character_only_tests(
    world_snapshot: Path,
    records: list[CharacterRecord],
    base_llm: LLMClient,
) -> list[TestResult]:
    results: list[TestResult] = []
    character_targets = choose_character_targets(records)
    if not character_targets:
        return [TestResult("character_only", False, "missing_character_targets")]

    for index, record in enumerate(character_targets, start=1):
        recorder = RecordingLLMClient(base_llm)
        game_agent, _, _ = build_agents(world_snapshot, records, recorder)
        label = format_character_label(record)
        update_info = (
            f"剧情更新：角色{label}发生重大转折，需要更新角色档案，"
            "不涉及世界设定或地理势力变化。"
        )
        try:
            result = game_agent.apply_update(update_info)
        except Exception as exc:
            results.append(
                TestResult(
                    f"character-only-{index}",
                    False,
                    f"exception: {exc}",
                    expected_output="WORLD=NO; CHARACTER=YES",
                    actual_output=recorder.last_output("GAME_DECIDE"),
                )
            )
            continue
        decision = result.decision
        success = decision.update_characters and not decision.update_world
        detail = (
            f"world={decision.update_world} characters={decision.update_characters}"
        )
        if success and not _has_character_updates(result):
            success = False
            detail = f"character_empty; {detail}"
        results.append(
            TestResult(
                f"character-only-{index}",
                success,
                detail,
                expected_output="WORLD=NO; CHARACTER=YES",
                actual_output=recorder.last_output("GAME_DECIDE"),
            )
        )
    return results


def run_skip_tests(
    world_snapshot: Path,
    records: list[CharacterRecord],
    base_llm: LLMClient,
) -> list[TestResult]:
    results: list[TestResult] = []
    prompts = [
        "剧情片段：营地闲聊，没有新增设定或角色变化。",
        "旅途中简单休整，没有世界或角色更新。",
        "日常对话，未引入新剧情节点或角色转折。",
    ]

    for index, update_info in enumerate(prompts, start=1):
        recorder = RecordingLLMClient(base_llm)
        game_agent, _, _ = build_agents(world_snapshot, records, recorder)
        try:
            result = game_agent.apply_update(update_info)
        except Exception as exc:
            results.append(
                TestResult(
                    f"skip-{index}",
                    False,
                    f"exception: {exc}",
                    expected_output="WORLD=NO; CHARACTER=NO",
                    actual_output=recorder.last_output("GAME_DECIDE"),
                )
            )
            continue
        decision = result.decision
        success = not decision.update_world and not decision.update_characters
        detail = (
            f"world={decision.update_world} characters={decision.update_characters}"
        )
        results.append(
            TestResult(
                f"skip-{index}",
                success,
                detail,
                expected_output="WORLD=NO; CHARACTER=NO",
                actual_output=recorder.last_output("GAME_DECIDE"),
            )
        )
    return results


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


def run_demo() -> None:
    world_snapshot = find_latest_world_snapshot()
    if not world_snapshot:
        print("未找到世界存档，请先生成世界快照。")
        return

    character_snapshot = find_latest_character_snapshot()
    if not character_snapshot:
        print("未找到角色存档，请先生成角色快照。")
        return
    records, linked_world = load_character_snapshot(character_snapshot)
    if not records:
        print("角色存档为空，请先生成角色快照。")
        return
    if linked_world and linked_world.exists():
        world_snapshot = linked_world

    try:
        base_llm = LLMClient()
    except Exception as exc:
        print(f"LLMClient 初始化失败: {exc}")
        return

    world_data = load_world_snapshot(world_snapshot)
    print(f"使用世界存档：{world_snapshot}")
    print(f"使用角色存档：{character_snapshot}")

    both_results = run_both_tests(world_snapshot, world_data, records, base_llm)
    world_results = run_world_only_tests(world_snapshot, world_data, records, base_llm)
    character_results = run_character_only_tests(world_snapshot, records, base_llm)
    skip_results = run_skip_tests(world_snapshot, records, base_llm)

    summarize_results("世界+角色更新", both_results)
    summarize_results("仅世界更新", world_results)
    summarize_results("仅角色更新", character_results)
    summarize_results("无需更新", skip_results)

    overall = both_results + world_results + character_results + skip_results
    summarize_results("总体成功率", overall)


if __name__ == "__main__":
    run_demo()
