"""仅发布前端目录与白名单配置，不暴露项目数据、日志和密钥。"""
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from app.core.config import PROJECT_ROOT

WEB_ROOT = PROJECT_ROOT / "app" / "web"
router = APIRouter()


@router.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB_ROOT / "index.html", headers={
        "Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
                                   "img-src 'self' blob:; connect-src 'self'; object-src 'none'; "
                                   "base-uri 'self'; frame-ancestors 'none'; form-action 'self'",
        "X-Content-Type-Options": "nosniff",
    })


@router.get("/api/ui-config", tags=["interface"])
def ui_config(request: Request):
    settings = request.app.state.settings
    return {
        "timezone": settings.timezone,
        "max_upload_bytes": settings.max_upload_bytes,
        "max_document_chars": settings.max_document_chars,
        "example": {
            "title": "登录模块迭代例会", "meeting_date": "2026-10-21",
            "text": (PROJECT_ROOT / "examples" / "meeting.txt").read_text(encoding="utf-8"),
            "members": [{"name": "张三", "role": "后端"}, {"name": "李四", "role": "测试"},
                        {"name": "王五", "role": "项目经理"}],
        },
    }
