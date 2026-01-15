# World Engine

世界树生成与管理工具，核心实现位于 `world/world_engine.py` 与 `world/world_prompt.py`。

## 核心设计
- 结构：根节点 `world`，包含 `macro` 与 `micro` 两棵子树。
- 节点字段：`identifier`（唯一 ID）、`key`（名称）、`value`（内容）、`children`。
- 模板解析：读取 `world/world_spec.md`（或 `world_spec_text`）生成宏观节点，支持“第X维度”与数字前缀（`1.1 核心设定`）。非节点行会被当作该节点的提示文本。

## 主要 API（WorldEngine）
- `view_node(identifier)`：查看节点信息。
- `view_children(identifier)`：列出子节点。
- `add_child(parent_id, child_key, key, allow_macro_add=False)`：在父节点下添加子节点（自动拼接 ID）。
- `add_node(identifier, key, parent_identifier=None, allow_macro_add=False)`：按完整 ID 添加节点。
- `update_node_content(identifier, value)`：更新节点内容。
- `remove_node(identifier)`：移除节点及其全部子树。
- `generate_world(user_pitch, regenerate=False)`：生成宏观内容、微观结构与节点细节。
- `apply_snapshot(snapshot)` / `from_snapshot(path)`：载入世界快照。
- `save_snapshot(path)` / `as_dict()`：保存或序列化快照。
- `micro_scale`：微观规模（`small`/`medium`/`large`）。

## LLM 生成流程
1) 解析 `world/world_spec.md`，生成 macro 节点（`key` 取模板标题）。  
2) 逐个 macro 节点调用 LLM 填充 `value`。  
3) 单次对话生成 micro 地区名称列表（small 2-3，medium 3-5，large 5-7）。  
4) 对每个地区生成政权名称列表，并为政权挂载 7 个固定子节点：文化/经济/政治/人口/地理/技术/资源。  
5) 遍历 micro 子树，为空节点补全内容（上下文包含宏观总结 + 父层信息）。  

提示词中默认要求：未明确说明时按现实世界情况填写，避免无依据虚构。

### 提示词生成（WorldPromptBuilder）
- `system_prompt()`：统一系统提示。
- `build_macro_prompt(...)`：宏观节点生成提示。
- `build_region_list_prompt(...)`：地区名称列表提示（JSON 数组）。
- `build_polity_list_prompt(...)`：政权名称列表提示（JSON 数组）。
- `build_micro_value_prompt(...)`：微观节点 value 生成提示。

## 使用示例
```python
from world.world_engine import WorldEngine

engine = WorldEngine(
    user_pitch="一个漂浮空岛组成的蒸汽朋克世界，能源匮乏。",
    micro_scale="medium",
    auto_generate=True,
)

core_law = engine.view_node("1.1")
print(core_law.key, core_law.value)

# 新增自定义节点（非宏观树）
engine.add_child("micro", "r9", "新地区")
```

## 环境配置
- 需要 `.env` 或系统变量提供：`OPENAI_API_KEY`（必需）、`OPENAI_BASE_URL`（可选）、`OPENAI_MODEL`（可选，默认 `gpt-3.5-turbo`）。
- 可在初始化时传入自定义 `llm_client`（测试可注入伪造 `chat_once`）。
- `test/test_world.py` 提供交互式流程：输入世界观设定并生成示例快照。
