import shutil
import unittest

from recast.documents import DocumentError, read_document
from .helpers import docx_bytes, pdf_bytes


class DocumentTests(unittest.TestCase):
    def test_docx_visible_order_and_tracked_deletions(self):
        raw = '<w:p><w:r><w:t>First</w:t></w:r><w:hyperlink><w:r><w:t> linked</w:t></w:r></w:hyperlink><w:del><w:r><w:t> deleted</w:t></w:r></w:del></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl><w:p><w:ins><w:r><w:t>Last</w:t></w:r></w:ins></w:p>'
        result = read_document("voice.docx", docx_bytes(raw_xml=raw), "reference")
        self.assertEqual(result.text, "First linked\n\nCell\n\nLast")

    def test_roles_and_binary_files(self):
        for name, data, role in [("source.pdf", b"fake", "source"), ("voice.exe", b"fake", "reference"), ("a.txt", b"a\x00b", "source"), ("a.md", b"\xff", "source"), ("bad.docx", b"bad zip", "reference")]:
            with self.subTest(name=name), self.assertRaises(DocumentError):
                read_document(name, data, role)

    def test_utf8_bom_and_crlf_are_preserved(self):
        result = read_document("source.tex", b"\xef\xbb\xbfHello.\r\n", "source")
        self.assertEqual(result.text, "Hello.\r\n")
        self.assertEqual(result.format, "latex")

    @unittest.skipUnless(shutil.which("pdftotext"), "Poppler not installed")
    def test_pdf_text_layer_and_page_count(self):
        result = read_document("voice.pdf", pdf_bytes(), "reference")
        self.assertIn("ordinary words", result.text)
        self.assertEqual(result.pages, 1)

    @unittest.skipUnless(shutil.which("pdftotext"), "Poppler not installed")
    def test_image_only_pdf_reports_ocr_requirement(self):
        with self.assertRaisesRegex(DocumentError, "OCR"):
            read_document("scan.pdf", pdf_bytes(""), "reference")

    def test_latex_external_files_not_expanded(self):
        result = read_document("a.tex", br"\input{private-file} Prose.", "source")
        self.assertTrue(any("not opened" in warning for warning in result.warnings))
