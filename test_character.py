from __future__ import annotations

from datetime import datetime
from pathlib import Path

from character.character_engine import CharacterEngine, CharacterRequest


class DummyLLMClient:
    """Lightweight stub that echoes prompts for offline testing."""

    def __init__(self) -> None:
        self.calls = 0

    def chat_once(
        self, prompt: str, system_prompt: str = "", log_label: str | None = None
    ) -> str:
        self.calls += 1
        head = prompt.splitlines()[0][:60]
        return f"[dummy-{self.calls}] {head}..."


def choose_llm_client():
    use_real = input("Use real LLM? (default: n) [y/N]: ").strip().lower()
    if use_real == "y":
        from llm_api.llm_client import LLMClient

        return LLMClient()
    return DummyLLMClient()


def run_demo() -> None:
    snapshot = _choose_world_snapshot()
    if not snapshot:
        return

    total = _prompt_int("Total characters", default=6, minimum=1)

    request = CharacterRequest(
        total=total,
    )

    client = choose_llm_client()
    engine = CharacterEngine.from_world_snapshot(snapshot, llm_client=client)
    records = engine.generate_characters(request)
    relations = engine.generate_relations(records)
    location_edges = engine.generate_location_edges(records)

    output_path = _save_snapshot(engine)
    print("\nCharacter generation complete.")
    print(f"World snapshot: {snapshot}")
    print(f"Characters: {len(records)}")
    print(f"Relations: {len(relations)}")
    print(f"Character-location edges: {len(location_edges)}")
    print(f"Saved: {output_path}")


def _prompt_int(prompt: str, default: int, minimum: int = 0) -> int:
    text = input(f"{prompt} (default: {default}): ").strip()
    if not text:
        return default
    try:
        value = int(text)
    except ValueError:
        return default
    return max(minimum, value)


def _choose_world_snapshot() -> Path | None:
    snapshots = _list_snapshots()
    if not snapshots:
        print("No world snapshots found. Run `python test_world.py` first.")
        return None

    print("\nAvailable world snapshots:")
    for index, path in enumerate(snapshots, start=1):
        print(f"{index}. {path}")

    choice = input("Choose snapshot number (default: 1): ").strip()
    if not choice:
        return snapshots[0]
    if not choice.isdigit():
        print("Invalid choice.")
        return None
    index = int(choice) - 1
    if index < 0 or index >= len(snapshots):
        print("Invalid choice.")
        return None
    return snapshots[index]


def _list_snapshots() -> list[Path]:
    folder = Path("save") / "world"
    if not folder.exists():
        return []
    snapshots = sorted(
        folder.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True
    )
    return snapshots


def _save_snapshot(engine: CharacterEngine) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path("save") / "characters" / f"characters_{timestamp}.json"
    engine.save_snapshot(output_path)
    return output_path


if __name__ == "__main__":
    run_demo()
