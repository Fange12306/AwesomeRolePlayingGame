# AwesomeRolePlayingGame

LLM 驱动的 RPG 世界/角色生成与更新框架，包含世界树生成、角色档案生成、剧情驱动的代理协作更新，以及一个本地 Web UI。

## 项目定位
- 目标：用结构化 JSON 记录世界设定与角色档案，便于持续扩展与更新。
- 适用场景：文字 RPG 世界观搭建、跑团素材整理、剧情更新与资料库维护。
- 输出产物：世界树快照、角色档案快照、角色关系边表、角色-地点边表、更新日志。

## 目录结构
- `world/`：世界生成与世界树结构（`WorldEngine`、`WorldAgent`、模板 `world_spec.md`）。
- `character/`：角色生成与更新（`CharacterEngine`、`CharacterAgent`、提示词模板）。
- `game/`：剧情驱动的更新协调（`GameAgent`）。
- `llm_api/`：OpenAI 客户端封装与日志。
- `web/` + `web_server.py`：本地 Web UI 与 REST API。
- `docs/`：系统设计文档与示例思维导图。
- `save/`：世界与角色快照。
- `log/`：LLM 与模块运行日志。
- `test/`：交互式示例与测试脚本。

## 环境与依赖
- Python 3.10+（建议）
- 依赖安装：
  - `python -m pip install -U openai python-dotenv`
  - 可选（测试）：`python -m pip install -U pytest`
- 环境变量（可放在 `.env`）：
  - `OPENAI_API_KEY`（必需）
  - `OPENAI_BASE_URL`（可选）
  - `OPENAI_MODEL`（可选，默认 `gpt-3.5-turbo`）

## 快速开始
### 1) 启动 Web UI
```bash
python web_server.py
```
浏览器访问 `http://localhost:6231`，可生成世界/角色并保存到 `save/`。

### 2) 命令行生成世界
```bash
python test/test_world.py
```
- 支持选择真实 LLM 或 Dummy 客户端。
- 自动生成 `docs/world_mindmap.md` 与 `save/world/*.json`。

### 3) 命令行生成角色
```bash
python test/test_character.py
```
- 依赖已有世界快照。
- 输出角色快照到 `save/characters/*.json`。

### 4) 剧情驱动更新（API）
- 通过 `POST /api/game/plan` 输入剧情文本。
- 可设置 `apply=false` 仅查看决策，不落盘。

## 核心概念与数据结构
### 世界树（World Tree）
- 根节点为 `world`，包含 `macro` 与 `micro` 两棵子树。
- `macro`：基于 `world/world_spec.md` 生成的宏观维度与子节点。
- `micro`：地区（`micro.r1`...）与政权（`micro.r1.p1`...），每个政权默认含 7 个固定子节点（文化/经济/政治/人口/地理/技术/资源）。

### 角色档案（Character Record）
- 记录字段：`id`, `region_id`, `polity_id`, `profile`。
- `profile` 为 JSON 字段集合：`name, summary, background, motivation, conflict, abilities, weaknesses, relationships, hooks, faction, profession, species, tier`。
- 关系数据独立保存为边表：`relations` 与 `character_location_edges`。

### 更新动作（Agents）
- 世界更新标签：`<|ADD_NODE|>`, `<|UPDATE_NODE|>`, `<|REMOVE_NODE|>`。
- 角色更新标签：`<|ADD_CHARACTER|>`, `<|UPDATE_CHARACTER|>`。
- `GameAgent` 负责搜索上下文、判断是否更新、校验命令并调用世界/角色代理。

## 生成与更新流程概览
### WorldEngine
1. 解析 `world/world_spec.md` 生成 macro 节点。
2. 逐个 macro 节点调用 LLM 填充 `value`。
3. 生成 macro 总结用于微观生成上下文。
4. 根据 `micro_scale` 生成地区与政权结构。
5. 为 micro 节点补全内容并保存快照。

### CharacterEngine
1. 从世界快照 `micro` 子树提取挂载点（region/polity）。
2. 生成角色蓝图并调用 LLM 输出角色档案 JSON。
3. 基于角色清单生成关系边表。
4. 生成角色-地点边表（规则边 + LLM 补充边）。
5. 保存角色快照。

### GameAgent
1. 搜索并读取相关世界节点/角色档案。
2. 判断是否需要世界/角色更新。
3. 生成更新动作并进行合理性校验。
4. 执行更新并在政权变更时联动更新角色档案。

## 快照与日志
- 世界快照：`save/world/*.json`
- 角色快照：`save/characters/*.json`
- LLM 日志：`log/llm.log`
- 模块日志：`log/world_engine.log`, `log/world_agent.log`, `log/character_engine.log`, `log/character_agent.log`, `log/game_agent.log`, `log/web_server.log`

## Web API 详细说明
### GET
- `/api/world`
  - 返回：`{ok, snapshot, save_path}`
  - 404：`no_snapshot`
- `/api/world/status`
  - 返回世界生成任务状态（`status`, `message`, `phase`, `macro_total`, `micro_total`, `stage_completed`, `stage_total`, `ready`）
- `/api/updates`
  - 返回最近世界/角色更新信息与修订号
- `/api/world/snapshots`
  - 返回世界快照列表（含 `name`, `path`, `full_path`, `mtime`）
- `/api/characters/snapshots`
  - 返回角色快照列表
- `/api/characters?path=...`
  - 读取指定角色快照
  - 400：`missing_path`
  - 404：`snapshot_not_found`
- `/api/progress?id=...`
  - 查询后台任务进度（`status`, `completed`, `total`, `message`, `kind`）

### POST
- `/api/generate`
  - 入参：`{prompt, scale}`；`scale` 取 `small|medium|large`
  - 返回：`{ok, job_id, total}`，后台异步生成
- `/api/characters/generate`
  - 入参：`{snapshot, total, pitch}`
  - 返回：`{ok, job_id, total}`
- `/api/import`
  - 入参：`{content, filename}`
  - `content` 为 JSON 字符串，自动规范化后写入快照
- `/api/update`
  - 入参：`{identifier, value}`
  - 409：`world_generation_running`
- `/api/game/plan`
  - 入参：`{text, apply}`；`apply` 默认为 `true`
  - 返回：决策与动作列表；`apply=false` 时只做规划不落盘

## 测试脚本
- `python test/test_world.py`：世界生成与节点操作示例，支持 Dummy LLM。
- `python test/test_character.py`：角色生成示例。
- `python test/test_world_agent.py`：世界代理查询/更新测试。
- `python test/test_character_agent.py`：角色代理更新测试。
- `python test/test_game_agents.py`：游戏代理联动测试。
- `python test/test_polity_merge.py`：政权合并流程测试。

## 常见限制与注意事项
- LLM 输出非确定性，解析失败时会回退为原始文本或使用启发式策略。
- Macro 树默认禁止新增节点；新增会被降级为 UPDATE。
- 世界模板 `world/world_spec.md` 的修改会直接影响宏观节点结构与内容生成。
- Web API 默认使用本地文件快照作为状态来源，请确保 `save/` 目录可写。
