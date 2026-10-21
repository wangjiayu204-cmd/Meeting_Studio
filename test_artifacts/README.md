# 中间测试与模型调用记录

本目录保留测试过程，方便人工检查。说明文件提交 Git，实际记录目录不提交。

```text
test_artifacts/
├── llm/<时间_调用ID_标签>/
│   ├── context.json             # 配置摘要、模型、提示词版本，不含密钥
│   ├── 01_request.json          # 单次 HTTP 方法、路径、请求体
│   ├── 01_response.json         # 状态码、耗时、原始响应体（可能含 think 标签）
│   ├── 01_content.txt           # 移除思考标签与围栏后的模型正文
│   ├── 01_validation.json       # JSON / Schema 校验问题（如有）
│   ├── result.json              # 校验后的结构化结果
│   └── summary.json             # 成功 / 失败、耗时、错误码、调用ID
├── workflows/<时间_编号_标签>/
│   ├── context.json             # 会议输入和运行ID
│   ├── 01_manager.json          # 角色产物和模型 trace_id
│   ├── 02_planner.json          # 任务草稿
│   ├── 02_draft.json            # 已落库的版本快照
│   ├── 03_reviewer.json         # 审核、程序问题与关联 trace_id
│   ├── result.json
│   └── summary.json             # 最后版本、返工次数和运行结果
└── api/<时间_测试ID_标签>/
    ├── context.json
    ├── ...                      # 冒烟脚本保存各步骤的请求 / 响应
    └── summary.json             # 总体验证结果与关联模型调用ID
```

发生重试、结构化参数降级或格式修复时，后续请求按 02、03 等编号保存。失败记录保留，先查看 summary.json，再对照各次 request / response / validation。

`python scripts/smoke_model.py` 测试真实模型；`python scripts/smoke_meetings.py` 测试真实会议 HTTP 接口。脚本会打印本次记录路径；临时服务和数据库自动清理，记录保留。单元测试使用临时目录，不污染这里。

`python scripts/smoke_planning.py` 测试完整三角色链路、编辑复核和重启；API 目录中保存 initial_draft.json / final_draft.json。按 run_id 找 workflows/context.json，再按各角色 trace_id 找 llm 目录，即可对照输入、原始响应和程序处理结果。失败联调记录保留用于对照，不覆盖成成功结果。

`python scripts/smoke_execution.py` 测试四角色、真实任务工具与看板：API 目录保留 01_reviewed_draft.json、02_execution.json、03_board.json，以及各次 HTTP 交换和重启日志。workflows 的 execution 目录保存 01_executor.json（模型 trace_id / 工具选择）与 02_tool_result.json（实际落库结果），按 run_id 关联。数据库中也保存工具调用成功记录；模型输出不作为执行成功证据。

`python scripts/smoke_runs.py` 验证后台提交与 SSE 真正流式接收：01_disconnected_events.json 保存主动断线前事件，02_reconnected_events.json 保存 Last-Event-ID 补读，04_execution_events.json 保存 Executor 与工具事件，05_full_replay / 06_restart_replay 用于对照全量和重启重放。每次收到事件即更新文件；summary.json 汇总事件数与运行 ID。SSE 不使用会一次读完正文的普通 HTTP 测试 hook。

`python scripts/smoke_exports.py` 验证真实四角色执行后的 JSON / ICS / 跟进接口。API 目录保留 01_reviewed_draft.json、02_execution.json、03_meeting_export.json（完整会议与任务快照）、04_tasks.ics（实际下载文件，可导入日历）、05_followup.json 和 06_restart_followup.json。summary.json 给出任务数、日历项数、完成 / 待办 / 逾期等统计和重启结果；对应 HTTP 交换也全部保存。该脚本使用临时会议，执行前验证日历返回 409，执行后完成其中一个任务再导出，不修改现有业务数据库。

`python scripts/smoke_interface.py` 无需浏览器，使用现有 Node 18+ 运行实际前端 api.mjs / state.mjs / runs.mjs 调用临时 FastAPI 与真实 vLLM。SSE 使用测试传输适配器，主动断开重连并检查去重；其余覆盖上传、编辑、复核、旧版本冲突、执行幂等、状态、下载和服务重启。保留 00_home.html、http_*.json、各阶段 events / run / connections、03_initial_draft.json、05_reviewed_draft.json、05_final_draft.json、07_meeting_export.json、08_tasks.ics、09_followup.json、node.log 和 summary.json。若模拟人工接受 Reviewer 语义意见，先核对已知样例所有事实、禁止程序错误，summary 记录 accepted_review_issues；产品页面不自动接受。browser_tested=false 明确表示没有测试 DOM 布局和浏览器点击。失败记录同样保留，前端流程通过不代表模型始终输出正确。

本地开发默认记录模型调用，可设 `PM_LLM_TRACE_ENABLED=false` 关闭。`PM_TEST_ARTIFACTS_PATH` 可调整保存位置。记录含输入会议和原始模型结果，仅供本地排查，不经 HTTP 暴露；鉴权头不保存，配置中的真实密钥做文本替换。思考文本只可能存在于原始响应文件，不作为用户结果展示。
