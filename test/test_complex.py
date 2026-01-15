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


@dataclass
class ComplexCase:
    name: str
    update_info: str
    expect_world: bool
    expect_characters: bool


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


def choose_world_nodes(
    snapshot: dict[str, dict[str, object]], limit: int = 4
) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    for identifier, node in snapshot.items():
        if identifier in {"world", "macro", "micro"}:
            continue
        key = str(node.get("key", node.get("title", ""))).strip()
        if not key:
            continue
        candidates.append((identifier, key))
    macro_nodes = [item for item in candidates if not item[0].startswith("micro.")]
    micro_nodes = [item for item in candidates if item[0].startswith("micro.")]
    random.shuffle(macro_nodes)
    random.shuffle(micro_nodes)
    ordered = macro_nodes + micro_nodes
    return ordered[:limit]


def extract_micro_context(
    snapshot: dict[str, dict[str, object]], limit: int = 4
) -> list[tuple[str, str]]:
    micro = snapshot.get("micro")
    if not micro:
        return []
    region_ids = micro.get("children", []) if isinstance(micro, dict) else []
    contexts: list[tuple[str, str]] = []
    for region_id in region_ids:
        region = snapshot.get(region_id, {})
        region_key = str(region.get("key", region.get("title", ""))).strip()
        polity_ids = region.get("children", []) if isinstance(region, dict) else []
        if not polity_ids:
            if region_key:
                contexts.append((region_key, ""))
            continue
        for polity_id in polity_ids:
            polity = snapshot.get(polity_id, {})
            polity_key = str(polity.get("key", polity.get("title", ""))).strip()
            if region_key or polity_key:
                contexts.append((region_key, polity_key))
    random.shuffle(contexts)
    return contexts[:limit]


def choose_character_targets(
    records: list[CharacterRecord], limit: int = 4
) -> list[CharacterRecord]:
    candidates = [record for record in records if record.identifier]
    if not candidates:
        return []
    random.shuffle(candidates)
    return candidates[:limit]


def format_character_label(record: CharacterRecord) -> str:
    name = ""
    faction = ""
    if isinstance(record.profile, dict):
        name = str(record.profile.get("name", "")).strip()
        faction = str(record.profile.get("faction", "")).strip()
    label = name or record.identifier
    if faction:
        return f"{label}（{faction}）"
    return label


def build_cases(
    world_data: dict[str, dict[str, object]],
    records: list[CharacterRecord],
) -> list[ComplexCase]:
    random.seed(17)
    world_nodes = choose_world_nodes(world_data, limit=6)
    micro_contexts = extract_micro_context(world_data, limit=6)
    characters = choose_character_targets(records, limit=6)

    if not world_nodes or not characters:
        return []

    def pick(items: list, index: int, fallback: str = ""):
        if not items:
            return fallback
        return items[index % len(items)]

    def build_detail(index: int, node_key: str) -> str:
        region_key, polity_key = pick(micro_contexts, index, ("", ""))
        location = "".join(part for part in (region_key, polity_key) if part)
        return location or node_key or "营地"

    def render_template(
        template: str,
        node_id: str,
        node_key: str,
        detail: str,
        label: str,
        label2: str,
    ) -> str:
        return template.format(
            node_id=node_id,
            node_key=node_key,
            detail=detail,
            label=label,
            label2=label2,
        )

    cases: list[ComplexCase] = []

    both_templates = [
        (
            "传闻在{detail}爆发多方冲突，{label}被迫在盟约与亲族之间抉择，"
            "导致{node_key}秩序动摇并引发权力格局重排。需要交代冲突后果，"
        ),
        (
            "关于{node_key}的秘密条款被揭露，{label}因此失去庇护并与{label2}公开对立，"
            "事件迅速波及{detail}周边，新的利益关系开始重组。"
        ),
        (
            "随着{detail}的资源链断裂，{label}被推为临时代理，试图重构{node_key}机制；"
            "其个人立场变化牵动多方。"
        ),
        (
            "一次突发仪式在{detail}失控，重塑了{node_key}的运作逻辑，{label}为掩盖真相"
            "调整阵营与策略。"
        ),
    ]

    world_only_templates = [
        (
            "{detail}的地缘边界重新划分，导致{node_key}相关制度与资源流向整体调整，"
        ),
        (
            "连续异常气候侵袭{detail}，迫使{node_key}的治理模型与生产结构重构，"
        ),
        (
            "围绕{node_key}的新法典在{detail}推行，引发宏观体系调整，"
        ),
        (
            "远方势力与{detail}签订新协议，导致{node_key}的战略定位变化，"
        ),
    ]

    character_only_templates = [
        (
            "角色{label}在长期潜伏后暴露身份，与{label2}的关系彻底反转，"
            "其价值取向与行动方式出现显著转折。"
        ),
        (
            "角色{label}在调查中发现自己的记忆被篡改，行为准则与目标被迫重写，"
            "行为准则与目标被迫重写。"
        ),
        (
            "角色{label}因背负新誓约而调整行事方式，其个人能力与弱点出现新限制，"
        ),
        (
            "角色{label}在内心挣扎后决定改换阵营，影响其关系网络与剧情钩子，"
        ),
    ]

    skip_templates = [
        "雨后的清晨，{label}整理行囊并记录路途见闻，队伍只是调整节奏。",
        "夜色下{label}与同伴交换对食物与旅程的感受，气氛平静。",
        "在{detail}的集市上，{label}观察匠人展示传统技艺，留下随记。",
        "{label}在营火旁复盘当天行程，写下短暂感悟。",
    ]

    for idx, template in enumerate(both_templates, start=1):
        node_id, node_key = pick(world_nodes, idx - 1)
        record = pick(characters, idx - 1)
        record2 = pick(characters, idx, record)
        label = format_character_label(record)
        label2 = format_character_label(record2)
        detail = build_detail(idx - 1, node_key)
        update_info = render_template(
            template, node_id, node_key, detail, label, label2
        )
        cases.append(
            ComplexCase(
                name=f"both-{idx}",
                update_info=update_info,
                expect_world=True,
                expect_characters=True,
            )
        )

    for idx, template in enumerate(world_only_templates, start=1):
        node_id, node_key = pick(world_nodes, idx + 1)
        record = pick(characters, idx - 1)
        label = format_character_label(record)
        detail = build_detail(idx + 1, node_key)
        update_info = render_template(
            template, node_id, node_key, detail, label, label
        )
        cases.append(
            ComplexCase(
                name=f"world-only-{idx}",
                update_info=update_info,
                expect_world=True,
                expect_characters=False,
            )
        )

    for idx, template in enumerate(character_only_templates, start=1):
        record = pick(characters, idx - 1)
        record2 = pick(characters, idx, record)
        node_id, node_key = pick(world_nodes, idx - 1)
        label = format_character_label(record)
        label2 = format_character_label(record2)
        detail = build_detail(idx - 1, node_key)
        update_info = render_template(
            template, node_id, node_key, detail, label, label2
        )
        cases.append(
            ComplexCase(
                name=f"character-only-{idx}",
                update_info=update_info,
                expect_world=False,
                expect_characters=True,
            )
        )

    for idx, template in enumerate(skip_templates, start=1):
        record = pick(characters, idx - 1)
        node_id, node_key = pick(world_nodes, idx - 1)
        label = format_character_label(record)
        detail = build_detail(idx - 1, node_key)
        update_info = render_template(
            template, node_id, node_key, detail, label, label
        )
        cases.append(
            ComplexCase(
                name=f"skip-{idx}",
                update_info=update_info,
                expect_world=False,
                expect_characters=False,
            )
        )

    return cases


def summarize_results(title: str, results: List[TestResult]) -> None:
    total = len(results)
    passed = sum(1 for result in results if result.success)
    rate = (passed / total * 100) if total else 0.0
    print(f"\n[{title}] {passed}/{total} ({rate:.1f}%)")
    for index, result in enumerate(results, start=1):
        status = "PASS" if result.success else "FAIL"
        detail = f" - {result.detail}" if result.detail else ""
        print(f"{index}. {status}: {result.name}{detail}")
        if not result.success and (result.expected_output or result.actual_output):
            expected = _snippet(result.expected_output) if result.expected_output else "N/A"
            actual = _snippet(result.actual_output) if result.actual_output else "N/A"
            print(f"   expected: {expected}")
            print(f"   actual: {actual}")


def _has_world_updates(result: GameUpdateResult) -> bool:
    nodes = result.world_nodes or ([result.world_node] if result.world_node else [])
    return any(node and node.value.strip() for node in nodes)


def _has_character_updates(result: GameUpdateResult) -> bool:
    records = (
        result.character_records
        or ([result.character_record] if result.character_record else [])
    )
    return any(record and str(record.profile).strip() for record in records)


def run_case(
    case: ComplexCase,
    world_snapshot: Path,
    records: list[CharacterRecord],
    base_llm: LLMClient,
) -> TestResult:
    recorder = RecordingLLMClient(base_llm)
    world_engine = WorldEngine.from_snapshot(world_snapshot, llm_client=recorder)
    character_engine = CharacterEngine.from_world_snapshot(
        world_snapshot, llm_client=recorder
    )
    character_engine.records = clone_records(records)
    world_agent = WorldAgent(world_engine, llm_client=recorder)
    character_agent = CharacterAgent(character_engine, llm_client=recorder)
    game_agent = GameAgent(world_agent, character_agent, llm_client=recorder)

    expected = f"world={case.expect_world} characters={case.expect_characters}"
    try:
        result = game_agent.apply_update(case.update_info)
    except Exception as exc:
        return TestResult(
            case.name,
            False,
            f"exception: {exc}",
            expected_output=expected,
            actual_output=recorder.last_output("GAME_DECIDE"),
        )

    decision = result.decision
    decision_match = (
        decision.update_world == case.expect_world
        and decision.update_characters == case.expect_characters
    )
    detail = (
        f"expected={expected} actual=world={decision.update_world} "
        f"characters={decision.update_characters}"
    )

    if not decision_match:
        reason = decision.reason.strip() if decision.reason else ""
        if reason:
            detail = f"{detail}; reason={reason}"
        return TestResult(
            case.name,
            False,
            detail,
            expected_output=expected,
            actual_output=recorder.last_output("GAME_DECIDE"),
        )

    if case.expect_world:
        if not _has_world_updates(result):
            detail = f"world_update_empty; {detail}"
            return TestResult(
                case.name,
                False,
                detail,
                expected_output="non-empty world node",
                actual_output=recorder.last_output("UPDATE_NODE")
                or recorder.last_output("ADD_NODE"),
            )

    if case.expect_characters:
        if not _has_character_updates(result):
            detail = f"character_update_empty; {detail}"
            return TestResult(
                case.name,
                False,
                detail,
                expected_output="non-empty character profile",
                actual_output=recorder.last_output("CHARACTER_UPDATE")
                or recorder.last_output("CHARACTER_ADD"),
            )

    return TestResult(case.name, True, "ok")


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

    world_data = load_world_snapshot(world_snapshot)
    cases = build_cases(world_data, records)
    if not cases:
        print("未能生成复杂测试用例，请检查存档内容。")
        return

    try:
        base_llm = LLMClient()
    except Exception as exc:
        print(f"LLMClient 初始化失败: {exc}")
        return

    print(f"使用世界存档：{world_snapshot}")
    print(f"使用角色存档：{character_snapshot}")
    print(f"复杂测试数量：{len(cases)}")

    results = [run_case(case, world_snapshot, records, base_llm) for case in cases]

    summarize_results("复杂剧情测试", results)


if __name__ == "__main__":
    run_demo()
