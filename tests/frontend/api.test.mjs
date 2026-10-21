import test from "node:test";
import assert from "node:assert/strict";
import { request, ApiError, optional, download } from "../../app/web/js/api.mjs";
import { node } from "../../app/web/js/dom.mjs";

test("HTTP 错误保留错误码与请求 ID，验证失败可定位", async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({ error: { code: "draft_version_conflict", message: "草稿已更新", details: [{ current_version: 5 }] }, request_id: "abc" }), { status: 409 });
  await assert.rejects(request("/meetings/a/draft"), error => {
    assert.ok(error instanceof ApiError); assert.equal(error.status, 409); assert.equal(error.code, "draft_version_conflict");
    assert.equal(error.requestId, "abc"); assert.deepEqual(error.details, [{ current_version: 5 }]); return true;
  });
});

test("网络失败与超时都有可读错误，未知 404 不被隐藏", async () => {
  globalThis.fetch = async () => { throw new TypeError("fetch failed"); };
  await assert.rejects(request("/health"), /无法连接服务/u);
  globalThis.fetch = async () => { const error = new Error(); error.name = "AbortError"; throw error; };
  await assert.rejects(request("/health"), /请求超时/u);
  globalThis.fetch = async () => new Response(JSON.stringify({ error: { code: "meeting_not_found", message: "会议不存在" } }), { status: 404 });
  await assert.rejects(optional("/meetings/a", "draft_not_found"), error => error.code === "meeting_not_found");
});

test("JSON 与上传请求按类型传递，接口不缓存", async () => {
  let captured;
  globalThis.fetch = async (url, options) => { captured = { url, ...options }; return new Response('{"ok":true}'); };
  await request("/meetings", { method: "POST", body: { title: "会议" } });
  assert.equal(captured.url, "/api/meetings"); assert.equal(captured.cache, "no-store");
  assert.equal(captured.headers["Content-Type"], "application/json");
  const form = new FormData(); form.append("title", "会议");
  await request("/meetings/upload", { method: "POST", body: form });
  assert.equal(captured.body, form); assert.equal(captured.headers["Content-Type"], undefined);
});

test("正文与模型文本通过 textContent 渲染", () => {
  globalThis.document = { createElement: tag => ({ tag }) };
  const value = '<img src=x onerror="alert(1)"><script>bad()</script>';
  const result = node("p", "paragraph", value);
  assert.equal(result.textContent, value); assert.equal(result.innerHTML, undefined);
});

test("下载先检查业务错误，成功时使用 Blob 和明确文件名", async () => {
  globalThis.fetch = async () => new Response('{"error":{"message":"确认执行后才能导出日历","code":"calendar_not_ready"}}', { status: 409 });
  await assert.rejects(download("abc", "ics"), /确认执行/u);
  let clicked = false, removed = false, anchor;
  globalThis.document = { createElement: () => (anchor = { click() { clicked = true; }, remove() { removed = true; } }), body: { append() {} } };
  globalThis.fetch = async () => new Response("BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n", { headers: { "X-PM-Task-Count": "2", "X-PM-Skipped-Count": "1" } });
  const result = await download("abc", "ics");
  assert.deepEqual(result, { count: 2, skipped: 1 });
  assert.equal(anchor.download, "meeting-abc.ics"); assert.ok(anchor.href.startsWith("blob:"));
  assert.equal(clicked && removed, true);
});
