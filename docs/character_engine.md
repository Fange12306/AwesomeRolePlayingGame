# Character Engine

Character generation utilities backed by `character/character_engine.py` and
`character/character_prompt.py`.

## 核心设计
- 数据来源：读取世界快照（`WorldEngine.as_dict()` 或 `save/world/*.json`），快照节点为 `key/value` 结构，抽取 `micro` 子树作为区域/政体挂载点。
- 角色结果：角色主体存放在 `profile`（JSON），顶层仅保留 `id/region_id/polity_id` 便于定位与映射。
- 关系拆分：角色关系与角色-地点关系采用独立边表（`relations` / `character_location_edges`）。
- 日志记录：LLM 调用日志由 `LLMClient` 统一写入 `log/llm.log`（带 `TYPE` 标记）。

## 主要 API（CharacterEngine）
- `from_world_snapshot(path, llm_client=None)`：从世界快照文件初始化。
- `set_world_snapshot(snapshot_dict)`：注入世界快照字典。
- `extract_mount_points()`：解析 `micro` 下的可挂载区域/政体节点。
- `build_blueprints(request)`：构建角色蓝图（当前仅含 ID 与挂载信息）。
- `generate_characters(request, regenerate=False)`：生成角色列表（`profile` 中含角色全量字段）。
- `generate_relations(records=None)`：基于角色列表生成角色关系边表。
- `generate_location_edges(records=None, regenerate=False)`：生成角色-地点边表。
- `save_snapshot(path, records=None)`：保存角色快照 JSON。

## LLM 生成流程
1) 读取世界快照并提取 `micro` 区域/政体节点。  
2) 使用角色总概况（可选）生成角色（`CharacterPromptBuilder`）并输出 JSON `profile`。  
3) 基于角色摘要生成关系边表（`RelationPromptBuilder`）。  
4) 基于角色 + 地点列表生成角色-地点边表（`LocationRelationPromptBuilder`）。  
5) 保存快照，包含 `characters/relations/character_location_edges`。

### 提示词生成（Prompt Builder）
- `CharacterPromptBuilder`：生成角色设定 JSON（含 `faction/profession/species/tier` 等字段）。
- `RelationPromptBuilder`：生成角色关系边表（有向边）。
- `LocationRelationPromptBuilder`：生成角色-地点关系边表（角色 -> 地点）。

## 使用示例
```python
from character.character_engine import CharacterEngine, CharacterRequest

engine = CharacterEngine.from_world_snapshot("save/world/world_20240101_120000.json")
request = CharacterRequest(total=6, pitch="一句话角色总概况")

records = engine.generate_characters(request)
relations = engine.generate_relations(records)
location_edges = engine.generate_location_edges(records)

engine.save_snapshot("save/characters/characters_20240101_120000.json")
```

## 输出结构（角色快照）
```json
{
  "generated_at": "...",
  "world_snapshot_path": "...",
  "characters": [
    {
      "id": "c1",
      "region_id": "micro.r1",
      "polity_id": "micro.r1.p1",
      "profile": {
        "name": "...",
        "summary": "...",
        "background": "...",
        "motivation": "...",
        "conflict": "...",
        "abilities": "...",
        "weaknesses": "...",
        "relationships": "...",
        "hooks": "...",
        "faction": "...",
        "profession": "...",
        "species": "...",
        "tier": "..."
      }
    }
  ],
  "relations": [],
  "character_location_edges": []
}
```

## 环境配置
- 依赖 `.env` 或系统变量：`OPENAI_API_KEY`（必需）、`OPENAI_BASE_URL`（可选）、`OPENAI_MODEL`（可选，默认 `gpt-3.5-turbo`）。
- `test/test_character.py` 提供交互式流程：选择世界快照、输入角色数量与角色总概况。
