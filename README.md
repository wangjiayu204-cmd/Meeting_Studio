# 智能会议与任务闭环管理

当前完成 **P4：分析与审核**。包含 Manager / Planner / Reviewer 的 LangGraph 工作流、程序校验、有限返工、持久化草稿版本与人工编辑复核。Executor、看板、后台调度、SSE 与前端按后续阶段交付。

实施步骤见 [模块化实施方案.md](模块化实施方案.md)。

## 运行

环境由用户配置，应用不会安装依赖。使用已有 `demo1`：

```bash
conda activate demo1
cd /app/data/wjy/demo
bash scripts/run.sh
```

访问：

- 健康检查：http://127.0.0.1:8080/api/health
- 模型连接检查：http://127.0.0.1:8080/api/model/health
- Swagger：http://127.0.0.1:8080/docs
- OpenAPI：http://127.0.0.1:8080/openapi.json

当前没有首页；根路径返回统一格式的 404。健康检查验证数据库版本及表可访问性，数据库正常时为 `ok`，异常返回 503。模型为 `not_checked`，不会发起模型请求。

自定义端口或监听地址：

```bash
PM_PORT=8081 bash scripts/run.sh
```

默认仅本机访问。浏览器在另一台机器时可用 SSH 端口转发；需要监听其他地址时由部署者设置 `PM_HOST`。

## 配置

配置优先级：显式参数（测试 / 应用工厂）> 环境变量 > 项目根目录 `.env` > 默认值。可复制 `.env.example` 为 `.env` 后修改，密钥不纳入 Git。

模型地址默认 `http://127.0.0.1:8000/v1`，名称 `qwen3-8b`。切换 DCU 修改 `PM_LLM_BASE_URL` 和必要的鉴权。HTTPX 使用 `trust_env=False`，模型请求直接访问配置地址，不读取环境代理。启动只创建客户端，不自动请求模型；模型离线不影响历史会议查询。

数据库相对路径始终基于项目根目录解析，不随启动目录变化。启动自动初始化 `data/pm.sqlite3`，启用外键和 WAL，以 `PRAGMA user_version` 管理版本；当前 v2，在已有 v1 上自动新增运行、分析和草稿表，保留会议数据。遇到高于支持版本的数据库拒绝启动。升级前停服备份；代码回退至 P2 前须同时恢复 v1 数据库备份。数据库不提交 Git。日志只记录基础状态与请求 ID，不输出模型密钥或完整会议内容。

## 会议接口

所有路径以 `/api` 开头，Swagger 可直接填写参数测试：

| 方法与路径 | 功能 |
|---|---|
| `POST /api/meetings` | JSON 创建会议，返回 201、会议 ID 和段落 |
| `POST /api/meetings/upload` | multipart 上传文档及元信息 |
| `GET /api/meetings?limit=20&offset=0` | 历史分页，返回 items / total / limit / offset |
| `GET /api/meetings/{id}` | 原文、稳定段落 ID 和人员等详情 |

创建会议示例：

```bash
curl --noproxy '*' http://127.0.0.1:8080/api/meetings \
  -H 'Content-Type: application/json' \
  -d '{"title":"登录模块例会","meeting_date":"2026-10-21","text":"张三负责本周完成登录接口联调，提交接口文档。"}'
```

上传示例（在项目根目录执行）：

```bash
curl --noproxy '*' http://127.0.0.1:8080/api/meetings/upload \
  -F 'file=@examples/meeting.txt' \
  -F 'title=登录模块迭代例会' \
  -F 'meeting_date=2026-10-21' \
  -F 'timezone=Asia/Shanghai' \
  -F 'members=[{"name":"张三","role":"后端"}]'
```

日期必填；时区省略时使用 `PM_TIMEZONE`。上传 members 是 JSON 数组字符串；JSON 创建时 members 直接传数组。历史列表不返回全文，查看详情获取原文。输入只保存会议，不自动调用模型。

默认文件上限 10 MiB、文本 24000 字符、PDF 100 页，均可配置。TXT 支持 UTF-8、UTF-8 BOM 和 GB18030。扫描件、加密或损坏 PDF 明确报错；不支持的类型返回 415，超限返回 413，解析或参数错误返回 422。请求体还有总大小保护，防止解析前超大上传。

原文换行规范化、移除 NUL 字符并去除首尾空白；按非空行生成 `p0001` 等编号。原文与段落原子保存，不保存上传文件到用户指定路径。耗时解析和同步数据库操作在线程中运行。应用重启后数据保留，原文暂不提供修改接口以保证引用稳定。

## 分析、审核与草稿

| 方法与路径 | 功能 |
|---|---|
| `POST /api/meetings/{id}/analysis` | 运行三角色分析，返回最终草稿 |
| `GET /api/meetings/{id}/analysis` | 最新已保存的会议分析 |
| `GET /api/meetings/{id}/draft` | 当前草稿 |
| `GET /api/meetings/{id}/drafts` | 全部历史版本，含返工中间版本 |
| `PUT /api/meetings/{id}/draft` | 提交 base_version、tasks、resolution_note，生成待复核新版本 |
| `POST /api/meetings/{id}/draft/review` | 提交 draft_version，对当前草稿重新审核并产生新版本 |
| `GET /api/runs/{id}` | 查看成功、失败、中断及返工次数 |

P4 分析与复核在当前 HTTP 请求内异步等待，HTTP 成功为 200；运行记录持久化，响应中草稿附 run_id。P6 再提供返回 202 的后台调度和 SSE，当前不要调用尚未实现的 `POST /runs`。

```bash
curl --noproxy '*' --max-time 930 -X POST \
  http://127.0.0.1:8080/api/meetings/替换为会议ID/analysis
```

Manager 提取议题和决议；Planner 生成任务及来源；程序验证引用、ID、缺失依据、日期与依赖图；Reviewer 对照原文作语义审核。有需修正问题时最多返工 `PM_MAX_REVISIONS=2` 次。整体限时 `PM_WORKFLOW_TIMEOUT=900` 秒。单次模型调用仍受自身时间预算限制。

每次 Planner 输出先存为 pending_review，每次审核结果新增版本，因此最终版本不一定为 1。状态 ready 表示程序和语义审核均通过；needs_attention 表示仍需人工处理；运行 succeeded 只表示流程结束。未知负责人 / 日期保留空值并提示确认，不强迫模型填造。P5 再实现问题接受与执行门槛，P4 尚不可创建看板任务。

编辑必须携带当前 base_version，过期返回 409；编辑后旧审核失效，需提交新版本复核。人工改变负责人 / 日期时必须提供 resolution_note，服务器将对应依据标记为 human；模型不允许伪造人工确认。原文引用仍必须有效。当前复核会产生新的审核记录，不修改历史版本。

会议同一时刻最多一个运行；分析期间编辑 / 再运行返回 409。失败会保留已经完成的分析和草稿，运行标记 failed；超时取消、应用重启后遗留运行标记 interrupted，可显式重新分析。服务使用单 worker，避免多个实例相互干扰运行状态。

端到端验证：

```bash
python scripts/smoke_planning.py
```

脚本用临时数据库和真实 vLLM 检查分析、任务、审核、编辑、复核、旧版冲突及重启持久化；所有接口和工作流记录保留。真实模型可能产生不合理的审核意见，此时脚本明确失败，草稿停留 needs_attention，不把模型结果伪装成通过。

## 模型调用与中间测试记录

`GET /api/model/health` 检查 `/v1/models` 是否包含配置的模型名称，正常时返回 model、latency_ms 和 trace_id；它不代表推理效果测试。真实推理请运行：

```bash
python scripts/smoke_model.py
```

该脚本会验证真实 FastAPI 模型健康接口，再调用 vLLM 提取示例会议的两个明确行动项，检查负责人、日期与引用，并打印保存路径。测试数据库和临时服务自动清理，中间记录保留。

记录统一保存在 [test_artifacts](test_artifacts/README.md)：

- `api/<时间_编号_标签>/`：接口测试各步骤响应、服务日志、总结果。
- `llm/<时间_编号_标签>/`：提示词、Schema、每次请求、原始响应、清洗正文、校验问题、解析结果与总结果。
- `workflows/<时间_编号_标签>/`：每个角色输出、关联模型 trace_id、各轮草稿和整体结果，context.json 含 run_id。
- 优先查看 `summary.json` 判断成败，再查看 `01_request.json`、`01_response.json` 和 `result.json`。重试 / 降级 / 修复按 02、03 等编号保存。
- 接口错误包含 trace_id，可以按该 ID 在记录中查找对应调用；正常日志不输出会议全文。

本地开发默认启用模型调用记录；设置 `PM_LLM_TRACE_ENABLED=false` 可关闭，`PM_TEST_ARTIFACTS_PATH` 可更改目录。记录不提交 Git，也不通过 HTTP 提供下载。鉴权头不保存，配置密钥做文本替换；记录保留会议输入及原始模型输出，可能包含 think 标签，供你人工排查。

模型客户端 `generate_structured(messages, schema)` 使用 JSON Schema 输出请求；服务明确不支持时降级为 JSON 提示词。兼容 think 标签与 JSON 围栏，结果经 Pydantic 校验；格式错误最多修复一次。重复 JSON 键、非标准 NaN、任意前后解释不被静默接受。输出截断、空正文、上下文超限及鉴权错误单独报告。

默认单请求超时 `PM_LLM_TIMEOUT=180` 秒，整体预算 `PM_LLM_TOTAL_TIMEOUT=240` 秒（包含排队、重试及格式修复），临时错误重试上限 `PM_LLM_MAX_RETRIES=2`，同时调用上限 `PM_MAX_CONCURRENT_RUNS=1`。模型列表健康检查整体最多 10 秒。

## 数据契约

- `app/domain/schemas.py`：会议、段落、分析、草稿、证据、审核、运行、任务、执行与事件模型。
- `app/domain/states.py`：运行 / 草稿 / 执行 / 看板状态及角色与事件枚举。
- 未知负责人 / 日期可空；人工来源必须填写说明；审核版本必须匹配草稿。
- 字段级验证在 P1 完成；P4 增加真实引用、依赖图、审核和草稿并发版本检查；P5 实现执行门槛。

错误结构：

```json
{
  "error": {"code": "not_found", "message": "Not Found", "details": []},
  "request_id": "服务端生成的请求ID"
}
```

响应头 `X-Request-ID` 与错误中的 ID 一致，可用于定位问题。参数错误不会回显原始输入。

## 验证

```bash
conda activate demo1
cd /app/data/wjy/demo
python -m pytest
bash -n scripts/run.sh
python scripts/smoke_meetings.py
python scripts/smoke_model.py
python scripts/smoke_planning.py
```

`pytest` 离线执行，无需模型服务，数据库与模型记录使用测试临时目录；包含 PDF、SQLite 事务、模型客户端受控响应和失败边界验证。`smoke_meetings.py` 使用真实临时服务检查会议接口及重启持久化，不请求模型；`smoke_model.py` 需要真实 vLLM。两个脚本都保留记录在 test_artifacts 下。依赖声明在 `requirements.txt`；验收全项目后再生成环境快照。
