from __future__ import annotations

import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dataclasses import dataclass

from character.character_agent import CharacterAgent
from character.character_engine import CharacterEngine, CharacterRecord
from game.game_agent import GameAgent
from llm_api.llm_client import LLMClient
from world.world_agent import WorldAgent
from world.world_engine import WorldEngine


@dataclass
class PolityDef:
    region_key: str
    region_id: str
    polity_key: str
    polity_id: str


@dataclass
class MergeCase:
    name: str
    polities: list[PolityDef]
    merge_pair: tuple[str, str]
    update_info: str


def build_world(llm: LLMClient, polities: list[PolityDef]) -> WorldEngine:
    engine = WorldEngine(world_spec_path=None, llm_client=llm, auto_generate=False)
    regions: dict[str, str] = {}
    for polity in polities:
        if polity.region_id not in regions:
            region = engine.add_child("micro", polity.region_id, polity.region_key)
            regions[polity.region_id] = region.identifier
        region_id = regions[polity.region_id]
        engine.add_child(region_id, polity.polity_id, polity.polity_key)
    return engine


def build_characters(polities: list[PolityDef]) -> list[CharacterRecord]:
    records: list[CharacterRecord] = []
    for idx, polity in enumerate(polities, start=1):
        records.append(
            CharacterRecord(
                identifier=f"c{idx}",
                region_id=f"micro.{polity.region_id}",
                polity_id=f"micro.{polity.region_id}.{polity.polity_id}",
                profile={"name": f"角色{idx}"},
            )
        )
    records.append(
        CharacterRecord(
            identifier=f"c{len(records) + 1}",
            region_id=f"micro.{polities[0].region_id}",
            polity_id=None,
            profile={"name": "独立角色"},
        )
    )
    return records


def run_case(case: MergeCase, llm: LLMClient) -> None:
    world_engine = build_world(llm, case.polities)
    world_agent = WorldAgent(world_engine, llm_client=llm)

    character_engine = CharacterEngine(
        world_snapshot=world_engine.as_dict(), llm_client=llm
    )
    character_engine.records = build_characters(case.polities)
    character_agent = CharacterAgent(character_engine, llm_client=llm)

    game_agent = GameAgent(world_agent, character_agent, llm_client=llm)
    original_polity_ids = {
        record.identifier: record.polity_id for record in character_engine.records
    }
    result = game_agent.apply_update(case.update_info)

    assert result.decision.update_world, f"{case.name}: update_world should be True"
    assert result.decision.update_characters, f"{case.name}: update_characters should be True"

    polity_ids = [f"micro.{p.region_id}.{p.polity_id}" for p in case.polities]
    merge_ids = list(case.merge_pair)
    remaining = [pid for pid in merge_ids if pid in world_engine.nodes]
    removed = [pid for pid in merge_ids if pid not in world_engine.nodes]
    assert len(removed) == 1, f"{case.name}: expected 1 merge polity removed, got {removed}"
    assert len(remaining) == 1, f"{case.name}: expected 1 merge polity remaining, got {remaining}"

    keep_id = remaining[0]
    for pid in polity_ids:
        if pid in merge_ids:
            continue
        if pid not in world_engine.nodes:
            raise AssertionError(f"{case.name}: unrelated polity removed unexpectedly")
    for record in character_engine.records:
        original = original_polity_ids.get(record.identifier)
        if original in merge_ids:
            if record.polity_id != keep_id:
                raise AssertionError(
                    f"{case.name}: merged record not reassigned to kept polity"
                )
        elif original:
            if record.polity_id != original:
                raise AssertionError(
                    f"{case.name}: unrelated record polity changed unexpectedly"
                )
    print(f"{case.name}: PASS")


def run_demo() -> None:
    llm = LLMClient()
    cases = [
        MergeCase(
            name="case_same_region",
            polities=[
                PolityDef("北境", "r1", "北境议会", "p1"),
                PolityDef("北境", "r1", "白石公国", "p2"),
            ],
            merge_pair=("micro.r1.p1", "micro.r1.p2"),
            update_info=(
                "剧情更新：政权合并。保留 micro.r1.p1 北境议会，删除 micro.r1.p2 白石公国。"
                "白石公国并入北境议会。"
            ),
        ),
        MergeCase(
            name="case_cross_region",
            polities=[
                PolityDef("海港", "r1", "海港联邦", "p1"),
                PolityDef("山岭", "r2", "山岭王国", "p1"),
            ],
            merge_pair=("micro.r1.p1", "micro.r2.p1"),
            update_info=(
                "剧情更新：政权合并。保留 micro.r1.p1 海港联邦，删除 micro.r2.p1 山岭王国。"
                "山岭王国整体并入海港联邦。"
            ),
        ),
        MergeCase(
            name="case_multi_polity",
            polities=[
                PolityDef("群岛", "r1", "自由议会", "p1"),
                PolityDef("群岛", "r1", "赤潮军政府", "p2"),
                PolityDef("群岛", "r1", "中立商会", "p3"),
            ],
            merge_pair=("micro.r1.p2", "micro.r1.p3"),
            update_info=(
                "剧情更新：政权合并。保留 micro.r1.p3 中立商会，删除 micro.r1.p2 赤潮军政府。"
                "赤潮军政府并入中立商会，其他政权不变。"
            ),
        ),
    ]
    for case in cases:
        run_case(case, llm)


if __name__ == "__main__":
    run_demo()
