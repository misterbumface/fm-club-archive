"""Turn AI display prose into predictable, readable profile sections.

The writer is deliberately allowed to choose prose, but it is not allowed to
choose whether the page becomes a wall of text. This formatter recognizes the
small set of career-history headings and breaks oversized prose into readable
chunks without changing a word of the generated narrative.
"""

from __future__ import annotations

import re
from typing import Any

_KNOWN_HEADINGS = {
    "career overview": "Career overview",
    "overview": "Career overview",
    "playing career": "Playing career",
    "club career": "Playing career",
    "international career": "International career",
    "managerial career": "Managerial career",
    "coaching career": "Managerial career",
    "honours and awards": "Honours and awards",
    "honors and awards": "Honours and awards",
    "player honours": "Player honours",
    "player honors": "Player honours",
    "managerial honours": "Managerial honours",
    "managerial honors": "Managerial honours",
}
_SENTENCE_BREAK_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Þ])")


def format_player_narrative(narrative: str | None) -> list[dict[str, Any]]:
    """Return sections with headings and paragraphs suitable for the template."""
    if not narrative or not narrative.strip():
        return []

    cleaned = re.sub(r"(?m)^#{1,6}\s*", "", narrative).replace("**", "")
    blocks = [block.strip() for block in re.split(r"\n\s*\n", cleaned) if block.strip()]
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] = {"heading": None, "paragraphs": []}

    for block in blocks:
        block_lines = [line.strip() for line in block.splitlines() if line.strip()]
        heading = _recognized_heading(block_lines[0]) if block_lines else None
        if heading:
            if current["paragraphs"] or current["heading"]:
                sections.append(current)
            current = {"heading": heading, "paragraphs": []}
            remainder = " ".join(block_lines[1:]).strip()
            if remainder:
                current["paragraphs"].extend(_split_oversized_paragraph(remainder))
            continue
        current["paragraphs"].extend(_split_oversized_paragraph(block))

    if current["paragraphs"] or current["heading"]:
        sections.append(current)
    return [section for section in sections if section["paragraphs"]]


def _recognized_heading(block: str) -> str | None:
    if "\n" in block or len(block.split()) > 5:
        return None
    normalized = re.sub(r"[^a-z ]", "", block.casefold()).strip()
    known = _KNOWN_HEADINGS.get(normalized)
    if known:
        return known
    # The universe-aware writer is encouraged to choose headings specific to
    # the player rather than repeat a fixed template. Preserve a short,
    # standalone, punctuation-free heading verbatim.
    stripped = block.strip()
    if (
        1 <= len(stripped.split()) <= 5
        and not re.search(r"[.!?,:;]$", stripped)
        and stripped[0].isupper()
    ):
        return stripped
    return None


def _split_oversized_paragraph(text: str, max_words: int = 90) -> list[str]:
    if len(text.split()) <= max_words:
        return [text]

    sentences = _SENTENCE_BREAK_RE.split(text)
    pieces: list[str] = []
    for sentence in sentences:
        if len(sentence.split()) <= max_words:
            pieces.append(sentence)
        else:
            pieces.extend(_split_long_list_sentence(sentence, max_words=max_words))

    paragraphs: list[str] = []
    current: list[str] = []
    current_words = 0
    for piece in pieces:
        piece_words = len(piece.split())
        if current and current_words + piece_words > max_words:
            paragraphs.append(" ".join(current).strip())
            current = []
            current_words = 0
        current.append(piece)
        current_words += piece_words
    if current:
        paragraphs.append(" ".join(current).strip())
    return paragraphs


def _split_long_list_sentence(sentence: str, max_words: int) -> list[str]:
    """Split comma-heavy FM award inventories while preserving their wording."""
    clauses = re.split(
        r"(?<=\),)\s+(?=[A-ZÀ-ÖØ-Þ])|"
        r"(?<=\))\s+(?=and\s+[A-ZÀ-ÖØ-Þ])|"
        r"(?<=,)\s+(?=(?:named|won|shortlisted|runner-up|[A-ZÀ-ÖØ-Þ]))",
        sentence,
        flags=re.IGNORECASE,
    )
    if len(clauses) == 1:
        return [sentence]

    chunks: list[str] = []
    current: list[str] = []
    current_words = 0
    target_words = min(65, max_words)
    for clause in clauses:
        clause = clause.strip()
        if not clause:
            continue
        clause_words = len(clause.split())
        if current and current_words + clause_words > target_words:
            chunks.append(" ".join(current).strip())
            current = []
            current_words = 0
        current.append(clause)
        current_words += clause_words
    if current:
        chunks.append(" ".join(current).strip())
    return chunks
