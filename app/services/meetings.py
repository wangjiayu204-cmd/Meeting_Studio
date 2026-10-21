from app.core.config import Settings
from app.core.errors import AppError
from app.documents.parser import DocumentError, normalize_text, parse_document, split_paragraphs
from app.domain.schemas import Meeting, MeetingCreate, MeetingPage
from app.repositories.meetings import MeetingRepository


class MeetingService:
    def __init__(self, repository: MeetingRepository, settings: Settings):
        self.repository = repository
        self.settings = settings

    def create(self, data: MeetingCreate) -> Meeting:
        try:
            text = normalize_text(data.text, self.settings.max_document_chars)
        except DocumentError as exc:
            raise AppError(exc.code, exc.message, exc.status_code) from exc
        metadata = data.model_dump(exclude={"text"})
        if "timezone" not in data.model_fields_set:
            metadata["timezone"] = self.settings.timezone
        meeting = Meeting(**metadata, text=text,
                          paragraphs=split_paragraphs(text))
        return self.repository.create(meeting)

    def parse_upload(self, filename: str, content: bytes) -> str:
        try:
            return parse_document(
                filename, content, max_bytes=self.settings.max_upload_bytes,
                max_chars=self.settings.max_document_chars,
                max_pdf_pages=self.settings.max_pdf_pages,
            )
        except DocumentError as exc:
            raise AppError(exc.code, exc.message, exc.status_code) from exc

    def get(self, meeting_id: str) -> Meeting:
        meeting = self.repository.get(meeting_id)
        if meeting is None:
            raise AppError("meeting_not_found", "会议不存在", 404)
        return meeting

    def list(self, limit: int, offset: int) -> MeetingPage:
        return self.repository.list(limit, offset)
