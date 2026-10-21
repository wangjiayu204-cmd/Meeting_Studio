import test from "node:test";
import assert from "node:assert/strict";
import { createState, permissions, taskPayload, localDate, parseMembers } from "../../app/web/js/state.mjs";

function ready() {
  return { ...createState(), meeting: { id: "meeting" },
    draft: { status: "ready", version: 4, review: { approved: true, issues: [] } } };
}

test("只有当前、已复核、未修改且空闲的草稿可确认执行", () => {
  assert.equal(permissions(ready()).execute, true);
  for (const changes of [{ dirty: true }, { busy: true }, { stale: true }, { historical: true },
    { runs: [{ status: "running" }] }, { runs: [{ status: "queued" }] }, { execution: { status: "succeeded" } }]) {
    assert.equal(permissions({ ...ready(), ...changes }).execute, false, JSON.stringify(changes));
  }
  for (const draft of [{ status: "pending_review", review: { approved: true, issues: [] } },
    { status: "ready", review: { approved: false, issues: [] } },
    { status: "ready", review: { approved: true, issues: [{ status: "open" }] } }]) {
    assert.equal(permissions({ ...ready(), draft }).execute, false);
  }
  assert.equal(permissions(createState()).execute, false);
});

test("未保存修改可保存，必须先保存再复核或接受问题", () => {
  const value = permissions({ ...ready(), dirty: true });
  assert.equal(value.save, true); assert.equal(value.review, false); assert.equal(value.accept, false);
  assert.equal(value.analyze, false); assert.equal(value.edit, true);
});

test("执行后只允许下载日历及查看，历史版本保持只读", () => {
  const value = permissions({ ...ready(), execution: { status: "succeeded" } });
  assert.equal(value.calendar, true); assert.equal(value.edit, false); assert.equal(value.analyze, false);
  assert.equal(permissions({ ...ready(), historical: true }).save, false);
  assert.equal(permissions(ready()).calendar, false);
});

test("草稿表单保留证据，空字段为空，引用与依赖可分隔", () => {
  const original = { title: "原名称", owner: "张三", owner_evidence: { source_refs: ["p0001"] },
    due_date: "2026-10-23", due_evidence: { source_refs: ["p0001"] }, priority: "medium" };
  const result = taskPayload(original, { title: " 新名称 ", owner: " ", due_date: "", source_refs: "p0001，p0002 p0003",
    dependencies: "task_a, task_b", needs_confirmation: "范围需核实\n\n 日期待确认 " });
  assert.equal(result.title, "新名称"); assert.equal(result.owner, null); assert.equal(result.owner_evidence, null);
  assert.equal(result.due_date, null); assert.equal(result.due_evidence, null);
  assert.deepEqual(result.source_refs, ["p0001", "p0002", "p0003"]);
  assert.deepEqual(result.dependencies, ["task_a", "task_b"]);
  assert.deepEqual(result.needs_confirmation, ["范围需核实", "日期待确认"]);
  assert.equal(original.owner, "张三");
  const preserved = taskPayload(original, { description: "补充说明" });
  assert.deepEqual(preserved.owner_evidence, original.owner_evidence);
});

test("会议默认日期按业务时区跨日，不受服务器 UTC 影响", () => {
  const instant = new Date("2026-10-21T17:00:00Z");
  assert.equal(localDate("Asia/Shanghai", instant), "2026-10-22");
  assert.equal(localDate("America/New_York", instant), "2026-10-21");
});

test("参会人按行解析并保留角色", () => {
  assert.deepEqual(parseMembers("张三｜后端\n\n李四|测试\n王五"), [
    { name: "张三", role: "后端" }, { name: "李四", role: "测试" }, { name: "王五", role: "" },
  ]);
});
