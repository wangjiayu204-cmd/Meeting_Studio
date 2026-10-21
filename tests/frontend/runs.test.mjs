import test from "node:test";
import assert from "node:assert/strict";
import { watchRun } from "../../app/web/js/runs.mjs";

class Source {
  static instances = [];
  constructor(url) { this.url = url; this.handlers = {}; this.readyState = 1; Source.instances.push(this); }
  addEventListener(name, handler) { this.handlers[name] = handler; }
  close() { this.closed = true; this.readyState = 2; }
  emit(value) { this.handlers[value.type]?.({ data: JSON.stringify(value) }); }
}
const tick = () => new Promise(resolve => setImmediate(resolve));
function setup(api = async () => ({ id: "run", status: "running" }), source = Source) {
  const events = [], runs = [], messages = [], errors = [], terminals = [];
  const watch = watchRun({ id: "run", status: "running" }, {
    onEvent: event => events.push(event), onRun: run => runs.push(run), onConnection: message => messages.push(message),
    onError: error => errors.push(error), onTerminal: run => terminals.push(run),
  }, { api, Source: source, pollMs: 100000 });
  return { watch, events, runs, messages, errors, terminals, source: Source.instances.at(-1) };
}

test("SSE 按运行隔离、补读去重，重连携带游标", () => {
  const context = setup();
  try {
    context.source.emit({ id: 1, run_id: "other", type: "agent_started" });
    context.source.emit({ id: 2, run_id: "run", type: "agent_started" });
    context.source.emit({ id: 2, run_id: "run", type: "agent_started" });
    context.source.emit({ id: 1, run_id: "run", type: "agent_started" });
    assert.equal(context.events.length, 1); assert.equal(context.watch.cursor, 2);
    context.watch.reconnect(); assert.equal(context.source.closed, true);
    assert.ok(Source.instances.at(-1).url.endsWith("after=2"));
  } finally { context.watch.close(); }
});

test("终态读取持久化运行，关闭连接且只完成一次", async () => {
  const context = setup(async () => ({ id: "run", status: "succeeded" }));
  context.source.emit({ id: 3, run_id: "run", type: "run_completed" }); await tick();
  assert.equal(context.source.closed, true); assert.equal(context.terminals.length, 1);
  context.source.emit({ id: 4, run_id: "run", type: "run_completed" }); await tick();
  assert.equal(context.terminals.length, 1);
});

test("关闭订阅后，慢请求不再写回已切换的会议", async () => {
  let release;
  const context = setup(() => new Promise(resolve => { release = resolve; }));
  const pending = context.watch.poll(); context.watch.close();
  release({ id: "run", status: "failed" }); await pending;
  assert.equal(context.runs.length, 0); assert.equal(context.terminals.length, 0);
});

test("无 EventSource 时轮询仍能获取成功或失败终态", async () => {
  const context = setup(async () => ({ id: "run", status: "failed", error_message: "连接失败" }), null);
  await context.watch.poll();
  assert.equal(context.terminals[0].status, "failed"); assert.equal(context.events.length, 0);
});

test("连接仍打开但丢失终态事件时，轮询有界恢复", async () => {
  const context = setup(async () => ({ id: "run", status: "succeeded" }));
  await context.watch.poll(); assert.equal(context.terminals.length, 0);
  await context.watch.poll(); assert.equal(context.terminals.length, 1);
});

test("无效进度事件不造成成功或崩溃", () => {
  const context = setup();
  try {
    context.source.handlers.agent_started({ data: "not json" });
    assert.equal(context.events.length, 0); assert.equal(context.terminals.length, 0);
    assert.match(context.messages[0], /无效事件/u);
  } finally { context.watch.close(); }
});
