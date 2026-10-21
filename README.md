# 智能会议与任务闭环管理

当前完成 **P8 页面实现与服务器验证**：原生 HTML / CSS / JavaScript 工作台已贯通会议输入、四角色进度、草稿编辑复核、人工确认、看板、跟进和下载。服务器没有浏览器，视觉布局与完整点击验收尚待在客户端浏览器执行，清单见 [页面使用与浏览器验收](docs/页面使用与浏览器验收.md)。

实施步骤见 [模块化实施方案.md](模块化实施方案.md)。

## 运行

环境由用户配置，应用不会安装依赖。使用已有 `demo1`：

```bash
conda activate demo1
cd /app/data/wjy/demo
bash scripts/run.sh
```

访问：

- 工作台：http://127.0.0.1:8080/
- 健康检查：http://127.0.0.1:8080/api/health
- 模型连接检查：http://127.0.0.1:8080/api/model/health
- Swagger：http://127.0.0.1:8080/docs
- OpenAPI：http://127.0.0.1:8080/openapi.json

首页与 /static 由 FastAPI 托管，无需 npm、打包器或外部 CDN。应用运行只需要 demo1，Node 仅用于可选的前端测试。健康检查验证数据库版本及表可访问性，数据库正常时为 `ok`，异常返回 503；模型为 `not_checked`，不会发起推理。页面可单独点击“检查模型连接”。

自定义端口或监听地址：

```bash
PM_PORT=8081 bash scripts/run.sh
```

默认仅本机访问。浏览器在另一台机器时可用 SSH 端口转发；需要监听其他地址时由部署者设置 `PM_HOST`。

例如在你自己的电脑终端执行（用户名和服务器地址换成实际值）：

```bash
ssh -N -L 8080:127.0.0.1:8080 用户名@服务器地址
```

随后在电脑浏览器打开 `http://127.0.0.1:8080/`；使用 VS Code Remote 时也可在“端口”面板转发 8080。若本地端口被占用，可改为 `-L 18080:127.0.0.1:8080` 并访问 18080。转发目标需能访问运行应用的容器端口。vLLM 保持 8000，页面服务使用 8080。

## 工作台操作（P8）

页面使用深色品牌横幅、会议档案侧栏、Agent 状态卡片、原文面板与产物标签页，适配桌面和窄屏；所有字体与图形均来自本地样式或 SVG。会议信息与模型正文按文本节点渲染，HTML 不作为可执行内容插入。

1. 新建会议：输入名称、日期、IANA 时区及可选参会人；粘贴纪要或拖拽 TXT / 文本型 PDF。点击“体验示例”可填入固定日期的示例，点击“创建会议”仅保存输入。
2. 分析：核对原文后启动智能分析。查看四个 Agent 的状态、实时事件、运行记录与错误编号；SSE 断线自动重连，补读去重，轮询作为终态回退。切换会议会关闭旧订阅，迟到响应不会写入新会议。
3. 核对与编辑：点击段落引用可定位原文；表单支持任务增删、负责人、截止日期、交付物、说明与优先级，展开细节可修改依赖、引用及待确认事项。人工补充或修改负责人 / 日期需要说明。保存产生新版本，必须再次复核。
4. 处理审核：程序硬错误需修改；其他问题可以逐项勾选并填写接受理由。页面不会自动接受意见。历史草稿只读，未保存修改、过期版本与活跃运行会阻止执行；请求失败保留表单，版本冲突提示先处理本地内容再刷新。
5. 确认执行：复核通过后，勾选核对声明并在确认弹窗中确认当前版本。成功才进入正式看板；已执行会议不再规划。运行失败可重试，执行重试需要再次确认。
6. 跟进与导出：看板支持按负责人筛选和状态更新；跟进页展示逾期、到期、待办和澄清项；可下载完整 JSON 与截止日期 ICS。会议 URL 中的 meeting 参数支持刷新与分享，历史列表分页读取。

会议草稿编辑保存在当前页面内存，未保存离开时会提示；不把会议全文写入浏览器本地存储。自动刷新进度不会覆盖尚未保存的草稿。当前工作台读取最近 100 条运行记录，完整历史仍可通过分页 API 查询。

```bash
# 前端模块自动测试，现有 Node 18+ 即可；不安装依赖
node --test tests/frontend/*.test.mjs
# 前端实际 API / 状态 / SSE 模块调用真实 vLLM，产物保留
python scripts/smoke_interface.py
```

`smoke_interface.py` 启动隔离服务与数据库，使用 Node 运行实际前端模块，覆盖上传、SSE 断线补读、编辑复核、旧版本拒绝、确认幂等、状态更新、导出及重启。测试中如遇 Reviewer 误报，只在已知样例的负责人、日期、交付物、依赖和引用全部核验一致且不存在程序错误后，模拟用户附理由接受；记录 raw review、接受后的版本与接受数量。这属于测试流程，产品页面不会自动放行。真实记录中保留失败，不能把流程测试通过解释为模型始终正确。服务器验证不涵盖浏览器 DOM 渲染、布局、键盘与实际下载交互。

## 配置

配置优先级：显式参数（测试 / 应用工厂）> 环境变量 > 项目根目录 `.env` > 默认值。可复制 `.env.example` 为 `.env` 后修改，密钥不纳入 Git。

模型地址默认 `http://127.0.0.1:8000/v1`，名称 `qwen3-8b`。切换 DCU 修改 `PM_LLM_BASE_URL` 和必要的鉴权。HTTPX 使用 `trust_env=False`，模型请求直接访问配置地址，不读取环境代理。启动只创建客户端，不自动请求模型；模型离线不影响历史会议查询。

数据库相对路径始终基于项目根目录解析，不随启动目录变化。启动自动初始化 `data/pm.sqlite3`，启用外键和 WAL，以 `PRAGMA user_version` 管理版本；当前 v4，在已有 v1 / v2 / v3 上自动升级，保留已有数据，新增事件和后台请求记录。遇到高于支持版本的数据库拒绝启动。升级前停服备份；回退至旧代码前须同时恢复对应版本数据库备份。数据库不提交 Git。日志只记录基础状态与请求 ID，不输出模型密钥或完整会议内容。

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

原有分析与复核接口仍在当前 HTTP 请求内异步等待，HTTP 成功为 200，响应草稿附 run_id。推荐页面和长任务使用 P6 后台接口，立即获得 202 和 run_id，再监听 SSE；两种入口共享会议锁和持久化事件。

```bash
curl --noproxy '*' --max-time 930 -X POST \
  http://127.0.0.1:8080/api/meetings/替换为会议ID/analysis
```

Manager 提取议题和决议；Planner 生成任务及来源；程序验证引用、ID、缺失依据、日期与依赖图；Reviewer 对照原文作语义审核。有需修正问题时最多返工 `PM_MAX_REVISIONS=2` 次。整体限时 `PM_WORKFLOW_TIMEOUT=900` 秒。单次模型调用仍受自身时间预算限制。

每次 Planner 输出先存为 pending_review，每次审核结果新增版本，因此最终版本不一定为 1。状态 ready 表示审核问题均已处理；needs_attention 表示仍需人工处理；运行 succeeded 只表示流程结束。未知负责人 / 日期保留空值并提示确认，不强迫模型填造。P5 支持附理由接受可接受问题，程序硬错误必须修正。

编辑必须携带当前 base_version，过期返回 409；编辑后旧审核失效，需提交新版本复核。人工改变负责人 / 日期时必须提供 resolution_note，服务器将对应依据标记为 human；模型不允许伪造人工确认。原文引用仍必须有效。当前复核会产生新的审核记录，不修改历史版本。

会议同一时刻最多一个运行；分析期间编辑 / 再运行返回 409。失败会保留已经完成的分析和草稿，运行标记 failed；超时取消、应用重启后遗留运行标记 interrupted，可显式重新分析。服务使用单 worker，避免多个实例相互干扰运行状态。

端到端验证：

```bash
python scripts/smoke_planning.py
```

脚本用临时数据库和真实 vLLM 检查分析、任务、审核、编辑、复核、旧版冲突及重启持久化；所有接口和工作流记录保留。真实模型可能产生不合理的审核意见，此时脚本明确失败，草稿停留 needs_attention，不把模型结果伪装成通过。

## 确认执行与看板

| 方法与路径 | 功能 |
|---|---|
| `POST /api/meetings/{id}/draft/accept-issues` | 按问题 ID 和理由接受语义意见 / 程序警告，新增版本 |
| `POST /api/meetings/{id}/execute` | 明确确认当前版本，Executor 调用本地任务工具 |
| `GET /api/meetings/{id}/execution` | 查看最近执行结果、run_id、任务 ID 和错误 |
| `GET /api/tasks` | 看板分页，支持 meeting_id / status / owner 筛选 |
| `PATCH /api/tasks/{id}` | 更新 todo / in_progress / done 状态 |

执行示例（先查询最新草稿版本）：

```bash
curl --noproxy '*' -X POST http://127.0.0.1:8080/api/meetings/替换为会议ID/execute \
  -H 'Content-Type: application/json' \
  -d '{"draft_version":2,"confirm":true,"acknowledgement":"已核对并确认创建任务"}'
```

存在待处理问题时，必须先修正并复核，或对可接受问题逐条提交理由：

```json
{
  "draft_version": 2,
  "resolutions": [
    {"issue_id": "替换为审核问题ID", "note": "已人工核实，允许当前暂不指定截止日期"}
  ]
}
```

只有完成 Reviewer 复核的草稿可接受问题；未审核草稿不能跳过复核。接受会生成新版本，执行时使用新版本号。无效引用、依赖环等程序硬错误不能接受；Executor 运行前和工具事务内再次校验。

Executor 只选择 `create_tasks`，参数为空；服务端绑定会议、版本和任务内容，拒绝模型额外传入任务或任意工具。任务、执行成功记录、工具成功记录与草稿 confirmed 状态在一个事务内提交。模型称“已执行”不会产生成功状态，必须有工具落库结果。

同一会议只执行一批任务，重复请求同一版本直接返回原结果，不再调用模型。并发执行中重复请求返回 409，结束后重试获取已有结果；旧版本始终返回 409。工具失败整批回滚，模型失败不创建任务，可再次确认重试（execution_id 复用、run_id 更新，历史运行保留）。已执行会议首版禁止编辑或重新分析。空任务列表允许确认并完成，创建数为 0。

看板状态可来回更新，并记录 updated_at；首版不自动限制依赖任务状态，依赖信息供查看与跟进。执行成功后任务内容是持久化快照，暂只开放状态更新。

```bash
python scripts/smoke_execution.py
```

该脚本通过真实模型完成三角色审核和 Executor 工具选择，验证两项任务、旧版阻止、重复确认、状态更新及重启保留。中间产物保存在 `test_artifacts/api/..._execution_smoke/`，包含 reviewed_draft、execution 和 board；`test_artifacts/workflows/..._execution/` 包含 Executor 关联 trace_id 与真实工具结果。

## 后台运行、SSE 与重试

| 接口 | 用途 |
|---|---|
| `POST /api/meetings/{id}/runs` | 提交分析 / 复核 / 执行，返回 202 和 Run |
| `GET /api/meetings/{id}/runs?limit=20&offset=0` | 分页查看会议运行历史（数组） |
| `GET /api/runs/{id}` | 查询 queued / running / succeeded / failed / interrupted |
| `GET /api/runs/{id}/events` | SSE，补读持久化事件并等待新事件 |
| `POST /api/runs/{id}/retry` | 为失败 / 中断的后台运行创建新 run_id |

三种提交正文：

```json
{"type":"analysis"}
```

```json
{"type":"review","draft_version":3}
```

```json
{"type":"execution","draft_version":4,"confirm":true,"acknowledgement":"已核对，确认创建任务"}
```

返回时运行先持久化为 queued，工作线程槽可用后变为 running；同一会议最多一个活跃运行，重复提交返回 409。执行已成功时重复提交返回原 run_id，HTTP 仍为 202，不再创建任务。队列含正在运行项，默认上限 `PM_MAX_PENDING_RUNS=20`，满时返回 503；后台同时运行数为 `PM_MAX_CONCURRENT_RUNS=1`，原等待式入口也受模型客户端并发限制。

```bash
curl --noproxy '*' -X POST http://127.0.0.1:8080/api/meetings/会议ID/runs \
  -H 'Content-Type: application/json' -d '{"type":"analysis"}'

curl --noproxy '*' -N http://127.0.0.1:8080/api/runs/运行ID/events
```

SSE 事件有递增 id，类型包括 run_queued、run_started、agent_started、agent_completed、review_feedback、tool_result、run_completed、run_failed 和 run_interrupted。data 为 Event JSON，不包含模型内部思考。终态事件与运行状态在同一事务写入；连接在补读完成且运行终止后关闭。运行成功不等于草稿 ready，页面需读取草稿审核状态。

断开 SSE 不会取消后台工作。重连使用 `Last-Event-ID: 上次ID` 或 `?after=上次ID`；两者同时存在取较大值，只返回更大的 ID。事件 ID 为全库递增，不必连续。默认轮询 0.3 秒、空闲心跳 10 秒，可通过 `PM_SSE_POLL_INTERVAL` 和 `PM_SSE_HEARTBEAT_SECONDS` 调整。浏览器 EventSource 监听具名事件，收到终态应主动 close，避免自动重连。

重试分析 / 复核正文为 `{}`。执行重试必须重新给出 `{"confirm":true,"acknowledgement":"再次核对并确认"}`，不会自动复用上次授权。复核 / 执行保留原草稿版本，期间已编辑则返回 409，需通过新版本重新提交。只允许失败或中断重试；原有等待式入口无后台请求记录，失败后通过原入口重新调用。

队列属于单进程内存调度，运行和事件持久化。服务停止会取消未完成工作并标记 interrupted，启动也会处理崩溃残留；不会自动继续执行，需显式重试。已原子提交的执行保留 succeeded，不因停服或重复连接撤回。始终使用单 Uvicorn worker。

```bash
python scripts/smoke_runs.py
```

真实验证会主动断开并重连 SSE，对比补读事件，完成后台分析和执行，重启后再验证事件重放与任务保留。全过程留在 `test_artifacts/api/..._runs_smoke/`。

## 导出与会议跟进（P7）

以下接口均不调用模型，也不修改任务：

| 接口 | 内容 |
|---|---|
| `GET /api/meetings/{id}/export?format=json` | 下载同一数据库快照中的会议原文、段落、最近分析及 run_id、当前草稿、执行记录和全部正式任务 |
| `GET /api/meetings/{id}/export?format=ics` | 下载确认执行成功后的任务截止日期日历，未执行或失败时返回 409 |
| `GET /api/meetings/{id}/followup` | 按会议 IANA 时区返回完成、待办、逾期、今日到期、缺失信息与待澄清事项 |

JSON 可在分析 / 执行前下载，尚无数据的字段为空；当前草稿与正式 tasks 分开，JSON 是查看与备份产物，暂不提供导入恢复接口。多表在同一读取事务内生成，任务不受看板分页限制。最近分析可能来自尚未产出草稿的运行，可通过 analysis_run_id 与 draft.run_id 区分。

ICS 使用 UTF-8、CRLF、75 字节折行及文本转义，每个任务生成稳定 UID 的 VEVENT。截止日期表示全天事件，DTEND 为次日（不包含）；日期不做 UTC 偏移。LAST-MODIFIED 表示任务更新时间，X-PM-TASK-STATUS 保存看板状态，完成项仍保留截止日期。日历客户端对重复导入的更新策略各异，系统不承诺自动同步。此功能只下载文件，不发送邀请或接入企业日历。

缺少日期和无法表示次日的 9999-12-31 项不伪造日程：响应头 X-PM-Task-Count / X-PM-Skipped-Count 给出导出 / 跳过数量，文件中的 X-PM-SKIPPED-TASK 列出任务 ID 与原因；JSON 仍保留这些任务。成功执行的空计划可下载有效空日历。文件名只包含服务器会议 ID，下载返回 Cache-Control: no-store。

跟进以请求时刻换算为会议当地日期：未完成且截止日期严格早于当天才算逾期；今日到期单列。overdue 与 due_today 都是 pending 的子集，不能重复相加；缺失负责人 / 日期计数也只统计未完成任务。未执行草稿不计入正式任务数，其缺失字段与 needs_confirmation 仍列为 origin=draft 的澄清项。报告保留分析未决议题、开放审核问题、任务引用及所有审核问题的处理说明，已接受问题不会再次列为开放问题。生成规则为确定性计算，不向模型请求编造后续安排。

本地工具分为创建工具白名单（create_tasks）和只读工具白名单（export_calendar / get_task_status），查询工具的会议由服务端绑定，不接受会议 ID 参数。当前导出和跟进由用户通过 HTTP 发起，创建任务流程只允许选择 create_tasks。

```bash
curl --noproxy '*' -OJ 'http://127.0.0.1:8080/api/meetings/会议ID/export?format=json'
curl --noproxy '*' -OJ 'http://127.0.0.1:8080/api/meetings/会议ID/export?format=ics'
curl --noproxy '*' 'http://127.0.0.1:8080/api/meetings/会议ID/followup'
python scripts/smoke_exports.py
python scripts/smoke_interface.py
```

真实验证目录 `test_artifacts/api/..._exports_smoke/` 包含 03_meeting_export.json、可导入的 04_tasks.ics、05_followup.json、重启后结果及全部 HTTP 交换和模型 / 工作流关联记录。

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
python scripts/smoke_execution.py
python scripts/smoke_runs.py
python scripts/smoke_exports.py
```

`pytest` 离线执行，无需模型服务，数据库与模型记录使用测试临时目录；包含 PDF、SQLite 事务、模型客户端受控响应和失败边界验证。`smoke_meetings.py` 使用真实临时服务检查会议接口及重启持久化，不请求模型；`smoke_model.py` 需要真实 vLLM。两个脚本都保留记录在 test_artifacts 下。依赖声明在 `requirements.txt`；验收全项目后再生成环境快照。
