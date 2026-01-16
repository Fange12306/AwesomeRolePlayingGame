# Agents 流程总览（详细版）

本文档详细描述当前代码中的主要 agent（`GameAgent` / `WorldAgent` / `CharacterAgent`）的执行流程、输入输出格式、解析与回退逻辑，便于理解调用顺序、职责边界与更新联动。

## 术语与数据结构
- 世界节点（WorldNode）：`identifier`, `key`, `value`, `children`，在 `world/world_engine.py` 中定义。
- 角色记录（CharacterRecord）：`identifier`, `region_id`, `polity_id`, `profile`，在 `character/character_engine.py` 中定义。
- 世界动作（ActionDecision）：`flag`, `index`, `raw`，由 `WorldAgent` 产出。
- 角色动作（CharacterActionDecision）：`flag`, `identifier`, `raw`，由 `CharacterAgent` 产出。
- 游戏更新决策（GameUpdateDecision）：`update_world`, `update_characters`, `raw`, `reason`。
- 游戏更新结果（GameUpdateResult）：包含世界/角色的动作与更新后的节点/记录列表。
- 政权（polity）：`micro.rX.pY` 级别节点（micro 区域的直接子节点）。
- 政权固定子节点：文化/经济/政治/人口/地理/技术/资源。

## GameAgent（`game/game_agent.py`）

### 入口与输出
- `decide_updates(update_info, read_context=None)`：判断是否更新世界与角色。
- `apply_update(update_info)`：完整执行流程，返回 `GameUpdateResult`。

### 搜索与读取（Search & Read）
- 默认搜索轮次：`DEFAULT_SEARCH_ROUNDS = 2`，每轮最多 4 个世界节点 + 4 个角色。
- 每轮流程：
  1) `_build_search_prompt` 提示 LLM 从可用节点/角色中选择需要读取项。
  2) `_parse_search_response` 解析 LLM 输出（JSON 或 `WORLD=...; CHARACTER=...`）。
  3) `_resolve_world_identifiers` / `_resolve_character_identifiers` 用 identifier 或 key/name 做映射。
  4) 若解析为空，回退 `_heuristic_search`（在剧情文本中匹配节点 key / 角色 ID / 角色名）。
  5) `_build_search_decision_prompt` 询问是否继续搜索，若 `NO` 或无新增则停止。
- 上下文格式：`W(n)` 与 `C(n)` 分组，用 `|` 拼接，避免超长行（默认 320 字符限制）。
- 若后续命令校验不通过，`reason` 会作为 `search_hint` 再次触发搜索补充上下文。

### 更新判断（Decide）
- `_build_decision_prompt` 强制两行输出：
  - `WORLD=YES/NO; CHARACTER=YES/NO`
  - `{"update_world":true|false,"update_characters":true|false,"reason":"..."}`
- 若未读取上下文，会追加完整的角色与世界节点列表作为参考。
- `_parse_decision` 支持 JSON 或文本格式；失败时走 `_heuristic_decision`：
  - 世界关键词：地区/政权/制度/战争/资源等。
  - 角色关键词：角色/人物/主角等。
  - 若文本过长且没有命中关键词，默认同时更新世界与角色。

### 命令生成与校验（Command Validate）
- 世界动作：`WorldAgent.collect_actions`（优先）或 `decide_actions`。
- 角色动作：`CharacterAgent.collect_actions`（优先）或 `decide_actions`。
- 校验轮数：`DEFAULT_COMMAND_VALIDATE_ROUNDS = 2`。
- `_build_command_validation_prompt` 会列出已读取上下文与即将执行的命令摘要；若不合理，返回 `NO` 并要求补充上下文。

### 执行与联动更新
- 若 `update_world` 为真：调用 `WorldAgent.apply_updates`。
- 若 `update_characters` 为真：调用 `CharacterAgent.apply_updates`。
- 若更新涉及政权：
  - `_maybe_update_characters_for_polity_updates`：筛选该政权下角色，LLM 判断是否需要更新档案。
  - `_maybe_update_characters_for_polity_removals`：政权删除时将角色 `polity_id` 置空并更新档案。

### 政权合并（Special Case）
- 触发条件：剧情中包含 `合并/并入/吞并/并为/归并` 等关键词，且至少命中两个政权名称/ID。
- `_build_polity_merge_prompt` 让 LLM 选择保留/删除政权。
- 更新流程：
  1) 更新保留政权内容（合并后的描述）。
  2) 删除被并入政权节点。
  3) 更新受影响角色的 `polity_id` 与档案。

## WorldAgent（`world/world_agent.py`）

### 角色与责任
- 面向世界树的查询与更新，输出 `ActionDecision`。
- 支持 `ADD_NODE` / `UPDATE_NODE` / `REMOVE_NODE` 三类动作。

### 决策与解析
- `_build_decision_prompt` 要求双重输出（标签与 JSON 数组）。
- `_parse_decisions` 支持三种形式：
  - `<|ADD_NODE|>:INDEX` 标签格式
  - JSON 数组 `[{"action":"UPDATE_NODE","index":"..."}]`
  - JSON 对象（容错）
- 关键解析与修正：
  - ADD：只允许输出父节点索引（不会直接接受新子节点 ID）。
  - UPDATE：若输出宏观节点但剧情明显指向 micro，会尝试切换到 micro 目标。
  - Macro 树禁止新增：若 ADD 目标落在 macro，则自动降级为 UPDATE。

### 具体动作执行
- `UPDATE_NODE`：
  - `_build_update_prompt` 要求两行输出 `<|KEY|>` 与 `<|VALUE|>`。
  - 解析失败时接受 JSON 或直接使用全文作为 value。
- `ADD_NODE`：
  - micro 分支允许连续创建多层节点（多组 `<|KEY|>/<|VALUE|>`）。
  - 若新增的是 micro 政权，会自动补齐 7 个固定子节点并补全内容。
- `REMOVE_NODE`：
  - 若删除的是 micro 政权，调用 `remove_polity` 删除整棵子树。

### 政权新增/删除特化
- `_detect_polity_intent`：使用 LLM 判断是否涉及新增/删除政权。
- 新增：若地区缺失，可先在 `micro` 下新增地区再新增政权。
- 删除：根据政权名称与地区提示解析出具体节点。

### 启发式回退
- `_infer_actions_from_text`：从剧情中抽取可能的节点标识或 key，转为 UPDATE。
- `_should_prefer_micro`：剧情包含 micro.* 或具体地区/政权名时优先 micro 节点。

## CharacterAgent（`character/character_agent.py`）

### 角色与责任
- 处理角色查询、增量更新、新增角色。
- 只对角色档案 JSON 与角色列表进行操作，不直接处理关系边表。

### 决策与解析
- `_build_decision_prompt` 要求双重输出（标签与 JSON 数组）。
- ADD 会分配新 ID（`c1`, `c2`...），UPDATE 只能指向已有角色 ID。

### 新增角色（ADD）
- `_match_mount_point`：在剧情信息中匹配 `region_key` 或 `polity_key`，确定挂载点。
- `CharacterPromptBuilder.build_prompt`：基于世界纲要与挂载点生成角色档案。
- 输出必须是单个 JSON 对象，字段固定为：
  `name, summary, background, motivation, conflict, abilities, weaknesses, relationships, hooks, faction, profession, species, tier`。

### 更新角色（UPDATE）
- `_build_update_prompt` 要求仅输出完整 JSON。
- `_normalize_profile_update` 保证必填字段齐全，未变更字段保持原值。

### 启发式回退
- `_infer_actions_from_text`：
  - 匹配 `cN` 格式 ID。
  - 匹配角色名（唯一映射才生效）。

## 日志与标签
- 日志统一写入 `log/*.log`，LLM 输入输出写入 `log/llm.log`。
- 典型标签：
  - GameAgent：`GAME_SEARCH_*`, `GAME_DECIDE`, `GAME_COMMAND_VALIDATE_*`。
  - WorldAgent：`DECIDE`, `UPDATE_NODE`, `ADD_NODE`, `POLITY_INTENT`。
  - CharacterAgent：`CHARACTER_DECIDE`, `CHARACTER_UPDATE`, `CHARACTER_ADD`。
