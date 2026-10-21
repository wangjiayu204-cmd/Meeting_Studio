import { $, empty, node, refs, selectOptions } from "./dom.mjs";
import { labels, permissions, active } from "./state.mjs";
import { renderEditor } from "./editor.mjs";

export const roles = [
  ["manager", "M", "Manager", "理解会议，提炼议题与共识"],
  ["planner", "P", "Planner", "拆解行动，建立任务与依赖"],
  ["reviewer", "R", "Reviewer", "复核依据，检查遗漏与冲突"],
  ["executor", "E", "Executor", "确认之后，创建正式任务"],
];

export function renderHistory(state, open) {
  const history = $("history"); history.replaceChildren();
  for (const meeting of state.history?.items || []) {
    const button = node("button", `history-item${state.meeting?.id === meeting.id ? " active" : ""}`);
    button.append(node("strong", "", meeting.title), node("span", "", `${meeting.meeting_date} · ${meeting.timezone}`));
    button.title = meeting.title; button.addEventListener("click", () => open(meeting.id));
    button.disabled = state.busy; history.append(button);
  }
  if (!history.childElementCount) history.append(node("p", "muted", "还没有会议，从新建开始。"));
  const total = state.history?.total || 0;
  $("history-page").textContent = `${total ? Math.floor(state.historyOffset / 12) + 1 : 0} / ${Math.ceil(total / 12)}`;
  $("history-prev").disabled = state.busy || state.historyOffset === 0;
  $("history-next").disabled = state.busy || state.historyOffset + 12 >= total;
}

export function renderSource(state) {
  $("current-title").textContent = state.meeting.title;
  $("current-meta").textContent = `${state.meeting.meeting_date}  ·  ${state.meeting.timezone}  ·  ${state.meeting.members.length ? `${state.meeting.members.length} 位参会人` : "未登记参会人"}`;
  $("paragraph-count").textContent = `${state.meeting.paragraphs.length} 段`;
  $("source-paragraphs").replaceChildren(...state.meeting.paragraphs.map(paragraph => {
    const article = node("article", "paragraph"); article.id = `source-${paragraph.id}`;
    article.append(node("span", "paragraph-id", paragraph.id), node("p", "", paragraph.text)); return article;
  }));
  $("analysis-summary").hidden = !state.analysis;
  $("analysis-summary").textContent = state.analysis?.summary || "";
}

export function renderMetrics(state) {
  const count = state.followup?.counts;
  const values = [["正式任务", count?.total ?? "—", "▤"], ["已完成", count?.completed ?? "—", "✓"],
    ["待跟进行动", count?.pending ?? "—", "◷"], ["逾期任务", count?.overdue ?? "—", "↗"]];
  $("metrics").replaceChildren(...values.map(([label, value, icon]) => {
    const card = node("div", "metric"); const text = node("div");
    text.append(node("div", "metric-label", label), node("div", "metric-value", value));
    card.append(text, node("div", "metric-icon", icon)); return card;
  }));
}

export function renderDraft(state, onChange) {
  const draft = state.shownDraft || state.draft;
  $("draft-empty").hidden = !!draft;
  $("draft-state").textContent = draft ? `V${draft.version} · ${labels[draft.status]}` : "等待分析";
  $("draft-state").className = `badge ${draft?.status === "ready" || draft?.status === "confirmed" ? "good" : draft ? "warn" : ""}`;
  selectOptions($("draft-version"), state.drafts.length ? state.drafts.slice().reverse().map(item =>
    [String(item.version), `V${item.version} · ${labels[item.status]}`]) : [["", "暂无版本"]], String(draft?.version || ""));
  renderEditor(draft?.tasks || [], !state.historical && !state.stale && state.execution?.status !== "succeeded", onChange);
  if (draft && !draft.tasks.length) $("draft-editor").append(node("p", "muted", "当前草稿没有任务。可核对原文后添加，或确认执行空计划。"));
  $("edit-note").value = "";
  $("execute-confirm").checked = false;
  renderReview(state);
  updateControls(state);
}

export function renderReview(state) {
  const draft = state.shownDraft || state.draft;
  $("review-panel").hidden = !draft?.review;
  $("review-summary").textContent = draft?.review?.summary || "";
  $("review-issues").replaceChildren();
  let acceptable = 0;
  for (const issue of draft?.review?.issues || []) {
    const block = node("div", `review-issue${issue.status !== "open" ? " closed" : ""}`);
    const status = issue.status === "open" ? "待处理" : issue.status === "accepted" ? "已接受" : "已解决";
    block.append(node("span", "badge", `${status} · ${issue.severity === "error" ? "问题" : "提示"}${issue.task_ref ? ` · ${issue.task_ref}` : ""}`), node("p", "", issue.message));
    if (issue.status === "open" && !(issue.origin === "program" && issue.severity === "error")) {
      acceptable++;
      const label = node("label", "checkbox-label");
      const checkbox = node("input"); checkbox.type = "checkbox"; checkbox.dataset.issueId = issue.id;
      label.append(checkbox, document.createTextNode("已核实，接受此问题并记录理由"));
      const note = node("textarea"); note.rows = 2; note.maxLength = 2000; note.dataset.issueNote = issue.id;
      note.setAttribute("aria-label", `接受理由：${issue.message}`); note.placeholder = "填写核实情况与接受理由";
      block.append(label, note);
    } else block.append(node("p", "muted", issue.resolution_note || "必须修改草稿，程序硬错误不能直接接受。"));
    $("review-issues").append(block);
  }
  if (draft?.review && !draft.review.issues.length) $("review-issues").append(node("p", "muted", "当前审核没有待处理问题。"));
  $("accept-issues").hidden = !acceptable;
}

export function updateControls(state) {
  const allowed = permissions(state);
  $("create-submit").disabled = state.busy || !state.config;
  $("fill-example").disabled = state.busy || !state.config;
  $("meeting-form").querySelectorAll("input,textarea,button").forEach(input => { input.disabled = state.busy || !state.config; });
  $("new-meeting").disabled = state.busy;
  $("history-prev").disabled = state.busy || state.historyOffset === 0;
  $("history-next").disabled = state.busy || !state.history || state.historyOffset + 12 >= state.history.total;
  document.querySelectorAll(".history-item").forEach(button => { button.disabled = state.busy; });
  $("refresh-meeting").disabled = state.busy;
  $("run-select").disabled = state.busy || !state.runs.length;
  $("retry-run").disabled = state.busy || state.dirty || state.stale || state.historical || state.runs.some(active);
  $("analyze").disabled = !allowed.analyze;
  $("analyze").textContent = state.runs.some(active) ? "智能协作进行中…" : state.draft ? "重新分析" : "开始智能分析 ↗";
  $("save-draft").disabled = !allowed.save;
  $("review-draft").disabled = !allowed.review;
  $("add-task").disabled = !allowed.edit;
  $("discard-edits").disabled = state.busy || (!state.dirty && !state.stale);
  $("draft-version").disabled = state.busy || !state.draft;
  $("accept-issues").disabled = !allowed.accept;
  $("export-json").disabled = !state.meeting || state.busy;
  $("export-ics").disabled = !allowed.calendar || state.busy;
  $("execute-confirm").disabled = !allowed.execute;
  $("execute").disabled = !allowed.execute || !$("execute-confirm").checked;
  $("edit-actions").hidden = !state.draft || state.execution?.status === "succeeded" || state.historical;
  $("execution-box").hidden = !state.draft || state.historical;
  $("execution-hint").textContent = state.execution?.status === "succeeded" ? "任务已创建。请前往看板更新状态，或下载日历。" :
    state.dirty ? "存在未保存的修改，请先保存新版本并提交复核。" :
    allowed.execute ? `即将执行草稿 V${state.draft?.version}。确认后创建正式任务。` : "请先处理审核问题，完成当前版本的复核。";
  const warning = state.historical ? "正在查看历史版本，仅供阅读。请切回最新版本后操作。" :
    state.stale ? "服务器草稿已更新，本地编辑已保留。请复制需要保留的内容，再放弃本地修改并加载最新版本。" :
    state.dirty ? "本地修改尚未保存。保存后需要重新复核，才能确认执行。" : "";
  $("draft-warning").hidden = !warning; $("draft-warning").textContent = warning;
  $("draft-editor").querySelectorAll("input,textarea,select,button.icon-button").forEach(input => { input.disabled = !allowed.edit; });
  $("review-issues").querySelectorAll("input,textarea").forEach(input => { input.disabled = !allowed.accept; });
  $("edit-note").disabled = !allowed.edit;
  document.querySelectorAll(".board-status").forEach(select => { select.disabled = state.busy; });
}

export function renderAgents(events, run) {
  $("agents").replaceChildren(...roles.map(([role, icon, name, description]) => {
    const latest = events.filter(event => event.agent === role && ["agent_started", "agent_completed"].includes(event.type)).at(-1);
    const status = latest?.type === "agent_completed" ? "done" : latest && active(run) ? "running" : "waiting";
    const row = node("div", `agent ${status}`); const copy = node("div", "agent-copy");
    copy.append(node("strong", "", name), node("p", "", description));
    row.append(node("div", "agent-icon", status === "done" ? "✓" : icon), copy,
      node("span", "agent-status", status === "done" ? "已完成" : status === "running" ? "处理中" : latest ? "已停止" : "等待"));
    return row;
  }));
}

export function renderBoard(state, changeStatus) {
  const oldOwner = $("owner-filter").value;
  const owners = [...new Set(state.tasks.map(task => task.owner || ""))].sort();
  selectOptions($("owner-filter"), [["", "全部负责人"], ...owners.filter(Boolean).map(owner => [owner, owner]),
    ...(owners.includes("") ? [["__unassigned__", "待分配"]] : [])], oldOwner);
  if (![...$("owner-filter").options].some(option => option.value === oldOwner)) $("owner-filter").value = "";
  const owner = $("owner-filter").value;
  const tasks = state.tasks.filter(task => !owner || (owner === "__unassigned__" ? task.owner === null : task.owner === owner));
  const board = $("board"); board.replaceChildren();
  if (state.execution?.status !== "succeeded") empty(board, "正式任务将在确认后出现", "先完成草稿复核，再确认执行。草稿不会自动进入看板。");
  else {
    const columns = node("div", "board-columns");
    for (const status of ["todo", "in_progress", "done"]) {
      const column = node("section", "board-column"); const heading = node("h3", "", labels[status]);
      const matching = tasks.filter(task => task.status === status); heading.append(node("span", "", matching.length));
      column.append(heading);
      for (const task of matching) {
        const card = node("article", "board-task"); card.dataset.taskId = task.id;
        card.append(node("span", `badge${task.priority === "high" ? " warn" : ""}`, labels[task.priority]), node("h4", "", task.title),
          node("p", "", `负责人 · ${task.owner || "待确认"}`));
        const overdue = state.followup?.overdue.some(item => item.id === task.id);
        card.append(node("p", overdue ? "overdue" : "", `${overdue ? "已逾期 · " : "截止 · "}${task.due_date || "待确认"}`), refs(task.source_refs));
        const select = node("select", "board-status"); select.setAttribute("aria-label", `任务状态：${task.title}`);
        selectOptions(select, ["todo", "in_progress", "done"].map(value => [value, labels[value]]), status);
        select.disabled = state.busy;
        select.addEventListener("change", () => changeStatus(task.id, select.value)); card.append(select); column.append(card);
      }
      if (!matching.length) column.append(node("p", "muted", "暂无任务"));
      columns.append(column);
    } board.append(columns);
  }
  const preview = $("calendar-preview"); preview.replaceChildren(node("h3", "", "截止日期一览"));
  const dated = tasks.filter(task => task.due_date).sort((a, b) => a.due_date.localeCompare(b.due_date));
  for (const task of dated) {
    const row = node("div", "calendar-row"); const text = node("div");
    text.append(node("p", "", task.title), node("small", "", `${task.owner || "待确认"} · ${labels[task.status]}`));
    row.append(node("div", "date-chip", task.due_date), text); preview.append(row);
  }
  if (!dated.length) preview.append(node("p", "muted", "暂无明确截止日期的正式任务。"));
  const missing = tasks.filter(task => !task.due_date).length;
  if (missing) preview.append(node("p", "muted", `${missing} 项任务缺少日期，不会出现在导出的日历中。`));
}

export function renderFollowup(state) {
  const container = $("followup"); const report = state.followup;
  if (!report) { empty(container, "暂无跟进报告", "保存会议后，可在这里查看任务与待澄清事项。"); return; }
  const header = node("div", "followup-heading");
  header.append(node("div", "eyebrow teal", "THE NEXT CONVERSATION"), node("h3", "", "为下一次推进，准备好重点"),
    node("p", "", `统计日期 ${report.as_of_date} · ${report.timezone}。逾期、今日到期均为待办的子集。`));
  container.replaceChildren(header);
  for (const [title, items] of [["需要优先跟进 · 逾期", report.overdue], ["今天到期", report.due_today], ["尚未完成", report.pending]]) {
    const section = node("section", "followup-section"); section.append(node("h3", "", `${title} · ${items.length}`));
    for (const task of items) {
      const row = node("div", "followup-item", task.title); row.append(node("small", "", `${task.owner || "负责人待确认"} · ${task.due_date || "日期待确认"}`), refs(task.source_refs)); section.append(row);
    }
    if (!items.length) section.append(node("p", "muted", "暂无事项")); container.append(section);
  }
  const section = node("section", "followup-section"); section.append(node("h3", "", `待澄清与下次议题 · ${report.clarifications.length}`));
  for (const question of report.clarifications) {
    const row = node("div", "followup-item", question.message);
    row.append(node("small", "", `${({ analysis: "会议未决议题", review: "开放审核问题", task: "正式任务", draft: "未执行草稿" })[question.origin]}${question.task_ref ? ` · ${question.task_ref}` : ""}`));
    if (question.source_refs.length) row.append(refs(question.source_refs)); section.append(row);
  }
  if (!report.clarifications.length) section.append(node("p", "muted", "当前没有记录待澄清事项。"));
  container.append(section);
}
