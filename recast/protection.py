"""Conservative lexical protection; a LaTeX compiler is never invoked."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re


@dataclass
class Block:
    id: str
    text: str
    editable: bool


def _balanced(text: str, start: int, opening: str, closing: str) -> int:
    depth, pos = 0, start
    while pos < len(text):
        if text[pos] == "\\":
            pos += 2
            continue
        if text[pos] == opening:
            depth += 1
        elif text[pos] == closing:
            depth -= 1
            if depth == 0:
                return pos + 1
        pos += 1
    return len(text)  # Malformed constructs remain untouched, rather than guessed.


def _latex_ranges(text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    begin = re.search(r"\\begin\s*\{document\}", text)
    if begin:
        ranges.append((0, begin.end()))
    ending = re.search(r"\\end\s*\{document\}", text)
    if ending:
        ranges.append((ending.start(), len(text)))
    for definition in re.finditer(r"\\(?:def|gdef|edef|xdef)\b[^{}]*\{", text):
        ranges.append((definition.start(), _balanced(text, definition.end() - 1, "{", "}")))
    # Unknown environments are opaque; known prose containers can be restyled.
    prose = {"document", "abstract", "itemize", "enumerate", "description", "center", "flushleft", "flushright"}
    for match in re.finditer(r"\\begin\s*\{([^}]+)\}", text):
        environment = match.group(1)
        if environment not in prose:
            end = re.search(r"\\end\s*\{" + re.escape(environment) + r"\}", text[match.end():])
            ranges.append((match.start(), match.end() + end.end() if end else len(text)))
    pos = 0
    while pos < len(text):
        char = text[pos]
        if char == "%":
            end = text.find("\n", pos)
            end = len(text) if end < 0 else end + 1
            ranges.append((pos, end))
            pos = end
        elif char == "$":
            delimiter = "$$" if text.startswith("$$", pos) else "$"
            cursor = pos + len(delimiter)
            while cursor < len(text):
                if text[cursor] == "\\":
                    cursor += 2
                elif text.startswith(delimiter, cursor):
                    cursor += len(delimiter)
                    break
                else:
                    cursor += 1
            ranges.append((pos, cursor))
            pos = cursor
        elif char == "\\":
            if text.startswith((r"\(", r"\["), pos):
                closing = r"\)" if text[pos + 1] == "(" else r"\]"
                end = text.find(closing, pos + 2)
                end = len(text) if end < 0 else end + 2
            elif re.match(r"\\verb\*?(?![A-Za-z])", text[pos:]):
                cursor = pos + 5
                if cursor < len(text) and text[cursor] == "*":
                    cursor += 1
                end = text.find(text[cursor], cursor + 1) + 1 if cursor < len(text) else len(text)
                if end <= 0:
                    end = len(text)
            else:
                command = re.match(r"\\(?:[A-Za-z@]+\*?|.)", text[pos:], re.DOTALL)
                end = pos + len(command.group()) if command else pos + 1
                while end < len(text):
                    # Horizontal whitespace only: do not swallow the next paragraph.
                    cursor = end
                    while cursor < len(text) and text[cursor] in " \t":
                        cursor += 1
                    if cursor < len(text) and text[cursor] in "[{":
                        opening = text[cursor]
                        end = _balanced(text, cursor, opening, "]" if opening == "[" else "}")
                    else:
                        break
            ranges.append((pos, end))
            pos = end
        elif char in "{}&#_^~":
            ranges.append((pos, pos + 1))
            pos += 1
        else:
            pos += 1
    # Preserve whitespace at syntax boundaries: removing the space after a bare
    # command could turn "\\item Text" into the different command "\\itemText".
    expanded = []
    for start, end in ranges:
        while start > 0 and text[start - 1].isspace():
            start -= 1
        while end < len(text) and text[end].isspace():
            end += 1
        expanded.append((start, end))
    return expanded


def _markdown_ranges(text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    if text.startswith("---\n") or text.startswith("---\r\n"):
        end = re.search(r"(?m)^(?:---|\.\.\.)\s*$", text[4:])
        if end:
            ranges.append((0, 4 + end.end()))
    lines = list(re.finditer(r"[^\n]*(?:\n|$)", text))
    fence = None
    for line in lines:
        raw = line.group()
        marker = re.match(r" {0,3}(`{3,}|~{3,})", raw)
        if fence:
            if marker and marker.group(1)[0] == fence[1][0] and len(marker.group(1)) >= len(fence[1]):
                ranges.append((fence[0], line.end()))
                fence = None
            continue
        if marker:
            fence = (line.start(), marker.group(1))
        elif re.match(r" {0,3}(?:#{1,6}\s|>|\[[^]]+\]:)| {4}|\t|\s*\|", raw) or re.match(r"\s*(?:[-*_]\s*){3,}$", raw) or re.match(r"\s*(?:={3,}|-{3,})\s*$", raw):
            ranges.append((line.start(), line.end()))
        elif "|" in raw and raw.count("|") >= 2:
            ranges.append((line.start(), line.end()))
        else:
            prefix = re.match(r"\s*(?:[-+*]|\d+[.)])\s+(?:\[[ xX]\]\s+)?", raw)
            if prefix:
                ranges.append((line.start(), line.start() + prefix.end()))
    if fence:
        ranges.append((fence[0], len(text)))
    patterns = [r"`+[^`\n]*`+", r"!?\[[^\]\n]*\]\([^\n]*?\)", r"!?\[[^\]\n]*\]\[[^\]\n]*\]", r"\[\^[^]]+\]", r"\[@[^]]+\]", r"<[^>\n]+>", r"(\*\*|__|~~)(?=\S)(?:[^\n]*?\S)?\1", r"(?<!\*)\*(?!\*)(?=\S)(?:[^*\n]*?\S)?\*(?!\*)", r"(?<!\w)_(?!_)(?=\S)(?:[^_\n]*?\S)?_(?!\w)", r"\*\*|__|~~|(?<!\w)[*_]|[*_](?!\w)", r" {2,}\r?\n", r"\\[^\w\s]", r"(?<!\\)\$\$[\s\S]*?(?<!\\)\$\$", r"(?<![\\$])\$(?!\$)[^\n$]*?(?<!\\)\$(?!\$)", r"<([A-Za-z][\w:-]*)\b[^>]*>[\s\S]*?</\1\s*>"]
    for pattern in patterns:
        ranges.extend((m.start(), m.end()) for m in re.finditer(pattern, text))
    return ranges


class ProtectedDocument:
    def __init__(self, text: str, format: str):
        self.original, self.format = text, format
        prefix = "RC" + hashlib.sha256(text.encode()).hexdigest()[:8]
        while prefix in text:
            prefix += "X"
        self.prefix = prefix
        self.token_pattern = re.compile(r"\[\[" + prefix + r"_\d{6}\]\]")
        self.protected: dict[str, str] = {}
        ranges = _latex_ranges(text) if format == "latex" else _markdown_ranges(text) if format == "markdown" else []
        for pattern in [r'https?://[^\s<>]+', r'"[^"\n]+"|“[^”]+”|‘[^’\n]+’', r'(?<!\w)[+-]?\d+(?:[.,:/-]\d+)*(?:\s?(?:%|°[CF]|kg|mg|km|cm|mm|ms|GB|MB|USD|EUR|Hz)\b|%)?', r"\[[0-9]+(?:[–, -]+[0-9]+)*\]"]:
            ranges.extend((m.start(), m.end()) for m in re.finditer(pattern, text))
        merged: list[list[int]] = []
        for start, end in sorted(ranges):
            if merged and start < merged[-1][1]:
                merged[-1][1] = max(end, merged[-1][1])
            else:
                merged.append([start, end])
        chunks, cursor = [], 0
        for index, (start, end) in enumerate(merged):
            token = f"[[{prefix}_{index:06d}]]"
            self.protected[token] = text[start:end]
            chunks.extend((text[cursor:start], token))
            cursor = end
        chunks.append(text[cursor:])
        self.masked = "".join(chunks)
        self.blocks: list[Block] = []
        for part in re.split(r"((?:\r?\n[ \t]*){2,})", self.masked):
            if not part:
                continue
            # Preserve whitespace outside each rewrite span, including file endings.
            leading = re.match(r"\s*", part).group()
            if leading:
                self._add(leading, False)
            inner = part[len(leading):]
            trailing = re.search(r"\s*$", inner).group()
            core = inner[:-len(trailing)] if trailing else inner
            while core:
                end = self._split_point(core)
                piece, core = core[:end], core[end:]
                self._add(piece, bool(re.search(r"[^\W\d_]", self.token_pattern.sub("", piece), re.UNICODE)))
                if core.startswith(" "):
                    self._add(" ", False)
                    core = core[1:]
            if trailing:
                self._add(trailing, False)

    def _split_point(self, core: str) -> int:
        """Prefer sentence boundaries and never orphan a tiny mid-sentence tail."""
        limit = 2000
        if len(core) <= limit:
            return len(core)
        for match in self.token_pattern.finditer(core):
            if match.start() < limit < match.end():
                limit = match.end()
                break
        search = core[:limit]
        sentence_ends = [
            match.end()
            for match in re.finditer(r"[.!?][\"')\]]?(?=\s+[A-Z0-9“\"'(])", search)
            if match.end() >= 800
        ]
        if sentence_ends:
            point = sentence_ends[-1]
        else:
            point = search.rfind(" ")
            if point < 800 or any(m.start() < point < m.end() for m in self.token_pattern.finditer(core)):
                point = limit
        if 0 < len(core) - point < 400 and len(core) <= limit + 400:
            return len(core)
        return point

    def _add(self, text: str, editable: bool):
        self.blocks.append(Block(f"b{len(self.blocks):05d}", text, editable))

    def restore(self, text: str) -> str:
        return self.token_pattern.sub(lambda m: self.protected[m.group()], text)

    def semantic_view(self, text: str) -> str:
        """Verify editable prose and facts; exact syntax is checked separately."""
        def replacement(match):
            value = self.protected[match.group()]
            percent = re.match(r"^(\s*)\\%(\s*)", value)
            if percent:
                return percent.group(1) + "%" + percent.group(2)
            if re.match(r'^[+\-]?\d|^["“‘]|^https?://', value):
                return value
            return "\n"
        return self.token_pattern.sub(replacement, text).strip()

    def validate(self, source: str, output: str) -> list[str]:
        errors = []
        before, after = self.token_pattern.findall(source), self.token_pattern.findall(output)
        if before != after:
            errors.append("Protected spans were removed, duplicated, moved, or changed.")
        if self.prefix in self.token_pattern.sub("", output):
            errors.append("A malformed protected-span marker was introduced.")
        if self.format == "latex" and re.search(r"[\\{}$%&#_^~]", self.token_pattern.sub("", output)):
            errors.append("New LaTeX syntax was introduced outside protected spans.")
        if self.format == "markdown":
            structural = r"[*_`#\[\]<>|]"
            if re.findall(structural, self.token_pattern.sub("", source)) != re.findall(structural, self.token_pattern.sub("", output)):
                errors.append("Markdown syntax outside protected spans changed.")
        if re.findall(r"\d+", self.token_pattern.sub("", source)) != re.findall(r"\d+", self.token_pattern.sub("", output)):
            errors.append("Unprotected numeric content was introduced or changed.")
        if source.strip() and not output.strip():
            errors.append("The rewrite is empty.")
        return errors

    def assemble(self, replacements: dict[str, str]) -> str:
        pieces = []
        for block in self.blocks:
            changed = replacements.get(block.id, block.text)
            if self.validate(block.text, changed):
                raise ValueError(f"Invalid replacement for {block.id}")
            pieces.append(changed)
        return self.restore("".join(pieces))


def qualification_findings(source: str, output: str, block_id: str) -> list[dict]:
    findings = []
    uncertain = r"\b(?:may|might|could|appears?|seems?|possibly|possible|potentially|preliminary|suggests?|estimated?|approximately)\b"
    negative = r"\b(?:not|never|cannot|no)\b|n['’]t\b"
    for pattern, label in [(uncertain, "qualification"), (negative, "negation")]:
        before = len(re.findall(pattern, source, re.I))
        after = len(re.findall(pattern, output, re.I))
        if (label == "qualification" and before > 0 and after == 0) or (label == "negation" and before != after):
            findings.append({"block_id": block_id, "type": label, "severity": "warning", "explanation": f"A deterministic {label} check found a possible change; review the meaning.", "source_excerpt": source, "output_excerpt": output})
    return findings
