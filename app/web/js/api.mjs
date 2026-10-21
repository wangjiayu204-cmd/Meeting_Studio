export class ApiError extends Error {
  constructor(message, { status = 0, code = "network_error", requestId = "", details = [] } = {}) {
    super(message); Object.assign(this, { status, code, requestId, details });
  }
}

export async function request(path, { method = "GET", body, timeout = 25000 } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const multipart = typeof FormData !== "undefined" && body instanceof FormData;
    const response = await fetch(`/api${path}`, {
      method, signal: controller.signal, cache: "no-store",
      headers: body !== undefined && !multipart ? { "Content-Type": "application/json" } : {},
      body: body === undefined ? undefined : multipart ? body : JSON.stringify(body),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new ApiError(data.error?.message || `请求失败（${response.status}）`, {
        status: response.status, code: data.error?.code || "http_error",
        requestId: data.request_id || response.headers.get("X-Request-ID") || "",
        details: data.error?.details || [],
      });
    }
    return await response.json();
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(error.name === "AbortError" ? "请求超时，请刷新状态后重试。" : "无法连接服务，请检查网络连接。");
  } finally { clearTimeout(timer); }
}

export async function optional(path, code) {
  try { return await request(path); }
  catch (error) { if (error.status === 404 && error.code === code) return null; throw error; }
}

export async function download(meetingId, format) {
  let response;
  try { response = await fetch(`/api/meetings/${encodeURIComponent(meetingId)}/export?format=${format}`, { cache: "no-store" }); }
  catch { throw new ApiError("下载失败，请检查网络后重试。"); }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new ApiError(data.error?.message || "下载失败", { status: response.status,
      code: data.error?.code, requestId: data.request_id });
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url; anchor.download = `meeting-${meetingId}.${format}`;
  document.body.append(anchor); anchor.click(); anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return { count: Number(response.headers.get("X-PM-Task-Count")), skipped: Number(response.headers.get("X-PM-Skipped-Count")) };
}
