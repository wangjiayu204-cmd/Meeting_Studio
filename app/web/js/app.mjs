import { request, download } from "./api.mjs";
import { $, node, notify, showError, ask, selectOptions } from "./dom.mjs";
import { createState, active, labels, localDate, parseMembers } from "./state.mjs";
import { addTask, collectTasks } from "./editor.mjs";
import { watchRun } from "./runs.mjs";
import { renderHistory, renderSource, renderMetrics, renderDraft, updateControls,
  renderAgents, renderBoard, renderFollowup } from "./views.mjs";

const state = createState();
let mode = "text", selectedFile = null, subscription = null, selectedRun = null, events = [];
const path = () => `/meetings/${encodeURIComponent(state.meeting.id)}`;

function dirty() {
  state.dirty = true; $("execute-confirm").checked = false; updateControls(state);
}

async function action(work) {
  if (state.busy) return;
  state.busy = true; updateControls(state);
  try { await work(); }
  catch (error) {
    if (["draft_version_conflict", "meeting_busy", "meeting_already_executed"].includes(error.code)) {
      state.stale = true;
      if (state.meeting) await refresh().catch(() => {});
    }
    showError(error);
  } finally { state.busy = false; updateControls(state); }
}

async function history(offset = state.historyOffset) {
  const page = await request(`/meetings?limit=12&offset=${offset}`);
  state.history = page; state.historyOffset = offset; renderHistory(state, openMeeting);
}

function resetSelection() {
  subscription?.close(); subscription = null; selectedRun = null; events = [];
  state.epoch++; Object.assign(state, { meeting: null, draft: null, shownDraft: null, analysis: null,
    execution: null, tasks: [], followup: null, runs: [], drafts: [], dirty: false, stale: false,
    historical: false, tab: "draft" });
}

async function allowLeave() {
  return !state.dirty || await ask("放弃未保存的修改？", "离开当前会议将丢弃本地草稿修改。已保存的版本会保留在会议档案中。");
}

async function openMeeting(id, { replace = false, force = false } = {}) {
  if (state.busy || (!force && !(await allowLeave()))) return;
  resetSelection();
  const epoch = state.epoch;
  state.busy = true; updateControls(state);
  try {
    const meeting = await request(`/meetings/${encodeURIComponent(id)}`);
    if (epoch !== state.epoch) return;
    state.meeting = meeting;
    $("create-panel").hidden = true; $("meeting-panel").hidden = false;
    await refresh({ forceEditor: true });
    const url = new URL(location.href); url.searchParams.set("meeting", id);
    globalThis.history[replace ? "replaceState" : "pushState"]({}, "", url);
    renderHistory(state, openMeeting); changeTab("draft");
  } catch (error) {
    showError(error);
    if (!state.meeting) { $("create-panel").hidden = false; $("meeting-panel").hidden = true; }
  } finally { state.busy = false; updateControls(state); }
}

async function refresh({ forceEditor = false } = {}) {
  if (!state.meeting) return;
  const epoch = state.epoch, prefix = path();
  const [snapshot, followup, runs, drafts] = await Promise.all([
    request(`${prefix}/export?format=json`), request(`${prefix}/followup`),
    request(`${prefix}/runs?limit=100`), request(`${prefix}/drafts`),
  ]);
  if (epoch !== state.epoch) return;
  const preserve = state.dirty && !forceEditor;
  if (preserve && snapshot.draft?.version !== state.editVersion) state.stale = true;
  Object.assign(state, { meeting: snapshot.meeting, analysis: snapshot.analysis, draft: snapshot.draft,
    execution: snapshot.execution, tasks: snapshot.tasks, followup, runs, drafts });
  renderSource(state); renderMetrics(state); renderBoard(state, changeStatus); renderFollowup(state);
  if (!preserve) {
    state.dirty = false; state.stale = false; state.historical = false; state.shownDraft = null;
    state.editVersion = state.draft?.version; renderDraft(state, dirty);
  } else {
    const choices = drafts.slice().reverse().map(d => [String(d.version), `V${d.version} · ${labels[d.status]}`]);
    selectOptions($("draft-version"), choices, String(state.editVersion)); updateControls(state);
  }
  renderRunOptions();
  const latest = runs.find(active) || runs[0];
  if (latest && (!selectedRun || (latest.id !== selectedRun.id && active(latest)))) selectRun(latest);
  if (!latest) { renderAgents([], null); $("run-state").textContent = "尚未运行"; $("event-log").replaceChildren(); }
  updateControls(state);
}

function renderRunOptions() {
  selectOptions($("run-select"), state.runs.length ? state.runs.map(run => [run.id,
    `${labels[run.type]} · ${labels[run.status]} · ${new Intl.DateTimeFormat("zh-CN", { timeZone: state.meeting.timezone,
      month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(run.created_at))}`]) : [["", "暂无运行记录"]], selectedRun?.id);
}

function selectRun(run) {
  subscription?.close(); selectedRun = run; events = [];
  const epoch = state.epoch;
  $("event-log").replaceChildren(); $("run-select").value = run.id;
  $("stream-state").textContent = active(run) ? "正在连接实时进度…" : "正在读取已保存事件…";
  renderRunState(run); renderAgents(events, run);
  const current = () => epoch === state.epoch && selectedRun?.id === run.id;
  subscription = watchRun(run, {
    onEvent(event) {
      if (!current()) return;
      events.push(event); renderAgents(events, selectedRun);
      const item = node("li");
      const time = new Intl.DateTimeFormat("zh-CN", { timeZone: state.meeting.timezone, hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(new Date(event.created_at));
      item.append(node("time", "", time), document.createTextNode(`${event.agent ? `${event.agent} · ` : ""}${event.message}`));
      $("event-log").append(item);
      if ($("event-log").childElementCount > 200) $("event-log").firstElementChild.remove();
      $("event-log").scrollTop = $("event-log").scrollHeight;
    },
    onRun(result) {
      if (!current()) return;
      selectedRun = result;
      state.runs = state.runs.map(item => item.id === result.id ? result : item);
      renderRunState(result); renderAgents(events, result); updateControls(state);
    },
    onConnection(message) { if (current()) $("stream-state").textContent = message; },
    onError(error) { if (current()) $("stream-state").textContent = `进度暂不可用：${error.message} 可刷新状态。`; },
    async onTerminal(result) {
      if (!current()) return;
      $("stream-state").textContent = "已读取持久化运行结果";
      // 活跃操作完成才刷新产物，历史事件回放不覆盖用户的编辑和版本选择。
      if (active(run)) {
        await refresh().catch(showError);
        if (!current()) return;
        notify(result.status === "succeeded" ? result.type === "execution" ? "任务已成功创建，可在看板中跟进。" :
          state.draft?.status === "ready" ? "草稿已通过审核，请核对后确认执行。" : "运行完成，请查看草稿与审核意见。" :
          `运行${labels[result.status]}：${result.error_message || "请查看记录后重试。"}`, result.status !== "succeeded");
        if (result.type === "execution" && result.status === "succeeded") changeTab("board");
      }
    },
  });
}

function renderRunState(run) {
  $("run-state").textContent = labels[run.status];
  $("run-state").className = `badge ${run.status === "succeeded" ? "good" : ["failed", "interrupted"].includes(run.status) ? "bad" : "warn"}`;
  $("run-error").hidden = !run.error_message;
  $("run-error").textContent = run.error_message ? `${run.error_message}\n运行编号：${run.id}` : "";
  $("retry-run").hidden = !["failed", "interrupted"].includes(run.status);
  $("retry-run").disabled = state.busy || state.dirty || state.stale || state.historical || state.runs.some(active);
}

async function startRun(body) {
  const run = await request(`${path()}/runs`, { method: "POST", body });
  await refresh(); selectRun(run);
  notify(`${labels[run.type]}已提交，后台会继续运行，关闭页面不会取消任务。`);
}

function changeTab(tab) {
  state.tab = tab;
  for (const name of ["draft", "board", "followup"]) {
    const selected = name === tab; const button = $(`tab-${name}`);
    button.classList.toggle("selected", selected); button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1; $(`view-${name}`).hidden = !selected;
  }
}

function setMode(value) {
  mode = value;
  for (const name of ["text", "file"]) {
    $(`mode-${name}`).classList.toggle("selected", name === value);
    $(`mode-${name}`).setAttribute("aria-pressed", String(name === value));
    $(`${name}-input-panel`).hidden = name !== value;
  }
}

function chooseFile(file) {
  if (!file) return;
  if (!/\.(txt|pdf)$/iu.test(file.name)) throw new Error("请选择 TXT 或文本型 PDF 文件。");
  if (file.size > state.config.max_upload_bytes) throw new Error("文件超过上传大小限制。");
  selectedFile = file; $("file-name").textContent = `${file.name} · ${(file.size / 1024).toFixed(1)} KB`;
}

$("meeting-form").addEventListener("submit", event => {
  event.preventDefault();
  void action(async () => {
    const data = { title: $("meeting-title").value.trim(), meeting_date: $("meeting-date").value,
      timezone: $("meeting-timezone").value.trim(), members: parseMembers($("meeting-members").value) };
    let result;
    if (mode === "file") {
      if (!selectedFile) throw new Error("请先选择会议文档。");
      const body = new FormData();
      for (const key of ["title", "meeting_date", "timezone"]) body.append(key, data[key]);
      body.append("members", JSON.stringify(data.members)); body.append("file", selectedFile);
      result = await request("/meetings/upload", { method: "POST", body, timeout: 60000 });
    } else {
      const text = $("meeting-text").value.trim();
      if ([...text].length < 10) throw new Error("会议正文至少需要 10 个字符。");
      if ([...text].length > state.config.max_document_chars) throw new Error("会议正文超过字符限制。");
      result = await request("/meetings", { method: "POST", body: { ...data, text } });
    }
    // 创建已成功时保留表单输入，打开详情失败也可从历史找回。
    await history(0); state.busy = false;
    await openMeeting(result.id, { force: true }); notify("会议已保存。核对原文后，可以启动智能分析。");
  });
});

$("new-meeting").addEventListener("click", async () => {
  if (state.busy || !(await allowLeave())) return;
  resetSelection(); $("create-panel").hidden = false; $("meeting-panel").hidden = true;
  const url = new URL(location.href); url.searchParams.delete("meeting"); globalThis.history.pushState({}, "", url);
  renderHistory(state, openMeeting); updateControls(state); $("meeting-title").focus();
});
$("fill-example").addEventListener("click", () => {
  if (!state.config) return;
  const example = state.config.example;
  $("meeting-title").value = example.title; $("meeting-date").value = example.meeting_date;
  $("meeting-members").value = example.members.map(member => `${member.name}｜${member.role}`).join("\n");
  $("meeting-text").value = example.text; setMode("text"); textCount();
  notify("已填入示例。示例使用固定会议日期 2026-10-21，可按需要修改后保存。");
});
$("mode-text").addEventListener("click", () => setMode("text"));
$("mode-file").addEventListener("click", () => setMode("file"));
$("meeting-file").addEventListener("change", () => { try { chooseFile($("meeting-file").files[0]); } catch (error) { showError(error); } });
for (const name of ["dragenter", "dragover"]) $("dropzone").addEventListener(name, event => { event.preventDefault(); $("dropzone").classList.add("dragover"); });
for (const name of ["dragleave", "drop"]) $("dropzone").addEventListener(name, event => { event.preventDefault(); $("dropzone").classList.remove("dragover"); });
$("dropzone").addEventListener("drop", event => {
  try { if (!state.config) return; if (event.dataTransfer.files.length !== 1) throw new Error("每次请选择一个文件。"); chooseFile(event.dataTransfer.files[0]); }
  catch (error) { showError(error); }
});
function textCount() { $("text-count").textContent = `${[...$("meeting-text").value].length.toLocaleString()} / ${(state.config?.max_document_chars || 24000).toLocaleString()} 字`; }
$("meeting-text").addEventListener("input", textCount);
$("history-refresh").addEventListener("click", () => { void action(() => history()); });
$("history-prev").addEventListener("click", () => { void action(() => history(Math.max(0, state.historyOffset - 12))); });
$("history-next").addEventListener("click", () => { void action(() => history(state.historyOffset + 12)); });
$("refresh-meeting").addEventListener("click", () => { void action(async () => { await refresh(); notify(state.dirty ? "状态已刷新，本地编辑已保留。" : "已加载最新状态。"); }); });
$("analyze").addEventListener("click", () => { void action(async () => {
  if (state.draft && !(await ask("重新分析会议？", "重新分析会生成新的草稿版本，已有版本仍会保留。"))) return;
  await startRun({ type: "analysis" });
}); });
$("add-task").addEventListener("click", () => { try { addTask(dirty); } catch (error) { showError(error); } });
$("discard-edits").addEventListener("click", () => { void action(async () => {
  if (await ask("放弃本地修改？", "将重新加载服务器上的最新草稿，本地未保存内容会丢失。")) await refresh({ forceEditor: true });
}); });
$("save-draft").addEventListener("click", () => { void action(async () => {
  const tasks = collectTasks();
  if (tasks.some(task => task.owner && !task.owner_evidence || task.due_date && !task.due_evidence)) {
    if (!$("edit-note").value.trim()) throw new Error("人工补充负责人或日期时，请填写修改说明。");
    for (const task of tasks) {
      if (task.owner && !task.owner_evidence) task.owner_evidence = { source_type: "human", source_refs: [], confirmation: $("edit-note").value.trim() };
      if (task.due_date && !task.due_evidence) task.due_evidence = { source_type: "human", source_refs: [], confirmation: $("edit-note").value.trim() };
    }
  }
  await request(`${path()}/draft`, { method: "PUT", body: { base_version: state.editVersion, tasks,
    resolution_note: $("edit-note").value.trim() } });
  state.dirty = false; await refresh({ forceEditor: true }); notify("新版本已保存。请提交复核，复核通过后才能执行。");
}); });
$("review-draft").addEventListener("click", () => { void action(() => startRun({ type: "review", draft_version: state.draft.version })); });
$("accept-issues").addEventListener("click", () => { void action(async () => {
  const resolutions = [...$("review-issues").querySelectorAll("input:checked")].map(input => ({
    issue_id: input.dataset.issueId,
    note: [...$("review-issues").querySelectorAll("textarea")].find(area => area.dataset.issueNote === input.dataset.issueId).value.trim(),
  }));
  if (!resolutions.length) throw new Error("请至少勾选一个已核实的问题。");
  if (resolutions.some(item => !item.note)) throw new Error("每个接受的问题都需要填写核实理由。");
  await request(`${path()}/draft/accept-issues`, { method: "POST", body: { draft_version: state.draft.version, resolutions } });
  await refresh({ forceEditor: true }); notify("问题处理记录已保存，新版本已生成。");
}); });
$("draft-version").addEventListener("change", async () => {
  const value = Number($("draft-version").value);
  if (!(await allowLeave())) { $("draft-version").value = String(state.shownDraft?.version || state.editVersion); return; }
  const selected = state.drafts.find(draft => draft.version === value);
  if (!selected) return;
  state.dirty = false; state.historical = value !== state.draft.version;
  state.shownDraft = state.historical ? selected : null; state.editVersion = selected.version;
  renderDraft(state, dirty);
});
$("execute-confirm").addEventListener("change", () => updateControls(state));
$("execute").addEventListener("click", () => { void action(async () => {
  if (!$("execute-confirm").checked) throw new Error("请先勾选核对确认。");
  if (!(await ask("确认创建正式任务？", `将按草稿 V${state.draft.version} 创建 ${state.draft.tasks.length} 个任务。\n确认后此会议不能重新规划，任务状态可在看板更新。`))) return;
  await startRun({ type: "execution", draft_version: state.draft.version, confirm: true,
    acknowledgement: `通过工作台核对草稿 V${state.draft.version} 的任务、负责人、日期与审核意见后明确确认` });
}); });
$("run-select").addEventListener("change", () => { const run = state.runs.find(item => item.id === $("run-select").value); if (run) selectRun(run); });
$("retry-run").addEventListener("click", () => { void action(async () => {
  if (!selectedRun) return;
  const body = {};
  if (selectedRun.type === "execution") {
    if (!(await ask("再次确认执行重试？", "将重试原运行绑定的草稿版本，创建任务前仍会执行版本与幂等检查。"))) return;
    body.confirm = true; body.acknowledgement = "通过工作台重新核对并明确确认重试执行";
  }
  const run = await request(`/runs/${selectedRun.id}/retry`, { method: "POST", body });
  await refresh(); selectRun(run); notify("重试已提交，请查看实时进度。");
}); });
for (const tab of ["draft", "board", "followup"]) {
  $(`tab-${tab}`).addEventListener("click", () => changeTab(tab));
  $(`tab-${tab}`).addEventListener("keydown", event => {
    const names = ["draft", "board", "followup"]; let index = names.indexOf(tab);
    if (["ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key)) {
      event.preventDefault(); index = event.key === "Home" ? 0 : event.key === "End" ? 2 : (index + (event.key === "ArrowRight" ? 1 : 2)) % 3;
      changeTab(names[index]); $(`tab-${names[index]}`).focus();
    }
  });
}
async function changeStatus(id, status) {
  await action(async () => {
    try { await request(`/tasks/${encodeURIComponent(id)}`, { method: "PATCH", body: { status } }); await refresh(); notify("任务状态已保存。"); }
    catch (error) { renderBoard(state, changeStatus); throw error; }
  });
}
$("owner-filter").addEventListener("change", () => renderBoard(state, changeStatus));
for (const format of ["json", "ics"]) $(`export-${format}`).addEventListener("click", () => { void action(async () => {
  const result = await download(state.meeting.id, format);
  notify(format === "ics" ? `已下载 ${result.count} 条日历项${result.skipped ? `；跳过 ${result.skipped} 项，请查看 JSON 或跟进报告中的缺失日期` : ""}。` : "完整会议 JSON 已下载。");
}); });
$("model-check").addEventListener("click", async () => {
  $("model-check").disabled = true; $("model-check").textContent = "正在检查…";
  try { const model = await request("/model/health", { timeout: 15000 }); notify(`模型连接正常 · ${model.model}。此检查验证服务连接，推理将在分析时执行。`); }
  catch (error) { showError(error); }
  finally { $("model-check").disabled = false; $("model-check").textContent = "检查模型连接"; }
});
window.addEventListener("beforeunload", event => { if (state.dirty) { event.preventDefault(); event.returnValue = ""; } });
// 通过 URL 分享会议；前进 / 后退统一加载，避免地址和当前会议脱节。
window.addEventListener("popstate", async () => {
  const target = new URL(location.href).searchParams.get("meeting");
  if (state.busy || !(await allowLeave())) {
    const url = new URL(location.href); state.meeting ? url.searchParams.set("meeting", state.meeting.id) : url.searchParams.delete("meeting");
    globalThis.history.pushState({}, "", url); return;
  }
  if (target) await openMeeting(target, { replace: true, force: true });
  else { resetSelection(); $("create-panel").hidden = false; $("meeting-panel").hidden = true; renderHistory(state, openMeeting); }
});

async function initialize() {
  updateControls(state); renderAgents([], null);
  try {
    const [config] = await Promise.all([request("/ui-config"), request("/health"), history(0)]);
    state.config = config; $("meeting-timezone").value = config.timezone;
    $("meeting-date").value = localDate(config.timezone); $("file-hint").textContent = `支持 TXT、文本型 PDF · 最大 ${(config.max_upload_bytes / 1024 / 1024).toFixed(0)} MB`;
    $("system-status").textContent = "服务运行正常"; textCount(); updateControls(state);
    const meetingId = new URL(location.href).searchParams.get("meeting");
    if (meetingId) await openMeeting(meetingId, { replace: true, force: true });
  } catch (error) { $("system-status").textContent = "服务连接失败"; $("system-status").classList.add("error"); showError(error); }
}
void initialize();
