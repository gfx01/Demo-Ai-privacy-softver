"""Text clean-up shared by all extractors."""

from __future__ import annotations

import re
import unicodedata
from html.parser import HTMLParser

# Fonts without ő/ű, and OCR models trained on other Latin languages, put
# these look-alikes in Hungarian text ("Kõmûves"). The name and place lists
# match exact letters, so a missed fix would let a name through unmasked.
HU_LOOKALIKES = str.maketrans({"õ": "ő", "ô": "ő", "Õ": "Ő", "Ô": "Ő", "û": "ű", "ũ": "ű", "Û": "Ű", "Ũ": "Ű"})

_BLOCK_TAGS = {
    "p", "div", "br", "li", "ul", "ol", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "header", "footer", "blockquote", "pre", "hr", "caption", "dd", "dt",
}
_CELL_TAGS = {"td", "th"}
_SKIP_TAGS = {"script", "style", "head", "title"}


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):  # noqa: ARG002
        if tag in _SKIP_TAGS:
            self.skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")
        elif tag in _CELL_TAGS:
            self.parts.append("\t")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextParser()
    parser.feed(html)
    parser.close()
    return tidy("".join(parser.parts))


def tidy(text: str) -> str:
    """NFC, unified line ends, no trailing spaces, at most one empty line.
    Linear time: a regex over long whitespace runs would backtrack."""
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    text = "\n".join(line.rstrip(" \t") for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fix_hungarian_accents(text: str) -> str:
    return text.translate(HU_LOOKALIKES)
