"""可读的诊断产物；不保存 HTTP 鉴权头。"""

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from time import monotonic
from uuid import uuid4


class ArtifactRecord:
    def __init__(self, root: Path | None, label: str, secrets: tuple[str, ...] = ()):
        self.id = uuid4().hex
        self.started = monotonic()
        self.secrets = tuple(value for value in secrets if value and value != "EMPTY")
        safe_label = re.sub(r"[^a-zA-Z0-9_-]", "_", label)[:64]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        self.path = root / f"{stamp}_{self.id[:8]}_{safe_label}" if root else None
        if self.path:
            self.path.mkdir(parents=True, exist_ok=False)

    def write(self, filename: str, value) -> None:
        if self.path is None:
            return
        text = value if isinstance(value, str) else json.dumps(
            value, ensure_ascii=False, indent=2, default=str, allow_nan=False,
        ) + "\n"
        for secret in self.secrets:
            text = text.replace(secret, "[REDACTED]")
        # 文件名只由调用方固定生成，不接受用户路径。
        (self.path / filename).write_text(text, encoding="utf-8")

    def finish(self, status: str, **details) -> None:
        self.write("summary.json", {
            "trace_id": self.id, "status": status,
            "elapsed_seconds": round(monotonic() - self.started, 3), **details,
        })
