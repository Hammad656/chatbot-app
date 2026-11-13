import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from zipfile import BadZipFile, LargeZipFile

from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from lxml.etree import XMLSyntaxError
from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_EXTRACTED_CHARS = 500_000
MAX_PDF_PAGES = 80
MAX_DOCX_UNCOMPRESSED_BYTES = 24 * 1024 * 1024
CHUNK_SIZE = 2_000
CHUNK_OVERLAP = 240


@dataclass(frozen=True)
class DocumentChunk:
    text: str
    section: str


def safe_filename(filename: str) -> str:
    basename = PurePosixPath(filename.replace("\\", "/")).name
    cleaned = "".join(
        character for character in basename if character.isprintable()
    ).strip()
    return cleaned[:255] or "document"


def _extract_pdf(content: bytes) -> list[tuple[str, str]]:
    reader = PdfReader(BytesIO(content), strict=False)
    if reader.is_encrypted:
        raise ValueError("This PDF is password-protected. Upload an unlocked PDF.")
    if len(reader.pages) > MAX_PDF_PAGES:
        raise ValueError(f"PDFs must contain {MAX_PDF_PAGES} pages or fewer.")

    sections = []
    extracted_size = 0
    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        if text.strip():
            extracted_size += len(text)
            if extracted_size > MAX_EXTRACTED_CHARS:
                raise ValueError(
                    f"Document text exceeds the {MAX_EXTRACTED_CHARS:,}-character processing limit."
                )
            sections.append((f"Page {page_number}", text))
    return sections


def _extract_docx(content: bytes) -> list[tuple[str, str]]:
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            if (
                sum(item.file_size for item in archive.infolist())
                > MAX_DOCX_UNCOMPRESSED_BYTES
            ):
                raise ValueError("The DOCX file expands beyond the allowed size.")
    except (BadZipFile, LargeZipFile) as error:
        raise ValueError("The uploaded DOCX file is invalid or unreadable.") from error

    try:
        document = Document(BytesIO(content))
    except (
        BadZipFile,
        KeyError,
        LargeZipFile,
        PackageNotFoundError,
        ValueError,
        XMLSyntaxError,
    ) as error:
        raise ValueError("The uploaded DOCX file is invalid or unreadable.") from error
    current_section = "Document"
    sections: list[tuple[str, str]] = []
    extracted_size = 0
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        extracted_size += len(text)
        if extracted_size > MAX_EXTRACTED_CHARS:
            raise ValueError(
                f"Document text exceeds the {MAX_EXTRACTED_CHARS:,}-character processing limit."
            )
        style_name = paragraph.style.name.lower() if paragraph.style else ""
        if style_name.startswith("heading"):
            current_section = text[:160]
        else:
            sections.append((current_section, text))

    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                text = " | ".join(cells)
                extracted_size += len(text)
                if extracted_size > MAX_EXTRACTED_CHARS:
                    raise ValueError(
                        f"Document text exceeds the {MAX_EXTRACTED_CHARS:,}-character processing limit."
                    )
                sections.append((current_section, text))
    return sections


def extract_document_chunks(filename: str, content: bytes) -> list[DocumentChunk]:
    extension = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    if extension == ".pdf":
        if not content.startswith(b"%PDF-"):
            raise ValueError("The uploaded file is not a valid PDF.")
        try:
            sections = _extract_pdf(content)
        except (PdfReadError, EOFError, KeyError) as error:
            raise ValueError(
                "The uploaded PDF file is invalid or unreadable."
            ) from error
    elif extension == ".docx":
        if not content.startswith(b"PK"):
            raise ValueError("The uploaded file is not a valid DOCX document.")
        sections = _extract_docx(content)
    else:
        raise ValueError("Upload a PDF or DOCX file.")

    extracted_size = sum(len(text) for _, text in sections)
    if extracted_size > MAX_EXTRACTED_CHARS:
        raise ValueError(
            f"Document text exceeds the {MAX_EXTRACTED_CHARS:,}-character processing limit."
        )
    if not sections:
        raise ValueError(
            "No selectable text was found. Scanned PDFs need OCR before upload."
        )

    chunks: list[DocumentChunk] = []
    for section, text in sections:
        normalized = re.sub(r"[ \t]+", " ", text)
        normalized = re.sub(r"\s*\n\s*", "\n", normalized).strip()
        start = 0
        while start < len(normalized):
            end = min(start + CHUNK_SIZE, len(normalized))
            if end < len(normalized):
                boundary = max(
                    normalized.rfind(" ", start + int(CHUNK_SIZE * 0.7), end),
                    normalized.rfind("\n", start + int(CHUNK_SIZE * 0.7), end),
                )
                if boundary > start:
                    end = boundary
            text_chunk = normalized[start:end].strip()
            if text_chunk:
                chunks.append(DocumentChunk(text=text_chunk, section=section[:160]))
            if end >= len(normalized):
                break
            start = max(start + 1, end - CHUNK_OVERLAP)

    return chunks
