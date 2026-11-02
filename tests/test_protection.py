import unittest

from recast.protection import ProtectedDocument, qualification_findings


LATEX = r"""\documentclass{article}
\newcommand{\thing}[1]{\textbf{#1}}
\begin{document}
\section{Results}
We checked 24 cases; they may improve by 12\% \cite[p.~2]{smith2025}.

We did not establish causation. Here is $x_1 + y^2$ and \(a_b\).
% Keep this comment and its line ending.
\begin{equation}a &= b + 12\end{equation}
\begin{itemize}
\item A careful observation with \emph{exact macro arguments}.
\end{itemize}
\verb|a%b| and \url{https://example.org/a_b} stay intact.
\begin{custom}Special content is opaque.\end{custom}
\end{document}
Ignored trailing material.
"""
MARKDOWN = '# Results\r\n\r\nWe checked 24 cases and may improve by 12%. See [Smith](https://example.org).\r\n\r\n```python\r\nvalue = 24\r\n```\r\n\r\n> A quoted block.\r\n\r\n| A | B |\r\n|---|---|\r\n| 1 | 2 |\r\n\r\nA **careful** result with `code` and $x_1$ [@smith].\r\n'


class ProtectionTests(unittest.TestCase):
    def test_roundtrip_for_all_formats_and_crlf(self):
        for text, fmt in [(LATEX, "latex"), (MARKDOWN, "markdown"), ('We checked 24 cases. “May improve.”\r\n\r\nEnd.\r\n', "text")]:
            with self.subTest(fmt=fmt):
                protected = ProtectedDocument(text, fmt)
                self.assertEqual(protected.assemble({}), text)

    def test_latex_prose_changes_and_syntax_survives(self):
        protected = ProtectedDocument(LATEX, "latex")
        self.assertIn("12%", protected.semantic_view(protected.masked))
        replacements = {block.id: block.text.replace("We checked", "We reviewed") for block in protected.blocks if block.editable}
        output = protected.assemble(replacements)
        self.assertEqual(output, LATEX.replace("We checked", "We reviewed"))
        for original in [r"\cite[p.~2]{smith2025}", "$x_1 + y^2$", "Special content is opaque.", "Ignored trailing material."]:
            self.assertIn(original, output)

    def test_markers_reject_loss_reorder_and_duplication(self):
        protected = ProtectedDocument("There were 24 cases in 2025.", "text")
        block = next(b for b in protected.blocks if b.editable)
        markers = protected.token_pattern.findall(block.text)
        for output in [block.text.replace(markers[0], ""), block.text + markers[0], block.text.replace(markers[0], "TEMP").replace(markers[1], markers[0]).replace("TEMP", markers[1])]:
            self.assertTrue(protected.validate(block.text, output))

    def test_new_latex_commands_are_rejected(self):
        protected = ProtectedDocument("A careful result.", "latex")
        self.assertTrue(protected.validate(protected.masked, r"A careful result. \input{secret}"))

    def test_latex_command_and_word_boundaries_are_protected(self):
        text = r"\item A result with \emph{exact content} here."
        protected = ProtectedDocument(text, "latex")
        item = next(value for value in protected.protected.values() if r"\item" in value)
        emphasized = next(value for value in protected.protected.values() if r"\emph" in value)
        self.assertTrue(item.endswith(" "))
        self.assertTrue(emphasized.startswith(" ") and emphasized.endswith(" "))

    def test_qualification_and_negation_drift(self):
        self.assertTrue(qualification_findings("The result may improve.", "The result will improve.", "b0"))
        self.assertTrue(qualification_findings("We did not establish causation.", "We established causation.", "b0"))
        self.assertFalse(qualification_findings("It may improve.", "It could improve.", "b0"))

    def test_very_long_prose_is_split_without_splitting_markers(self):
        text = ("A result for 123 cases with careful wording. " * 300) + "\n"
        protected = ProtectedDocument(text, "text")
        self.assertGreater(len(protected.blocks), 2)
        self.assertEqual(protected.assemble({}), text)

    def test_long_prose_splits_at_sentences_without_orphan_tails(self):
        sentences = [f"Sentence number {i} discusses result {i} in careful detail." for i in range(40)]
        text = "# A long abstract\n\n" + " ".join(sentences) + "\n"
        protected = ProtectedDocument(text, "markdown")
        editable = [block for block in protected.blocks if block.editable]
        self.assertGreater(len(editable), 1)
        for block in editable:
            self.assertGreaterEqual(len(block.text), 300)
            self.assertRegex(block.text, r"[.!?][\"')\]]?$")
        self.assertEqual(protected.assemble({}), text)

    def test_html_and_display_math_are_opaque_in_markdown(self):
        text = '<script>keep_this();</script>\n\n$$x_1 + 2$$\n\nSome prose.'
        protected = ProtectedDocument(text, "markdown")
        editable = " ".join(b.text for b in protected.blocks if b.editable)
        self.assertNotIn("keep_this", editable)
        self.assertNotIn("x_1", editable)

    def test_markdown_emphasis_remains_whole(self):
        protected = ProtectedDocument("A **careful** result with *a* short _word_ and ~~old~~ text.", "markdown")
        for span in ["**careful**", "*a*", "_word_", "~~old~~"]:
            self.assertIn(span, protected.protected.values())
        self.assertTrue(protected.validate(protected.masked, protected.masked + " **new**"))

    def test_new_numeric_facts_are_rejected(self):
        protected = ProtectedDocument("We checked 24 cases.", "text")
        self.assertTrue(protected.validate(protected.masked, protected.masked + " In 2026."))
