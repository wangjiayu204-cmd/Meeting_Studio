export const terminal = status => ["succeeded", "failed", "interrupted"].includes(status);
export const active = run => run && !terminal(run.status);
export const labels = {
  pending_review: "待复核", needs_attention: "需要处理", ready: "可确认执行", confirmed: "已确认执行",
  queued: "排队中", running: "运行中", succeeded: "已完成", failed: "失败", interrupted: "已中断",
  analysis: "分析", review: "复核", execution: "执行", todo: "待开始", in_progress: "进行中", done: "已完成",
  low: "低优先级", medium: "普通优先级", high: "高优先级",
};

export function permissions(state) {
  const running = state.runs?.some(active);
  const locked = state.busy || running || state.stale || state.historical;
  const executed = state.execution?.status === "succeeded";
  const draft = state.draft;
  return {
    analyze: !!state.meeting && !locked && !executed && !state.dirty,
    edit: !!draft && !locked && !executed,
    save: !!draft && !locked && !executed && state.dirty,
    review: !!draft && !locked && !executed && !state.dirty,
    accept: !!draft?.review && !locked && !executed && !state.dirty &&
      ["ready", "needs_attention"].includes(draft.status),
    execute: !!draft && !locked && !executed && !state.dirty && draft.status === "ready" &&
      draft.review?.approved === true && !draft.review.issues.some(issue => issue.status === "open"),
    calendar: !!state.meeting && executed,
  };
}

export function createState() {
  return { epoch: 0, busy: false, dirty: false, stale: false, historical: false,
    meeting: null, draft: null, analysis: null, execution: null, tasks: [], followup: null,
    runs: [], drafts: [], history: null, historyOffset: 0, tab: "draft", config: null };
}

export function taskPayload(original, fields) {
  const result = { ...original };
  for (const field of ["local_id", "title", "description", "deliverable", "priority"]) {
    if (field in fields) result[field] = fields[field].trim();
  }
  for (const field of ["owner", "due_date"]) if (field in fields) result[field] = fields[field].trim() || null;
  for (const field of ["dependencies", "source_refs"]) if (field in fields) {
    result[field] = fields[field].split(/[,，\s]+/u).filter(Boolean);
  }
  if ("needs_confirmation" in fields) result.needs_confirmation = fields.needs_confirmation.split("\n").map(x => x.trim()).filter(Boolean);
  if (!result.owner) result.owner_evidence = null;
  if (!result.due_date) result.due_evidence = null;
  return result;
}

export function localDate(zone, now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(now);
  const get = key => parts.find(part => part.type === key).value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}

export function parseMembers(text) {
  return text.split("\n").map(line => line.trim()).filter(Boolean).map(line => {
    const [name, ...role] = line.split(/[|｜]/u);
    return { name: name.trim(), role: role.join("｜").trim() };
  });
}
