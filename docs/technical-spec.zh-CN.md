# AwesomeRolePlayingGame 技术规格说明

## 概述
本文档定义了一个由多智能体协作、LLM 驱动的角色扮演游戏（RPG）框架的技术规格。该框架强调世界观一致性、分层剧情控制、可配置的规则/属性系统，以及可靠的存档/读档能力。

## 目标
- 通过多智能体协作支持多轮叙事式玩法。
- 通过前置世界观骨架与受控增量细化避免设定冲突。
- 通过分层剧情结构（Arc/Scene）保持叙事连贯性。
- 提供可配置的属性与判定系统，并支持显性/隐性展示。
- 支持完整的游戏状态持久化与加载。

## 智能体架构
### 核心智能体
1. **StateManagerAgent**
   - **职责**：读写全部状态文件；维护 schema 版本；向其他智能体提供快照。
   - **输入**：当前状态请求、增量更新（`delta`）。
   - **输出**：持久化结果、解析后的快照、冲突报告。

2. **WorldbuilderAgent**
   - **职责**：在初始化时生成世界观骨架；在游戏过程中增量扩展细节。
   - **输入**：世界主题/背景提示、当前世界状态、剧情触发条件。
   - **输出**：只扩展（不覆盖）现有设定的增量数据。

3. **PlotDirectorAgent**
   - **职责**：维护 Arc/Scene 分层结构；决定剧情推进与分支选择。
   - **输入**：当前剧情状态、用户行动、世界观上下文。
   - **输出**：Scene 迁移、Arc 完成、下一 Arc 骨架请求。

4. **RulesEngineAgent**
   - **职责**：根据角色/环境属性进行判定；解释判定结果。
   - **输入**：用户行动、属性、环境修正、可见性设置。
   - **输出**：判定结果（显性/隐性）、修正值、结果元数据。

5. **NarratorAgent**
   - **职责**：在世界观与剧情约束下输出叙事文本。
   - **输入**：世界状态、剧情状态、判定结果、用户行动。
   - **输出**：叙事文本与下一步交互提示。

### 智能体交互流程
1. **StateManagerAgent** 提供当前状态快照。
2. **PlotDirectorAgent** 根据用户行动确定 Arc/Scene 走向。
3. **RulesEngineAgent** 进行判定并返回结果。
4. **NarratorAgent** 输出叙事结果与选项。
5. **StateManagerAgent** 持久化世界/剧情/规则增量与新快照。

## 数据模型与目录结构
### 世界观数据（`world/`）
- `world/overview.json`：时代背景、世界规则、主题关键词。
- `world/regions.json`：大陆/区域与地理骨架。
- `world/nations.json`：国家/势力、政体与关系骨架。
- `world/tech_society.json`：科技、经济、文化、军事基线。

**约束**：
- 初始化仅生成骨架。
- 增量更新只允许填充空字段。
- 发生冲突时拒绝写入，并返回 `PlotDirectorAgent`。

### 剧情数据（`plot/`）
- `plot/arc_index.json`：所有 Arc 列表、状态、前置条件。
- `plot/arc_current.json`：当前 Arc 摘要、冲突、分支。
- `plot/scene_current.json`：当前 Scene 节点、地点、参与者。

**规则**：
- 每回合必须绑定一个 Scene。
- Arc 完成后触发新 Arc 骨架生成。

### 规则数据（`rules/`）
- `rules/attributes.json`：属性定义（依世界观而定）。
- `rules/checks.json`：判定规则（掷骰/阈值/概率）。
- `rules/visibility.json`：`explicit` 或 `implicit` 显示模式。

### 存档数据（`saves/<save_id>/`）
- `saves/<save_id>/world/*`
- `saves/<save_id>/plot/*`
- `saves/<save_id>/rules/*`
- `saves/<save_id>/state.json`：当前 Arc/Scene、角色快照、schema 版本。

## 初始化流程
1. 收集用户选择的主题/背景。
2. `WorldbuilderAgent` 生成世界观骨架文件。
3. `PlotDirectorAgent` 生成初始 Arc/Scene 骨架。
4. `RulesEngineAgent` 根据背景初始化属性与判定规则。
5. `StateManagerAgent` 持久化所有初始文件并返回快照。

## 世界观增量扩展
- 当 Scene 引用到尚未细化的区域/势力/系统时触发。
- `WorldbuilderAgent` 只填补缺失细节并记录版本。
- 扩展内容以增量形式附加，避免覆盖已有设定。

## 剧情推进规则
- 当用户行动达成关键目标时触发 Scene 迁移。
- 当 `arc_current.json` 中条件满足时结束 Arc。
- 新 Arc 骨架基于当前世界状态与玩家行为生成。

## 规则与判定
- 判定可采用确定性或概率性规则（由 `checks.json` 定义）。
- 结果展示取决于可见性设置：
  - **显性**：展示数值、掷骰、阈值与结果。
  - **隐性**：仅输出叙事后果。

## 冲突处理
- 当新增量与现有世界/剧情设定冲突时：
  - 拒绝写入增量。
  - 向 `PlotDirectorAgent` 返回冲突报告。
  - 叙事必须保持与既有状态一致。

## 版本管理
- 每个文件包含 `schema_version` 与 `last_updated` 元数据。
- 存档包含 `spec_version` 以确保兼容性。
