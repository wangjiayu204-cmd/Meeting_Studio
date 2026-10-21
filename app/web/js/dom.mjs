export const $ = id => document.getElementById(id);

// 原文、模型输出、错误正文均以文本节点渲染，不解析为 HTML。
export function node(tag, className = "", text) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text !== undefined) result.textContent = String(text);
  return result;
}
export function empty(container, title, description) {
  const block = node("div", "empty-state");
  block.append(node("div", "empty-symbol", "✧"), node("h3", "", title), node("p", "", description));
  container.replaceChildren(block);
}
export function notify(message, error = false) {
  const target = $("notice");
  target.textContent = message; target.className = `notice${error ? " error" : ""}`; target.hidden = false;
}
export function showError(error) {
  const details = (error.details || []).filter(item => item.message).map(item => `${(item.location || []).join(".")}：${item.message}`);
  notify([error.message, ...details.slice(0, 5), error.requestId ? `请求编号：${error.requestId}` : ""].filter(Boolean).join("\n"), true);
}
export function refs(values) {
  const result = node("div", "refs"); result.append(node("span", "", "原文依据"));
  for (const ref of values) {
    const button = node("button", "ref-button", ref); button.type = "button";
    button.addEventListener("click", () => {
      const target = document.getElementById(`source-${ref}`);
      if (!target) return;
      document.querySelectorAll(".paragraph.highlight").forEach(p => p.classList.remove("highlight"));
      target.classList.add("highlight"); target.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }); result.append(button);
  }
  return result;
}
export function selectOptions(select, items, selected) {
  select.replaceChildren(...items.map(([value, label]) => {
    const option = node("option", "", label); option.value = value; return option;
  }));
  if (selected !== undefined) select.value = selected;
}
export function ask(title, message) {
  const dialog = $("confirm-dialog");
  $("dialog-title").textContent = title; $("dialog-message").textContent = message;
  dialog.returnValue = "cancel";
  return new Promise(resolve => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), { once: true });
    dialog.showModal();
  });
}
