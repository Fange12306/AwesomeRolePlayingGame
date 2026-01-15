# Character Engine

角色生成与关系构建工具，核心实现位于 `character/character_engine.py` 与 `character/character_prompt.py`。

## 核心设计
- 数据来源：读取世界快照（`WorldEngine.as_dict()` 或 `save/world/*.json`），抽取 `micro` 子树作为挂载点。
- 角色结果：每条记录为 `id/region_id/polity_id/profile`，`profile` 尽量解析为 JSON；解析失败时会保留原始文本。
- 关系拆分：角色关系与角色-地点关系采用独立边表（`relations` / `character_location_edges`）。
- 日志记录：LLM 调用日志写入 `log/llm.log`，并带 `TYPE` 标记。

## 主要 API（CharacterEngine）
- `from_world_snapshot(path, llm_client=None)`：从世界快照文件初始化。
- `set_world_snapshot(snapshot_dict)`：注入世界快照字典。
- `extract_mount_points()`：解析 `micro` 下的区域/政体挂载点。
- `build_blueprints(request)`：构建角色蓝图（ID + 挂载点，按挂载点轮询分配）。
- `generate_characters(request, regenerate=False)`：生成角色列表。
- `generate_relations(records=None)`：基于角色列表生成角色关系边表。
- `generate_location_edges(records=None, regenerate=False)`：生成角色-地点边表（规则边 + LLM 补充边）。
- `save_snapshot(path, records=None)`：保存角色快照 JSON。

## LLM 生成流程
1) 读取世界快照并提取 `micro` 区域/政体节点。  
2) 使用角色总概况（可选）生成角色（`CharacterPromptBuilder`），输出 JSON `profile`。  
3) 基于角色摘要生成关系边表（`RelationPromptBuilder`）。  
4) 生成地点边表：先添加规则边（`origin`/`affiliation`），再由 LLM 补充额外关系（`LocationRelationPromptBuilder`）。  
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
