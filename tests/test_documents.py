from io import BytesIO

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.documents.parser import DocumentError, parse_document, split_paragraphs


def make_pdf(*, text="Meeting action: deliver the integration report.", pages=1, password=None):
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=612, height=792)
        if text:
            font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                     NameObject("/Subtype"): NameObject("/Type1"),
                                     NameObject("/BaseFont"): NameObject("/Helvetica")})
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
            stream = DecodedStreamObject()
            stream.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii"))
            page[NameObject("/Contents")] = writer._add_object(stream)
    if password:
        writer.encrypt(password)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def parse(filename, data, **overrides):
    return parse_document(filename, data, **{
        "max_bytes": 100000, "max_chars": 24000, "max_pdf_pages": 100, **overrides,
    })


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "gb18030"])
def test_txt_encodings_and_stable_paragraphs(encoding):
    text = "会议决定完成登录模块联调。\r\n\r\n张三提交联调报告。"
    parsed = parse("会议.TXT", text.encode(encoding))
    paragraphs = split_paragraphs(parsed)
    assert [p.id for p in paragraphs] == ["p0001", "p0002"]
    assert paragraphs[1].text == "张三提交联调报告。"
    assert split_paragraphs(parsed) == paragraphs


def test_real_text_pdf():
    assert "deliver the integration report" in parse("meeting.pdf", make_pdf())


def test_pdf_character_limit_includes_only_actual_text():
    text = parse("meeting.pdf", make_pdf())
    assert parse("meeting.pdf", make_pdf(), max_chars=len(text)) == text


@pytest.mark.parametrize("filename,data,options,code,status", [
    ("scan.pdf", make_pdf(text=""), {}, "document_empty", 422),
    ("encrypted.pdf", make_pdf(password="secret"), {}, "encrypted_pdf", 422),
    ("bad.pdf", b"not a pdf", {}, "invalid_pdf", 422),
    ("many.pdf", make_pdf(pages=2), {"max_pdf_pages": 1}, "pdf_too_many_pages", 413),
    ("long.pdf", make_pdf(), {"max_chars": 10}, "document_too_long", 413),
    ("bad.txt", b"\xff", {}, "text_encoding_error", 422),
    ("empty.txt", b"   ", {}, "document_empty", 422),
    ("large.txt", b"a" * 20, {"max_bytes": 10}, "upload_too_large", 413),
    ("long.txt", b"a" * 30, {"max_chars": 20}, "document_too_long", 413),
    ("meeting.html", b"content", {}, "unsupported_document", 415),
])
def test_document_failures(filename, data, options, code, status):
    with pytest.raises(DocumentError) as captured:
        parse(filename, data, **options)
    assert captured.value.code == code
    assert captured.value.status_code == status
