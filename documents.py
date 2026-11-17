"""Turns downloaded documents into readable text, and checks they're the right ones."""

from __future__ import annotations

import re
from html.parser import HTMLParser

# Tags that start a new line when the HTML is turned into text.
BLOCK_TAGS = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "title"}
CELL_TAGS = {"td", "th"}
SKIP_TAGS = {"script", "style", "head"}


class _TextMaker(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skipping = 0
        self.in_cell = 0  # inside a table cell, paragraphs don't start new lines
        self.in_pre = 0  # inside <pre>, line breaks in the source are real

    def handle_starttag(self, tag, attrs):
        if tag == "pre":
            self.in_pre += 1
        if tag in SKIP_TAGS:
            self.skipping += 1
        elif tag in CELL_TAGS:
            self.in_cell += 1
            self.parts.append("\t")
        elif tag in BLOCK_TAGS:
            self.parts.append(" " if self.in_cell and tag != "tr" else "\n")

    def handle_endtag(self, tag):
        if tag == "pre":
            self.in_pre = max(0, self.in_pre - 1)
        if tag in SKIP_TAGS and self.skipping:
            self.skipping -= 1
        elif tag in CELL_TAGS:
            self.in_cell = max(0, self.in_cell - 1)
        elif tag == "tr":
            self.in_cell = 0
            self.parts.append("\n")
        elif tag in BLOCK_TAGS:
            self.parts.append(" " if self.in_cell else "\n")

    def handle_data(self, data):
        if self.skipping:
            return
        if not self.in_pre:
            # In HTML, a line break in the source is just a space; only tags make new lines.
            data = data.replace("\r", " ").replace("\n", " ")
        self.parts.append(data)


def html_to_text(html: str) -> str:
    """Readable text from HTML: one line per paragraph or table row, cells split by tabs.

    A document with no HTML tags at all (some older exhibits are plain text) keeps its lines.
    """
    if re.search(r"<[a-zA-Z]", html):
        maker = _TextMaker()
        maker.feed(html)
        maker.close()
        text = "".join(maker.parts)
    else:
        text = html
    text = text.replace("\xa0", " ")
    lines = []
    for line in text.split("\n"):
        cells = [" ".join(c.split()) for c in line.split("\t")]
        cleaned = "\t".join(c for c in cells if c)
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def press_release_from_filing(full_filing: str) -> tuple[str, str] | None:
    """Find the press release (exhibit 99.1, or the first exhibit 99) in a filing's
    complete text file. Returns (exhibit type, its HTML), or None if there isn't one."""
    found = []
    for doc in full_filing.split("<DOCUMENT>")[1:]:
        kind = re.search(r"<TYPE>([^\s<]+)", doc)
        body = re.search(r"<TEXT>(.*?)</TEXT>", doc, re.S)
        if kind and body and kind.group(1).upper().startswith("EX-99"):
            found.append((kind.group(1).upper(), body.group(1)))
    if not found:
        return None
    for kind, body in found:
        if kind in ("EX-99.1", "EX-99.01"):
            return kind, body
    return found[0]


def earnings_headline_score(text: str) -> int:
    """How much a press release's opening looks like a quarterly earnings release.

    "Oracle Announces Fiscal 2022 Fourth Quarter and Full Year Financial Results"
    scores 3; a release that only mentions a quarter in passing scores 1.
    """
    opening = text[:600].lower()
    score = 0
    if re.search(r"\b(announces|reports)\b", opening):
        score += 1
    if "results" in opening:
        score += 1
    if "quarter" in opening:
        score += 1
    return score


def transcript_text(turns: list[dict]) -> str:
    """One paragraph per speaker turn: 'Name (Title): what they said'."""
    paragraphs = []
    for t in turns:
        who = t.get("speaker") or "Unknown"
        title = t.get("title")
        label = f"{who} ({title})" if title and title != who else who
        paragraphs.append(f"{label}: {t.get('content', '').strip()}")
    return "\n\n".join(paragraphs)


QUARTER_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4}


def transcript_label(turns: list[dict]) -> tuple[int | None, int | None]:
    """The (year, quarter) the call announces in its opening lines, if it says.

    Operators usually open with e.g. "the Microsoft Fiscal Year 2024 First Quarter
    Earnings Conference Call" or "Amazon.com Q1 2020 financial results".
    """
    opening = " ".join(t.get("content", "") for t in turns[:3])[:1500]
    quarter = None
    m = re.search(r"\b(first|second|third|fourth)\s+quarter\b", opening, re.I)
    if m:
        quarter = QUARTER_WORDS[m.group(1).lower()]
    else:
        m = re.search(r"\bQ([1-4])\b", opening)
        if m:
            quarter = int(m.group(1))
    year = None
    m = re.search(r"\b(?:fiscal\s+(?:year\s+)?)(20\d\d)\b|\bFY\s?(20\d\d)\b", opening, re.I)
    if m:
        year = int(m.group(1) or m.group(2))
    else:
        m = re.search(r"\b(20\d\d)\b", opening)
        if m:
            year = int(m.group(1))
    return year, quarter
