import unittest
from io import BytesIO

from docx import Document

from resolveai.documents import CHUNK_SIZE, extract_document_chunks


class DocumentChunkingTests(unittest.TestCase):
    def test_extracts_docx_headings_and_chunks_long_sections(self):
        document = Document()
        document.add_heading("Experience", level=1)
        document.add_paragraph("Built software for customers. " * 100)
        stream = BytesIO()
        document.save(stream)

        chunks = extract_document_chunks("company.docx", stream.getvalue())

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk.section == "Experience" for chunk in chunks))
        self.assertTrue(all(len(chunk.text) <= CHUNK_SIZE for chunk in chunks))
        self.assertIn(chunks[0].text[-100:], chunks[1].text)

    def test_rejects_unsupported_and_invalid_files(self):
        with self.assertRaisesRegex(ValueError, "PDF or DOCX"):
            extract_document_chunks("company.txt", b"plain text")
        with self.assertRaisesRegex(ValueError, "valid PDF"):
            extract_document_chunks("company.pdf", b"not a pdf")
        with self.assertRaisesRegex(ValueError, "valid DOCX"):
            extract_document_chunks("company.docx", b"not a docx")


if __name__ == "__main__":
    unittest.main()
