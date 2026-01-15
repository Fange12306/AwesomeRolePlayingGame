# Agents 流程总览

本文档概述当前代码中的主要 agent（`GameAgent` / `WorldAgent` / `CharacterAgent`）执行流程，便于理解调用顺序与职责边界。

## GameAgent
- 输入：剧情文本 `update_info`。
- 阶段 1：搜索与读取（默认最多 2 轮）。
  - LLM 在可用世界节点/角色列表中挑选需读取项（含已读列表与数量限制）。
  - 解析/校验 ID，失败时回退到启发式关键词检索。
  - 读取世界节点与角色档案并累积上下文。
  - 每轮再由 LLM 判断是否继续搜索；若不继续或无新增条目则停止。
- 阶段 2：更新决策。
  - 以已读上下文判断是否更新世界或角色。
  - 若未读取到内容，则改用完整列表作为上下文。
- 阶段 3：命令校验（默认最多 2 轮）。
  - 先生成更新动作（不执行），再用 LLM 判断合理性。
  - 若不合理，携带原因回到搜索补充上下文后重试。
- 阶段 4：执行更新。
  - `update_world = True`：调用 `WorldAgent.collect_actions/decide_actions` 与 `apply_updates`。
  - `update_characters = True`：调用 `CharacterAgent.collect_actions/decide_actions` 与 `apply_updates`。
  - 若世界更新触及微观政权节点，会额外判断该政权下绑定角色是否需要更新。

## WorldAgent
- 输入：剧情文本 `update_info` 或查询 `query`。
- 查询流程 `extract_info`：
  - LLM 从世界树节点列表中选择最相关节点并返回内容。
- 更新流程：
  - `collect_actions` 优先识别“在某地区新增政权/国家”的特定指令。
  - 其他情况由 LLM 产出 `<|ADD_NODE|>` / `<|UPDATE_NODE|>` 决策，再结合启发式补全。
  - `apply_update` 根据动作类型执行：
    - UPDATE：更新已有节点内容。
    - ADD：新增子节点；宏观(Macro)树默认禁止新增（会回退为 UPDATE）。
  - 支持 `add_polity/remove_polity` 对微观政权进行新增/删除并补齐 7 个固定子节点。

## CharacterAgent
- 输入：剧情文本 `update_info` 或查询 `query`。
- 查询流程 `extract_info`：
  - LLM 从角色列表中选择最相关角色并返回档案内容。
- 更新流程：
  - `collect_actions/decide_actions` 决定 `<|ADD_CHARACTER|>` / `<|UPDATE_CHARACTER|>`。
  - UPDATE：在固定字段集合内重写角色 JSON。
  - ADD：
    - 根据世界挂载点（地区/政权）构造角色蓝图。
    - 使用世界概要与角色设定生成角色档案并加入记录列表。
