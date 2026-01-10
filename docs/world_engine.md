# World Engine

World graph utilities backed by `world/world_engine.py` and `world/world_prompt.py`.

## 核心设计
- 图结构：根节点 `world`，其子节点由内置的世界模板（原 `world.md` 内容）或传入的自定义文本生成（如 `1`, `1.1`, `1.1.1`）。非编号行会作为该节点的说明文本存储在 `description`。
- 节点字段：`identifier`（唯一 ID）、`title`、`description`（约束/提示）、`value`（生成的设定）、`children`。
- 解析规则：支持“第X维度”与数字前缀（`1.1 现实定性` 等）。缺失的父节点会用占位符补齐，保证树的完整性。

## 主要 API（WorldEngine）
- `view_node(identifier)`：查看节点信息。
- `view_children(identifier)`：列出子节点。
- `add_child(parent_id, child_key, title, description="")`：在父节点下添加子节点（节点 ID 自动拼接）。
- `add_node(identifier, title, parent_identifier=None, description="")`：按完整 ID 添加节点。
- `update_node_content(identifier, value)`：更新节点内容。
- `generate_world(user_pitch, regenerate=False)`：遍历所有节点（跳过根），为每个节点调用一次 LLM 填充 `value`。根节点的 `value` 会设为用户输入的世界观简介。
- `as_dict()`：将整棵树序列化为字典，便于调试或存档。

## LLM 生成流程
1) 用户输入一段世界观初稿 `user_pitch`。  
2) `generate_world` 深度优先遍历节点。  
3) 对每个节点调用 `WorldPromptBuilder.build_node_prompt(...)` 生成提示词，并使用单次 `chat_once` 完成内容填写。  
4) 生成内容写入节点的 `value`，可通过 `view_node` 或 `as_dict` 查看。

### 提示词生成（WorldPromptBuilder）
- `system_prompt()`：统一的系统提示，强调简洁、聚焦设定。  
- `build_node_prompt(user_pitch, node, parent_value="", extra_context=None)`：组合用户初稿、节点说明与父节点内容，要求直接输出该节点的设定。

## 使用示例
```python
from world.world_engine import WorldEngine

# 如需自定义节点模板，可传入 world_spec_text，否则使用内置模板（不依赖 world.md）
engine = WorldEngine(world_md_path=None)  # 依赖 OPENAI_* 环境变量
engine.generate_world("一个漂浮空岛组成的蒸汽朋克世界，能源匮乏。")

core_law = engine.view_node("1.1.1")
print(core_law.title, core_law.value)

# 新增自定义节点
engine.add_child("2", "2.3", "未来冲突", description="描述未来 50 年内的主要战争或变革。")
```

## 环境配置
- 需要 `.env` 或系统变量提供：`OPENAI_API_KEY`（必需）、`OPENAI_BASE_URL`（可选）、`OPENAI_MODEL`（可选，默认 `gpt-3.5-turbo`）。
- 如需注入自定义 LLM 客户端，可在初始化时传入 `llm_client` 参数；测试用可传入伪造的 `chat_once` 实现，避免真实请求。
- `test_world.py` 会提示用户输入世界观设定，并可选择是否使用真实 LLM（默认 Dummy）。
