from starlette.requests import Request

from app.core.errors import error_response


class BodyLimitMiddleware:
    """在表单解析及临时文件写入前限制总请求体，避免只限制解析后文件。"""

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        # Content-Length 仅用于提前拒绝，仍累计真实数据以覆盖分块传输。
        header = request.headers.get("content-length", "")
        if header.isdigit() and int(header) > self.max_bytes:
            return await self.reject(request, receive, send)
        chunks = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > self.max_bytes:
                return await self.reject(request, receive, send)
            chunks.append(message)
            if not message.get("more_body", False):
                break
        iterator = iter(chunks)

        async def bounded_receive():
            try:
                return next(iterator)
            except StopIteration:
                return await receive()

        await self.app(scope, bounded_receive, send)

    async def reject(self, request, receive, send):
        response = error_response(request, "request_too_large", "请求体超过大小限制", 413)
        await response(request.scope, receive, send)
