# World Engine（详细版）

世界树生成与管理工具，核心实现位于 `world/world_engine.py` 与 `world/world_prompt.py`，负责构建世界树结构、生成宏观与微观设定、序列化快照。

## 数据模型
- `WorldNode`
  - `identifier`：节点唯一 ID（如 `1.1`、`micro.r1.p1.resources`）。
  - `key`：节点标题（可被更新）。
  - `value`：节点内容（文本）。
  - `children`：子节点集合。
- 根节点结构：
  - `world`（根）
  - `macro`（宏观设定树）
  - `micro`（微观设定树）
- ID 规则：
  - macro：模板解析生成（`1`、`1.1`、`2.3` 等）。
  - micro：`micro.r{n}` 表示地区；`micro.r{n}.p{n}` 表示政权；政权下固定 7 个子节点（文化/经济/政治/人口/地理/技术/资源）。

## 世界模板与解析规则
- 默认模板：`world/world_spec.md`。
- 可通过构造参数 `world_spec_text` 覆盖模板内容。
- 解析规则（`_parse_world_spec`）：
  - 行以“第X维度”开头 -> 生成宏观一级节点（ID 为数字）。
  - 行以 `1.1 标题` 形式开头 -> 生成子节点（ID 为数字层级）。
  - 其他行视为“提示文本”，归属到最近的节点，作为生成时的 hint。
- 注意：提示行不要以数字开头，否则会被误识为新节点。

## 生成流程（LLM）
1) **宏观节点生成**
   - 解析模板并建立 macro 节点树。
   - 对每个 macro 节点调用 LLM 填充 `value`。
2) **宏观总结**
   - 将 macro 节点内容汇总为简短摘要，作为微观生成上下文。
3) **微观结构生成**
   - 调用 LLM 生成地区名称列表（数量取决于 `micro_scale`）。
   - 对每个地区生成政权名称列表。
   - 为每个政权添加固定子节点（文化/经济/政治/人口/地理/技术/资源）。
4) **微观内容补全**
   - 为每个 micro 节点生成 `value`。
   - 上下文包含宏观摘要、父节点信息与已生成的地区/政权列表。

## Micro Scale 规模
- `small`：地区 2-3，政权 2-3。
- `medium`：地区 3-5，政权 3-5。
- `large`：地区 5-7，政权 5-7。

## Prompt 与上下文构造
- `WorldPromptBuilder.build_macro_prompt`：宏观节点生成提示。
- `WorldPromptBuilder.build_region_list_prompt`：地区名称列表（JSON 数组）。
- `WorldPromptBuilder.build_polity_list_prompt`：政权名称列表（JSON 数组）。
- `WorldPromptBuilder.build_micro_value_prompt`：微观节点内容生成提示。
- `macro_summary`：由 `build_macro_summary_prompt` 生成，用于提供一致性上下文。

## 公共 API
- `view_node(identifier)`：查看节点。
- `view_children(identifier)`：列出子节点（排序）。
- `add_child(parent_id, child_key, key, allow_macro_add=False)`：新增子节点。
- `add_node(identifier, key, parent_identifier=None, allow_macro_add=False)`：按完整 ID 添加节点。
- `update_node_content(identifier, value)`：更新节点内容。
- `remove_node(identifier)`：删除节点及其子树（禁止删除根节点）。
- `generate_world(user_pitch, regenerate=False, progress_callback=None)`：生成完整世界。
- `apply_snapshot(snapshot)` / `from_snapshot(path)`：加载快照。
- `save_snapshot(path)` / `as_dict()`：保存或序列化快照。

## 快照格式
- `as_dict()` 返回结构：
  ```json
  {
    "world": {"key": "World", "value": "...", "children": ["macro", "micro"]},
    "macro": {"key": "Macro", "value": "", "children": ["1", "2", "3"]},
    "1": {"key": "世界基石", "value": "...", "children": ["1.1", "1.2"]}
  }
  ```
- 每个节点包含 `key`, `value`, `children`，children 为子节点 ID 列表。

## 重试与容错
- `_generate_text_with_retry`：输出为空或报错时自动重试。
- `_parse_name_list`：严格解析 JSON 数组；数量不达标会重试。
- 输出异常时会记录到 `log/world_engine.log` 与 `log/llm.log`。

## 示例
```python
from world.world_engine import WorldEngine

engine = WorldEngine(
    user_pitch="一个漂浮空岛组成的蒸汽朋克世界，能源匮乏。",
    micro_scale="medium",
    auto_generate=True,
)

node = engine.view_node("1.1")
print(node.key, node.value)

engine.save_snapshot("save/world/world_example.json")
```

## 相关文件
- `world/world_engine.py`
- `world/world_prompt.py`
- `world/world_spec.md`
