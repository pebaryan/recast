import io
from xml.sax.saxutils import escape
import zipfile


REFERENCE = "Start with what you can observe. Explain the result in ordinary words. Give each idea enough room, and keep the reader beside you. A short sentence can carry a simple observation. A longer sentence can connect that observation to a careful interpretation without making the conclusion stronger than the evidence.\n\nGood explanations need clear transitions. They also need restraint. Say what happened, explain the connection, and leave open questions open. Avoid grand claims and ornamental phrases. Let precise words do the work."
PROFILE = {"summary": "Plain, measured explanations.", "traits": [{"dimension": "Vocabulary", "observation": "Uses everyday words.", "evidence": "ordinary words", "confidence": "high"}], "rules": ["Use ordinary words and varied sentence lengths."], "avoid": ["Do not overstate conclusions."]}


def docx_bytes(paragraphs=None, raw_xml=None):
    body = raw_xml or "".join(f"<w:p><w:r><w:t>{escape(p)}</w:t></w:r></w:p>" for p in (paragraphs or REFERENCE.split("\n\n")))
    xml = f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>'
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", xml)
    return data.getvalue()


def pdf_bytes(text=REFERENCE):
    # Minimal text-layer fixture with a standard font, not a user deliverable.
    lines = text.replace("\n", " ").split(". ")
    commands = ["BT /F1 11 Tf 50 740 Td 15 TL"]
    for index, line in enumerate(lines):
        if index:
            commands.append("T*")
        safe = line.replace("\\", "\\\\").replace("(", r"\(").replace(")", r"\)")
        commands.append(f"({safe}) Tj")
    commands.append("ET")
    stream = "\n".join(commands).encode("ascii", "replace")
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>", b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>", b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"]
    data, offsets = bytearray(b"%PDF-1.4\n"), [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    start = len(data)
    data.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode())
    return bytes(data)


class FakeModel:
    fingerprint = "fixture-model"

    def __init__(self, damage=False, findings=False, truncate=False):
        self.calls, self.damage, self.findings, self.truncate = [], damage, findings, truncate

    def check_cancelled(self):
        pass

    def complete(self, system, data, schema, stage, **kwargs):
        from recast.llm import ModelError
        self.calls.append({"stage": stage})
        if stage == "style":
            import copy
            return copy.deepcopy(PROFILE)
        if self.truncate:
            raise ModelError("rewrite did not finish normally (possibly truncated)")
        if stage == "rewrite":
            return {"blocks": [{"id": item["id"], "text": item["text"].replace("[[", "[") if self.damage else item["text"].replace("We checked", "We reviewed")} for item in data["source_blocks"]]}
        blocks = data["blocks"]
        return {"claims": [{"block_id": block["block_id"], "text": block["source"]} for block in blocks], "findings": [{"block_id": blocks[0]["block_id"], "type": "qualification", "severity": "error", "explanation": "Planted semantic error.", "source_excerpt": blocks[0]["source"], "output_excerpt": blocks[0]["output"]}] if self.findings else []}
