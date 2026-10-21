// 服务器上的前端模块联调；测试传输适配器不等同于浏览器或布局测试。
import assert from "node:assert/strict";
import { writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { request } from "../app/web/js/api.mjs";
import { createState, permissions, taskPayload, parseMembers } from "../app/web/js/state.mjs";
import { watchRun } from "../app/web/js/runs.mjs";

const [base, artifactDirectory, timeoutSeconds = "930"] = process.argv.slice(2);
assert.ok(base?.startsWith("http://127.0.0.1:"));
const save = (name, value) => writeFileSync(resolve(artifactDirectory, name), typeof value === "string" ? value : JSON.stringify(value, null, 2) + "\n");
const nativeFetch = globalThis.fetch;
let exchangeCount = 0;
globalThis.fetch = async (path, options = {}) => {
  const response = await nativeFetch(new URL(path, base), options);
  const body = options.body instanceof FormData ? Object.fromEntries([...options.body].map(([key, value]) =>
    [key, typeof value === "string" ? value : { filename: value.name, size: value.size }])) : options.body;
  save(`http_${String(++exchangeCount).padStart(3, "0")}.json`, {
    method: options.method || "GET", url: path, request_body: body,
    status: response.status, request_id: response.headers.get("X-Request-ID"), response_body: await response.clone().text(),
  });
  return response;
};

class HttpEventSource {
  constructor(path) {
    this.handlers = {}; this.readyState = 0; this.controller = new AbortController();
    void this.read(path);
  }
  addEventListener(name, handler) { this.handlers[name] = handler; }
  close() { this.closed = true; this.readyState = 2; this.controller.abort(); }
  async read(path) {
    try {
      const response = await nativeFetch(new URL(path, base), { signal: this.controller.signal });
      assert.equal(response.status, 200); this.readyState = 1; this.onopen?.();
      const reader = response.body.getReader(); const decoder = new TextDecoder(); let pending = "";
      while (!this.closed) {
        const { value, done } = await reader.read(); if (done) break;
        pending += decoder.decode(value, { stream: true }).replaceAll("\r\n", "\n");
        let boundary;
        while ((boundary = pending.indexOf("\n\n")) >= 0) {
          const block = pending.slice(0, boundary); pending = pending.slice(boundary + 2);
          const name = block.split("\n").find(line => line.startsWith("event: "))?.slice(7);
          const data = block.split("\n").filter(line => line.startsWith("data: ")).map(line => line.slice(6)).join("\n");
          if (!this.closed && name) this.handlers[name]?.({ data });
        }
      }
      if (!this.closed) { this.readyState = 2; this.onerror?.(); }
    } catch (error) { if (!this.closed) { this.readyState = 2; this.onerror?.(error); } }
  }
}

let reconnects = 0;
async function waitForRun(run, name, reconnect = false) {
  const events = [], connections = [];
  let watcher, timer;
  const result = await new Promise((done, fail) => {
    timer = setTimeout(() => { watcher.close(); fail(new Error("后台运行等待超时")); }, Number(timeoutSeconds) * 1000);
    watcher = watchRun(run, {
      onEvent(event) {
        events.push(event); save(`${name}_events.json`, events);
        if (reconnect && reconnects === 0 && event.type === "agent_started") { reconnects++; watcher.reconnect(); }
      },
      onRun(value) { save(`${name}_run.json`, value); },
      onConnection(value) { connections.push(value); save(`${name}_connections.json`, connections); },
      onError(error) { clearTimeout(timer); watcher.close(); fail(error); },
      onTerminal(value) { clearTimeout(timer); done(value); },
    }, { Source: HttpEventSource, pollMs: 500 });
  });
  assert.equal(result.status, "succeeded", result.error_message);
  assert.ok(events.length > 0, "应收到真实 SSE 事件");
  assert.equal(new Set(events.map(event => event.id)).size, events.length, "补读不能产生重复事件");
  return result;
}

const config = await request("/ui-config");
assert.equal((await request("/health")).phase, "P8");
const home = await globalThis.fetch("/"); assert.equal(home.status, 200);
const html = await home.text(); save("00_home.html", html);
for (const asset of [...html.matchAll(/(?:src|href)="(\/static\/[^\"]+)"/g)].map(match => match[1])) {
  const response = await globalThis.fetch(asset); assert.equal(response.status, 200, asset);
}
for (const file of ["app", "api", "dom", "state", "views", "editor", "runs"]) {
  const response = await globalThis.fetch(`/static/js/${file}.mjs`); assert.equal(response.status, 200);
  assert.ok(response.headers.get("content-type").includes("javascript"));
}
const form = new FormData();
form.append("title", config.example.title); form.append("meeting_date", config.example.meeting_date);
form.append("timezone", config.timezone);
form.append("members", JSON.stringify(parseMembers("张三｜后端\n李四｜测试\n王五｜项目经理")));
form.append("file", new Blob([config.example.text], { type: "text/plain" }), "meeting.txt");
const meeting = await request("/meetings/upload", { method: "POST", body: form });
const prefix = `/meetings/${meeting.id}`; save("01_uploaded_meeting.json", meeting);
await waitForRun(await request(`${prefix}/runs`, { method: "POST", body: { type: "analysis" } }), "02_analysis", true);
let draft = await request(`${prefix}/draft`);
save("03_initial_draft.json", draft); assert.equal(draft.tasks.length, 2);
const tasks = draft.tasks.map((task, index) => index ? task : taskPayload(task, { description: task.description + "\n人工核对：交付时附上验证说明。" }));
const edited = await request(`${prefix}/draft`, { method: "PUT", body: { base_version: draft.version, tasks,
  resolution_note: "演示前端表单编辑，补充交付说明，不改变原文负责人和日期" } });
assert.ok(edited.version > draft.version); assert.equal(edited.status, "pending_review");
assert.equal(permissions({ ...createState(), meeting, draft: edited }).execute, false);
await assert.rejects(request(`${prefix}/draft`, { method: "PUT", body: { base_version: draft.version, tasks } }), error => error.status === 409);
await waitForRun(await request(`${prefix}/runs`, { method: "POST", body: { type: "review", draft_version: edited.version } }), "04_review");
draft = await request(`${prefix}/draft`); save("05_reviewed_draft.json", draft);
// 隔离样例中模拟用户逐项核对后接受 Reviewer 误报；产品页面不会自动接受意见。
// 先独立验证所有已知事实，任何遗漏、字段错误或程序错误都必须失败。
const login = draft.tasks.find(task => task.owner === "张三");
const testing = draft.tasks.find(task => task.owner === "李四");
assert.equal(draft.tasks.length, 2); assert.ok(login && testing);
assert.equal(login.due_date, "2026-10-23"); assert.equal(testing.due_date, "2026-10-26");
assert.ok(login.deliverable.includes("接口说明") && login.deliverable.includes("联调报告"));
assert.ok(testing.deliverable.includes("测试报告"));
assert.ok(testing.dependencies.includes(login.local_id));
assert.ok(login.source_refs.includes("p0005") && testing.source_refs.includes("p0006"));
let acceptedReviewIssues = 0;
if (draft.status === "needs_attention") {
  const issues = draft.review.issues.filter(issue => issue.status === "open");
  assert.ok(issues.length && issues.every(issue => issue.origin === "reviewer"), "程序问题不得接受");
  const resolutions = issues.map(issue => ({ issue_id: issue.id,
    note: "隔离测试模拟人工核对：p0005/p0006 的负责人、日期、交付物和依赖已逐项与测试标注一致，接受此 Reviewer 语义意见。产品不会自动放行。" }));
  draft = await request(`${prefix}/draft/accept-issues`, { method: "POST", body: { draft_version: draft.version, resolutions } });
  acceptedReviewIssues = resolutions.length;
}
save("05_final_draft.json", draft); assert.equal(draft.status, "ready");
assert.equal(permissions({ ...createState(), meeting, draft }).execute, true);
const executionBody = { type: "execution", draft_version: draft.version, confirm: true, acknowledgement: "前端模块联调：已核对当前草稿并明确确认创建正式任务" };
const run = await request(`${prefix}/runs`, { method: "POST", body: executionBody });
await waitForRun(run, "06_execution");
assert.equal((await request(`${prefix}/runs`, { method: "POST", body: executionBody })).id, run.id);
const board = await request(`/tasks?meeting_id=${meeting.id}`); assert.equal(board.total, 2);
await request(`/tasks/${board.items[0].id}`, { method: "PATCH", body: { status: "done" } });
const exported = await request(`${prefix}/export?format=json`); save("07_meeting_export.json", exported);
const calendar = await globalThis.fetch(`/api${prefix}/export?format=ics`);
assert.equal(calendar.status, 200); const ics = await calendar.text(); save("08_tasks.ics", ics);
assert.equal((ics.match(/BEGIN:VEVENT/g) || []).length, 2);
const followup = await request(`${prefix}/followup`); save("09_followup.json", followup);
assert.equal(followup.counts.completed, 1); assert.equal(followup.counts.pending, 1);
assert.equal((await request(`${prefix}/drafts`)).some(value => value.version === edited.version), true);
assert.ok((await request("/meetings?limit=12")).items.some(value => value.id === meeting.id));
save("result.json", { meeting_id: meeting.id, execution_run_id: run.id, task_count: 2, reconnects, accepted_review_issues: acceptedReviewIssues,
  counts: followup.counts, frontend_modules_used: ["api.mjs", "state.mjs", "runs.mjs"],
  browser_tested: false, scope: "HTTP + JavaScript modules; no DOM layout or click automation" });
console.log("P8 前端模块与真实 HTTP/vLLM 联调通过：上传、SSE 重连、编辑复核、版本冲突、执行幂等、看板、导出与跟进。");
