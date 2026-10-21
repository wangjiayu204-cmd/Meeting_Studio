# 智能会议与任务闭环管理

当前完成 **P2：文档与数据**。包含应用基础、SQLite 持久化、TXT / 文本型 PDF 解析、会议创建与分页查询。Agent、草稿、看板与前端按后续阶段交付。

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

模型地址默认 `http://127.0.0.1:8000/v1`，名称 `qwen3-8b`。切换 DCU 修改 `PM_LLM_BASE_URL` 和必要的鉴权。HTTP 调用将在 P3 实现。

数据库相对路径始终基于项目根目录解析，不随启动目录变化。启动自动初始化 `data/pm.sqlite3`，启用外键和 WAL，以 `PRAGMA user_version` 管理版本；遇到高于支持版本的数据库拒绝启动。数据库不提交 Git。日志只记录基础状态与请求 ID，不输出模型密钥或完整会议内容。

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

## 数据契约

- `app/domain/schemas.py`：会议、段落、分析、草稿、证据、审核、运行、任务、执行与事件模型。
- `app/domain/states.py`：运行 / 草稿 / 执行 / 看板状态及角色与事件枚举。
- 未知负责人 / 日期可空；人工来源必须填写说明；审核版本必须匹配草稿。
- 字段级验证在 P1 完成；引用实际存在、依赖环、执行权限和版本并发检查按 P4 / P5 实现。

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
```

测试离线执行，无需模型服务，数据库使用测试临时目录；包含真实生成的 PDF 解析、SQLite 事务与重启持久化验证。`smoke_meetings.py` 会在临时端口启动两次真实服务，使用隔离数据库检查 HTTP 接口及重启持久化，结束后自动停止进程并清理临时数据。依赖声明在 `requirements.txt`；验收全项目后再生成环境快照。
