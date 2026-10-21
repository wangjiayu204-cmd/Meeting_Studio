from io import BytesIO
from pathlib import PurePosixPath
import re

from pypdf import PdfReader

from app.domain.schemas import Paragraph


class DocumentError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def normalize_text(text: str, max_chars: int) -> str:
    if len(text) > max_chars:
        raise DocumentError("document_too_long", f"文档超过 {max_chars} 字符，请拆分后提交", 413)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").strip()
    if len(text) < 10:
        raise DocumentError("document_empty", "可提取文字不足 10 字符；扫描 PDF 请先进行 OCR")
    return text


def split_paragraphs(text: str) -> list[Paragraph]:
    lines = [re.sub(r"[ \t]+", " ", line).strip()
             for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return [Paragraph(id=f"p{index:04d}", text=line)
            for index, line in enumerate((line for line in lines if line), 1)]


def parse_document(filename: str, content: bytes, *, max_bytes: int,
                   max_chars: int, max_pdf_pages: int) -> str:
    if len(content) > max_bytes:
        raise DocumentError("upload_too_large", f"文件超过 {max_bytes} 字节限制", 413)
    suffix = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    if suffix == ".txt":
        text = None
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                text = content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise DocumentError("text_encoding_error", "无法识别 TXT 编码，请转换为 UTF-8")
        return normalize_text(text, max_chars)
    if suffix != ".pdf":
        raise DocumentError("unsupported_document", "仅支持 TXT 和文本型 PDF", 415)
    try:
        reader = PdfReader(BytesIO(content), strict=True)
        if reader.is_encrypted:
            raise DocumentError("encrypted_pdf", "不支持加密 PDF，请先解密")
        if len(reader.pages) > max_pdf_pages:
            raise DocumentError("pdf_too_many_pages", f"PDF 最多支持 {max_pdf_pages} 页，请拆分后上传", 413)
        chunks = []
        length = 0
        for page in reader.pages:
            chunk = page.extract_text() or ""
            length += len(chunk) + (1 if chunks else 0)
            if length > max_chars:
                raise DocumentError("document_too_long", f"文档超过 {max_chars} 字符，请拆分后上传", 413)
            chunks.append(chunk)
        return normalize_text("\n".join(chunks), max_chars)
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError("invalid_pdf", "PDF 无法解析，请确认文件完整且包含可提取文字") from exc
