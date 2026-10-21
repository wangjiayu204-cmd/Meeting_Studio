import { request } from "./api.mjs";
import { active, terminal } from "./state.mjs";

const eventNames = ["run_queued", "run_started", "agent_started", "agent_completed", "review_feedback",
  "tool_result", "run_completed", "run_failed", "run_interrupted"];

// 一个订阅只属于一个会议和运行；补读、轮询和重连均复用游标并去重。
export function watchRun(run, { onEvent, onRun, onConnection, onError, onTerminal },
                         { Source = globalThis.EventSource, api = request, pollMs = 3500 } = {}) {
  let stopped = false, source, timer, cursor = 0, polling = false, finalized = false, terminalPolls = 0;
  const seen = new Set();
  function close() { stopped = true; source?.close(); clearInterval(timer); }
  async function finish(result) {
    if (finalized || stopped) return;
    finalized = true; close(); onRun(result);
    await onTerminal(result);
  }
  async function poll() {
    if (stopped || polling) return;
    polling = true;
    try {
      const result = await api(`/runs/${encodeURIComponent(run.id)}`);
      if (stopped) return;
      onRun(result);
      if (terminal(result.status)) {
        // 先允许 SSE 补读终态。SSE 不可用时轮询仍能恢复页面状态。
        terminalPolls++;
        if (!source || source.readyState !== 1 || terminalPolls >= 2) await finish(result);
      }
    } catch (error) { if (!stopped) onError(error); }
    finally { polling = false; }
  }
  function connect() {
    if (stopped || !Source) return;
    source?.close();
    source = new Source(`/api/runs/${encodeURIComponent(run.id)}/events?after=${cursor}`);
    source.onopen = () => { if (!stopped) onConnection("实时进度已连接"); };
    source.onerror = () => {
      if (stopped) return;
      onConnection("连接中断，正在重连并查询状态…"); void poll();
    };
    for (const name of eventNames) source.addEventListener(name, event => {
      if (stopped) return;
      let value;
      try { value = JSON.parse(event.data); }
      catch { onConnection("收到无效事件，正在查询状态…"); void poll(); return; }
      if (value.run_id !== run.id || !Number.isSafeInteger(value.id) || seen.has(value.id) || value.id <= cursor) return;
      seen.add(value.id); cursor = value.id; onEvent(value);
      if (["run_completed", "run_failed", "run_interrupted"].includes(value.type)) {
        // 获取持久化终态，不能根据模型消息或单个 agent 完成判定工作流成功。
        void api(`/runs/${encodeURIComponent(run.id)}`).then(finish).catch(onError);
      }
    });
  }
  connect();
  if (active(run)) timer = setInterval(() => { void poll(); }, pollMs);
  else if (!Source) void finish(run);
  return { close, reconnect: connect, poll, get cursor() { return cursor; } };
}
