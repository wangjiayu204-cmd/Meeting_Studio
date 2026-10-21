# 智能会议与任务闭环管理

当前完成 **P1：可启动基础**。包含配置、领域数据契约、统一错误处理、请求追踪和应用健康检查。文档解析、数据库、Agent、业务接口和前端按后续阶段交付。

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

当前没有首页；根路径返回统一格式的 404。健康检查只报告应用存活，数据库为 `not_configured`、模型为 `not_checked`，不会发起模型请求。

自定义端口或监听地址：

```bash
PM_PORT=8081 bash scripts/run.sh
```

默认仅本机访问。浏览器在另一台机器时可用 SSH 端口转发；需要监听其他地址时由部署者设置 `PM_HOST`。

## 配置

配置优先级：显式参数（测试 / 应用工厂）> 环境变量 > 项目根目录 `.env` > 默认值。可复制 `.env.example` 为 `.env` 后修改，密钥不纳入 Git。

模型地址默认 `http://127.0.0.1:8000/v1`，名称 `qwen3-8b`。切换 DCU 修改 `PM_LLM_BASE_URL` 和必要的鉴权。HTTP 调用将在 P3 实现。

数据库相对路径始终基于项目根目录解析，不随启动目录变化；P1 不创建数据库。日志只记录基础状态与请求 ID，不输出模型密钥或完整会议内容。

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
```

P1 测试离线执行，无需模型和数据库。依赖声明在 `requirements.txt`；验收全项目后再生成环境快照。
