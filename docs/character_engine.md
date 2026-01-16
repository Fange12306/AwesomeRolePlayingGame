# Character Engine（详细版）

角色生成与关系构建工具，核心实现位于 `character/character_engine.py` 与 `character/character_prompt.py`。

## 数据结构
- `CharacterRequest`：生成请求（`total`, `pitch`）。
- `MountPoint`：角色挂载点（地区/政权 ID + key/value 摘要）。
- `CharacterBlueprint`：生成蓝图（角色 ID + 挂载点 ID）。
- `CharacterRecord`：角色记录（`identifier`, `region_id`, `polity_id`, `profile`）。

## 世界快照依赖
- `CharacterEngine` 读取世界快照（`save/world/*.json` 或传入字典）。
- 仅依赖 `micro` 子树以提取地区/政权挂载点。
- world snapshot 缺失时仍可生成角色，但挂载点为空。

## 角色生成流程
1) **挂载点提取**
   - `extract_mount_points()` 遍历 `micro` 根下的地区与政权。
   - 若地区没有政权，则仅生成地区挂载点。
2) **蓝图分配**
   - `build_blueprints()` 按挂载点循环分配角色位置（round-robin）。
3) **角色档案生成**
   - `CharacterPromptBuilder.build_prompt()` 构造角色 prompt。
   - 输出必须为单个 JSON 对象，包含固定字段：
     `name, summary, background, motivation, conflict, abilities, weaknesses, relationships, hooks, faction, profession, species, tier`。
   - `_generate_profile_with_retry` 会对非 JSON 输出进行一次重试。
4) **保存结果**
   - `save_snapshot()` 写入角色快照 JSON（含关系边表）。

## 角色关系边表
- `generate_relations()` 使用 `RelationPromptBuilder`，输出 JSON 数组。
- 字段约束：
  - `source_id`, `target_id`, `type`, `stance`, `intensity`, `note`。
- 解析失败时回退为 `[{"raw": "..."}]` 结构。

## 角色-地点关系边表
- `generate_location_edges()` 由两部分组成：
  1) **规则边**：
     - `origin`：角色 -> region
     - `affiliation`：角色 -> polity
  2) **LLM 补充边**：每个角色额外 1-2 条地点关系。
- 地点类型推断：
  - `micro.rX` -> `region`
  - `micro.rX.pY` -> `polity`
  - 其他 `micro.*` 子节点 -> `subregion`
  - 固定政权子节点（文化/经济/政治/人口/地理/技术/资源）会被排除。
- 合并策略：去重、仅保留有效角色/地点 ID。

## 角色快照格式
```json
{
  "generated_at": "2024-01-01T12:00:00",
  "world_snapshot_path": "save/world/world_20240101_120000.json",
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

## 主要 API
- `from_world_snapshot(path, llm_client=None)`：从世界快照文件初始化。
- `set_world_snapshot(snapshot_dict)`：注入世界快照字典。
- `extract_mount_points()`：解析 `micro` 下的地区/政权挂载点。
- `build_blueprints(request)`：构建角色蓝图。
- `generate_characters(request, regenerate=False)`：生成角色列表。
- `generate_relations(records=None)`：生成角色关系边表。
- `generate_location_edges(records=None, regenerate=False)`：生成角色-地点边表。
- `save_snapshot(path, records=None)`：保存角色快照 JSON。

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

## 日志与提示词
- LLM 调用日志：`log/llm.log`。
- 角色模块日志：`log/character_engine.log`。
- 提示词模板：`character/character_prompt.py`。
