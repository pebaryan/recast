"""Text-only extraction. Neither LaTeX nor uploaded document code is executed."""

from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import asdict, dataclass, field

SOURCE_EXTENSIONS = {".txt", ".md", ".markdown", ".tex", ".latex"}
REFERENCE_EXTENSIONS = {".docx", ".pdf", ".txt", ".md", ".markdown"}
MAX_REFERENCE_BYTES = 40 * 1024 * 1024
MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_EXTRACTED_CHARS = 4 * 1024 * 1024
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class DocumentError(ValueError):
    pass


@dataclass
class Document:
    name: str
    format: str
    text: str
    sha256: str
    warnings: list[str] = field(default_factory=list)
    pages: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > 20 * 1024 * 1024:
                raise DocumentError("DOCX document text is too large to extract.")
            xml = archive.read(info)
        if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
            raise DocumentError("DOCX XML declarations with entities are unsupported.")
        root = ET.fromstring(xml)
    except (zipfile.BadZipFile, KeyError, ET.ParseError, RuntimeError) as exc:
        raise DocumentError(f"Cannot read DOCX document text: {exc}") from exc

    def visible(node: ET.Element) -> str:
        if node.tag in {W + "del", W + "moveFrom"}:
            return ""
        if node.tag == W + "t":
            return node.text or ""
        if node.tag == W + "tab":
            return "\t"
        if node.tag in {W + "br", W + "cr"}:
            return "\n"
        return "".join(visible(child) for child in node)

    # Paragraph iteration includes table-cell paragraphs in document order.
    return "\n\n".join(visible(p) for p in root.iter(W + "p") if visible(p).strip())


def _pdf_text(data: bytes) -> tuple[str, int, list[str]]:
    converter = os.environ.get("RECAST_PDFTOTEXT") or shutil.which("pdftotext")
    if converter:
        with tempfile.TemporaryDirectory(prefix="recast-pdf-") as folder:
            source = Path(folder) / "reference.pdf"
            extracted = Path(folder) / "reference.txt"
            source.write_bytes(data)
            try:
                completed = subprocess.run(
                    [converter, "-enc", "UTF-8", "-eol", "unix", str(source), str(extracted)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=60,
                    check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise DocumentError(f"PDF text extraction failed: {exc}") from exc
            if completed.returncode:
                detail = completed.stderr.decode("utf-8", "replace").strip()[:500]
                raise DocumentError(f"Cannot extract PDF text. Check that it is readable and unencrypted. {detail}")
            if extracted.stat().st_size > MAX_EXTRACTED_CHARS * 4:
                raise DocumentError("Extracted PDF text exceeds the extraction limit.")
            text = extracted.read_text(encoding="utf-8", errors="replace")
        pages = text.split("\f")
        if pages and not pages[-1].strip():
            pages.pop()
    else:
        try:
            from pypdf import PdfReader  # Optional fallback; not required with Poppler.
        except ImportError as exc:
            raise DocumentError("PDF extraction needs pdftotext (Poppler), or the optional pypdf package. Set RECAST_PDFTOTEXT to the executable path.") from exc
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                raise DocumentError("Encrypted PDF references are unsupported. Supply an unlocked copy.")
            pages = [page.extract_text() or "" for page in reader.pages]
        except DocumentError:
            raise
        except Exception as exc:
            raise DocumentError(f"Cannot extract PDF text: {exc}") from exc
    if sum(len(re.findall(r"[^\W\d_]", page, re.UNICODE)) for page in pages) < 30:
        raise DocumentError("This PDF has no usable text layer. OCR is required for an image-only or scanned reference; OCR is not enabled.")
    empty = sum(not page.strip() for page in pages)
    warnings = ["PDF reading order is inferred; review the extracted reference, especially for columns and hyphenation."]
    if empty:
        warnings.append(f"{empty} PDF page(s) had no extractable text and were skipped; they may need OCR.")
    return "\n\n".join(page.strip() for page in pages if page.strip()), len(pages), warnings


def read_document(name: str, data: bytes, role: str) -> Document:
    if role not in {"source", "reference"}:
        raise DocumentError("Document role must be source or reference.")
    extension = Path(name).suffix.lower()
    allowed = SOURCE_EXTENSIONS if role == "source" else REFERENCE_EXTENSIONS
    if extension not in allowed:
        raise DocumentError(f"Unsupported {role} format {extension or '(none)'}. Use {', '.join(sorted(allowed))}.")
    limit = MAX_SOURCE_BYTES if role == "source" else MAX_REFERENCE_BYTES
    if not data or len(data) > limit:
        raise DocumentError(f"The {role} must be nonempty and at most {limit // (1024 * 1024)} MB.")
    warnings: list[str] = []
    pages = None
    if extension == ".docx":
        text = _docx_text(data)
        warnings.append("DOCX body text and table cells extracted. Images, headers, footers, and notes are not used as style evidence.")
    elif extension == ".pdf":
        text, pages, warnings = _pdf_text(data)
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise DocumentError("Save text, Markdown, and LaTeX files as UTF-8.") from exc
        if "\x00" in text:
            raise DocumentError("The file contains binary data rather than UTF-8 prose.")
    if not text.strip():
        raise DocumentError("No readable text was found in the document.")
    if len(text) > MAX_EXTRACTED_CHARS:
        raise DocumentError("Extracted text exceeds the 4-million-character limit.")
    fmt = "latex" if extension in {".tex", ".latex"} else "markdown" if extension in {".md", ".markdown"} else extension[1:]
    if role == "reference" and len(text.split()) < 80:
        warnings.append("This reference is short; its writing style may be difficult to infer reliably.")
    if fmt == "latex" and re.search(r"\\(?:input|include|bibliography|addbibresource)\b", text):
        warnings.append("External LaTeX files and bibliographies are preserved as commands but are not opened or expanded. Supply a self-contained source for complete coverage.")
    return Document(Path(name).name, fmt, text, hashlib.sha256(data).hexdigest(), warnings, pages)


def read_path(path: Path, role: str) -> Document:
    limit = MAX_SOURCE_BYTES if role == "source" else MAX_REFERENCE_BYTES
    if path.stat().st_size > limit:
        raise DocumentError(f"{path.name} exceeds the {role} upload limit.")
    return read_document(path.name, path.read_bytes(), role)
