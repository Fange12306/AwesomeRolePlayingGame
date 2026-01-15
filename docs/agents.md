# Agents 流程总览

本文档概述当前代码中的主要 agent（GameAgent / WorldAgent / CharacterAgent）执行流程，便于理解调用顺序与职责边界。

## GameAgent
- 输入：剧情文本 `update_info`。
- 阶段 1：搜索与读取（多轮）。
  - 每轮由 LLM 决定需要读取的世界节点与角色（带可用列表与已读列表）。
  - 解析并解析/校验 ID，无法解析时会回退到关键词启发式搜索。
  - 读取对应世界节点/角色档案并累计上下文。
  - 再由 LLM 判断是否继续搜索；若不继续或没有新增条目则结束搜索。
- 阶段 2：决策是否更新。
  - 使用“已读取内容”作为主要上下文来判断是否更新世界/角色。
  - 若未读取到内容，则回退到完整角色/世界节点列表作为上下文。
- 阶段 3：命令校验（可重复）。
  - 先生成世界/角色更新命令（但不执行）。
  - 使用 LLM 判断命令是否合理；若不合理，则带原因返回到搜索与读取补充上下文。
- 阶段 4：执行更新。
  - `update_world = True`：调用 WorldAgent 的 `collect_actions/decide_actions` 与 `apply_updates`。
  - `update_characters = True`：调用 CharacterAgent 的 `collect_actions/decide_actions` 与 `apply_updates`。
  - 若更新了某个国家/政权或其子节点，则额外判断该政权绑定的角色是否需要更新。

## WorldAgent
- 输入：剧情文本 `update_info` 或查询 `query`。
- 查询流程 `extract_info`：
  - LLM 从世界树节点列表中选择最相关节点，返回对应内容。
- 更新流程：
  - `collect_actions` 优先处理“在某地区新增国家”的特定指令，返回明确的 ADD 步骤。
  - 若无特定指令，则 LLM 决策 ADD/UPDATE，解析后补充启发式推断。
  - `apply_update` 依据动作类型执行：
    - UPDATE：判断是否需要更新 key 与 value，并写入节点。
    - ADD：生成新节点或多级节点；宏观(Macro)树禁止新增。
    - 特例：当输入是“在「地区」新增「国家」”，若地区不存在先创建地区，再在该地区创建国家并补齐政权子节点。

## CharacterAgent
- 输入：剧情文本 `update_info` 或查询 `query`。
- 查询流程 `extract_info`：
  - LLM 从角色列表中选择最相关角色，返回档案内容。
- 更新流程：
  - `collect_actions/decide_actions` 决定新增或更新，确保 UPDATE 指向已有角色。
  - UPDATE：根据固定 JSON 字段更新角色档案。
  - ADD：
    - 匹配世界挂载点（地区/政权），构造角色蓝图。
    - 使用世界概要与角色设定生成角色档案，加入记录列表。
