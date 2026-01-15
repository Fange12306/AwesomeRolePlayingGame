# AwesomeRolePlayingGame

LLM 驱动的 RPG 世界/角色生成与更新框架，提供世界引擎、角色引擎、代理协作更新流程，以及一个简易 Web UI。

## 主要模块
- `world/`：世界生成与世界树结构（`WorldEngine`）。
- `character/`：角色生成、关系/地点边表（`CharacterEngine`）。
- `game/`：剧情驱动的更新协调（`GameAgent`）。
- `web_server.py` + `web/`：本地 Web UI 与 REST API。
- `llm_api/`：OpenAI 客户端封装与日志。

## 环境与依赖
- Python 3.10+（建议）
- 依赖安装：
  - `python -m pip install -U openai python-dotenv`
  - 可选：`python -m pip install -U pytest`
- 环境变量（可放在 `.env`）：
  - `OPENAI_API_KEY`（必需）
  - `OPENAI_BASE_URL`（可选）
  - `OPENAI_MODEL`（可选，默认 `gpt-3.5-turbo`）

## 使用方式
### 启动 Web UI
```bash
python web_server.py
```
浏览器访问 `http://localhost:6231`，生成世界/角色并保存到 `save/`。

### 命令行示例
```bash
python test/test_world.py
python test/test_character.py
```
脚本为交互式流程，可选择真实 LLM 或 Dummy 客户端。

## 快照与日志
- 世界快照：`save/world/*.json`
- 角色快照：`save/characters/*.json`
- LLM 日志：`log/llm.log`
- 其他模块日志：`log/*.log`

## Web API 概览
GET：
- `/api/world`：当前世界快照
- `/api/world/status`：世界生成任务状态
- `/api/updates`：世界/角色最近更新信息
- `/api/world/snapshots`：世界快照列表
- `/api/characters/snapshots`：角色快照列表
- `/api/characters?path=...`：读取指定角色快照
- `/api/progress?id=...`：任务进度

POST：
- `/api/generate`：生成世界（`prompt`, `scale`）
- `/api/characters/generate`：生成角色（`snapshot`, `total`, `pitch`）
- `/api/import`：导入世界快照（`content`, `filename`）
- `/api/update`：更新单个世界节点（`identifier`, `value`）
- `/api/game/plan`：剧情驱动的更新决策与应用（`text`, `apply`）
