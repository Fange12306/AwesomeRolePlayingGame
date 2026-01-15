from __future__ import annotations

import json
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from character.character_agent import ADD_TAG, CharacterAgent
from character.character_engine import CharacterEngine, CharacterRecord
from llm_api.llm_client import LLMClient


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


def build_engine(
    world_snapshot: Path,
    records: list[CharacterRecord],
    llm_client: RecordingLLMClient,
) -> CharacterEngine:
    engine = CharacterEngine.from_world_snapshot(world_snapshot, llm_client=llm_client)
    engine.records = clone_records(records)
    return engine


def format_profile(profile: dict[str, object] | str) -> str:
    if isinstance(profile, dict):
        return json.dumps(profile, ensure_ascii=False, separators=(",", ":"))
    return str(profile or "")


def choose_character_targets(
    records: list[CharacterRecord], limit: int = 3
) -> list[CharacterRecord]:
    candidates = [record for record in records if record.identifier]
    if not candidates:
        return []
    random.shuffle(candidates)
    return candidates[:limit]


def run_query_tests(
    agent: CharacterAgent, records: list[CharacterRecord], recorder: RecordingLLMClient
) -> list[TestResult]:
    results: list[TestResult] = []
    targets = choose_character_targets(records)
    if not targets:
        return [TestResult("query", False, "no_character_targets")]

    for record in targets:
        name = ""
        if isinstance(record.profile, dict):
            name = str(record.profile.get("name", "")).strip()
        query = f"查询角色ID:{record.identifier}"
        if name:
            query = f"{query}，姓名:{name}"
        expected_profile = format_profile(record.profile)
        try:
            response = agent.extract_info(query).strip()
        except Exception as exc:
            results.append(
                TestResult(
                    query,
                    False,
                    f"exception: {exc}",
                    expected_output=record.identifier,
                    actual_output=recorder.last_output("CHARACTER_EXTRACT"),
                )
            )
            continue
        success = bool(response and response == expected_profile)
        if success:
            detail = "matched"
        else:
            detail = (
                f"mismatch expected_len={len(expected_profile)} got_len={len(response)}"
            )
        results.append(
            TestResult(
                query,
                success,
                detail,
                expected_output=record.identifier,
                actual_output=recorder.last_output("CHARACTER_EXTRACT"),
            )
        )
    return results


def run_update_tests(
    agent: CharacterAgent, records: list[CharacterRecord], recorder: RecordingLLMClient
) -> list[TestResult]:
    results: list[TestResult] = []
    targets = choose_character_targets(records)
    if not targets:
        return [TestResult("update", False, "no_character_targets")]

    for index, record in enumerate(targets, start=1):
        name = ""
        if isinstance(record.profile, dict):
            name = str(record.profile.get("name", "")).strip()
        label = name or record.identifier
        update_info = f"剧情更新：角色{label}经历重大变故，请更新其档案。"
        try:
            decision = agent.decide_action(update_info)
            updated = agent.apply_update(decision.flag, decision.identifier, update_info)
        except Exception as exc:
            results.append(
                TestResult(
                    f"update-{index}",
                    False,
                    f"exception: {exc}",
                    expected_output="non-empty updated profile",
                    actual_output=recorder.last_output("CHARACTER_UPDATE"),
                )
            )
            continue
        profile_text = str(updated.profile).strip()
        success = bool(profile_text)
        detail = f"id={updated.identifier}; flag={decision.flag}"
        output_label = "CHARACTER_ADD" if decision.flag == ADD_TAG else "CHARACTER_UPDATE"
        results.append(
            TestResult(
                f"update-{index}",
                success,
                detail,
                expected_output="non-empty updated profile",
                actual_output=recorder.last_output(output_label),
            )
        )
    return results


def _build_add_hints(engine: CharacterEngine) -> list[str]:
    hints: list[str] = []
    for mount in engine.extract_mount_points():
        parts = []
        if mount.region_key:
            parts.append(mount.region_key)
        if mount.polity_key:
            parts.append(mount.polity_key)
        if parts:
            hints.append("".join(parts))
    return hints


def run_add_tests(
    agent: CharacterAgent, engine: CharacterEngine, recorder: RecordingLLMClient
) -> list[TestResult]:
    results: list[TestResult] = []
    existing_ids = {record.identifier for record in engine.records}
    hints = _build_add_hints(engine)
    if hints:
        random.shuffle(hints)
    for index in range(1, 4):
        hint = hints[index - 1] if index - 1 < len(hints) else ""
        update_info = f"{hint}出现新角色，需要补充角色档案。" if hint else "新增关键角色，需要补充角色档案。"
        try:
            record = agent.create_character(update_info)
        except Exception as exc:
            results.append(
                TestResult(
                    f"add-{index}",
                    False,
                    f"exception: {exc}",
                    expected_output="non-empty profile",
                    actual_output=recorder.last_output("CHARACTER_ADD"),
                )
            )
            continue
        profile_text = str(record.profile).strip()
        is_new = record.identifier not in existing_ids
        success = bool(profile_text) and is_new
        detail = f"id={record.identifier}; hint={hint or 'none'}"
        existing_ids.add(record.identifier)
        results.append(
            TestResult(
                f"add-{index}",
                success,
                detail,
                expected_output="non-empty profile",
                actual_output=recorder.last_output("CHARACTER_ADD"),
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
    snapshot = find_latest_character_snapshot()
    if not snapshot:
        print("未找到角色存档，请先生成角色快照。")
        return
    records, world_snapshot = load_character_snapshot(snapshot)
    if not records:
        print("角色存档为空，请先生成角色快照。")
        return
    if not world_snapshot:
        world_snapshot = find_latest_world_snapshot()
    if not world_snapshot:
        print("未找到世界存档，请先生成世界快照。")
        return

    try:
        base_llm = LLMClient()
    except Exception as exc:
        print(f"LLMClient 初始化失败: {exc}")
        return

    print(f"使用角色存档：{snapshot}")
    print(f"使用世界存档：{world_snapshot}")

    query_recorder = RecordingLLMClient(base_llm)
    query_engine = build_engine(world_snapshot, records, query_recorder)
    query_agent = CharacterAgent(query_engine, llm_client=query_recorder)
    query_results = run_query_tests(query_agent, query_engine.records, query_recorder)

    update_recorder = RecordingLLMClient(base_llm)
    update_engine = build_engine(world_snapshot, records, update_recorder)
    update_agent = CharacterAgent(update_engine, llm_client=update_recorder)
    update_results = run_update_tests(update_agent, update_engine.records, update_recorder)

    add_recorder = RecordingLLMClient(base_llm)
    add_engine = build_engine(world_snapshot, records, add_recorder)
    add_agent = CharacterAgent(add_engine, llm_client=add_recorder)
    add_results = run_add_tests(add_agent, add_engine, add_recorder)

    summarize_results("查询测试", query_results)
    summarize_results("更新测试", update_results)
    summarize_results("新增测试", add_results)

    overall = query_results + update_results + add_results
    summarize_results("总体成功率", overall)


if __name__ == "__main__":
    run_demo()
