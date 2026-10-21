import { $, node, refs, selectOptions } from "./dom.mjs";
import { labels, taskPayload } from "./state.mjs";

export function renderEditor(tasks, editable, onChange) {
  const container = $("draft-editor"); container.replaceChildren();
  tasks.forEach((task, index) => container.append(taskCard(task, index, editable, onChange)));
}

function taskCard(task, index, editable, onChange) {
  const card = node("article", "task-card"); card.original = structuredClone(task);
  const header = node("div", "task-card-heading");
  header.append(node("span", "task-number", `ACTION ${String(index + 1).padStart(2, "0")}`));
  if (editable) {
    const remove = node("button", "icon-button", "×"); remove.type = "button";
    remove.setAttribute("aria-label", `删除任务 ${task.title || index + 1}`);
    remove.addEventListener("click", () => { card.remove(); onChange(); }); header.append(remove);
  }
  card.append(header);
  const fields = node("div", "task-fields");
  let target = fields;
  const field = (key, label, { type = "text", full = false, area = false, max } = {}) => {
    const wrapper = node("label", full ? "full" : "", label);
    const input = node(area ? "textarea" : key === "priority" ? "select" : "input");
    input.dataset.field = key;
    if (!area && key !== "priority") input.type = type;
    if (area) input.rows = 2;
    if (max) input.maxLength = max;
    if (key === "title") input.classList.add("title-input");
    if (key === "priority") selectOptions(input, ["low", "medium", "high"].map(value => [value, labels[value]]), task[key]);
    else input.value = Array.isArray(task[key]) ? task[key].join(key === "needs_confirmation" ? "\n" : ", ") : task[key] || "";
    input.disabled = !editable;
    input.addEventListener("input", onChange);
    wrapper.append(input); target.append(wrapper);
  };
  field("title", "任务名称", { full: true, max: 240 });
  field("owner", "负责人（可留空）", { max: 100 }); field("due_date", "截止日期（可留空）", { type: "date" });
  field("deliverable", "预期产出", { full: true, max: 2000 });
  field("description", "任务说明", { full: true, area: true, max: 4000 });
  field("priority", "优先级", { full: true });
  const advanced = node("details", "task-advanced full");
  advanced.append(node("summary", "", "依赖、原文依据与待确认事项"));
  target = node("div", "task-fields"); advanced.append(target); fields.append(advanced);
  field("local_id", "任务标识（供依赖关系引用）", { full: true });
  field("dependencies", "依赖任务标识（逗号分隔）", { full: true });
  field("source_refs", "原文段落引用（逗号分隔）", { full: true });
  field("needs_confirmation", "待确认事项（每行一项）", { full: true, area: true });
  card.append(fields, refs(task.source_refs));
  const origins = [task.owner_evidence, task.due_evidence].filter(item => item?.source_type === "human");
  if (origins.length) card.append(node("p", "evidence-note", `人工确认依据：${[...new Set(origins.map(item => item.confirmation))].join("；")}`));
  return card;
}

export function collectTasks() {
  return [...$("draft-editor").querySelectorAll(".task-card")].map(card => taskPayload(card.original,
    Object.fromEntries([...card.querySelectorAll("[data-field]")].map(input => [input.dataset.field, input.value]))));
}

export function addTask(onChange) {
  const count = $("draft-editor").querySelectorAll(".task-card").length;
  if (count >= 100) throw new Error("单份计划最多包含 100 个任务。");
  const suffix = globalThis.crypto?.randomUUID?.().replaceAll("-", "").slice(0, 12) || `${Date.now()}`;
  const task = { local_id: `task_${suffix}`, title: "", description: "", deliverable: "", owner: null,
    due_date: null, owner_evidence: null, due_evidence: null, priority: "medium", dependencies: [],
    source_refs: [], needs_confirmation: [] };
  const card = taskCard(task, count, true, onChange); $("draft-editor").append(card);
  onChange(); card.querySelector("input").focus();
}
