"""Unicode-aware normalisation and tokenisation.

Python's ``\\w`` treats Indic vowel signs and viramas (categories Mc/Mn) as
non-word characters, which splits "மாத்ரு" into "ம", "த", "ர". Tokens here are runs
of letters (L*), combining marks (M*) and digits (N*).
"""

from __future__ import annotations

import unicodedata

_TOKEN_CATEGORIES = frozenset("LMN")

ENGLISH_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "could",
        "do",
        "does",
        "for",
        "from",
        "have",
        "how",
        "i",
        "if",
        "in",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "or",
        "please",
        "should",
        "tell",
        "the",
        "this",
        "to",
        "was",
        "what",
        "when",
        "where",
        "which",
        "who",
        "will",
        "with",
        "would",
        "you",
        "your",
        "about",
        "any",
        "there",
        "their",
        "them",
        "they",
        "get",
        "got",
        "need",
        "want",
    }
)


def normalise(text: str) -> str:
    """NFC-normalise and casefold."""
    return unicodedata.normalize("NFC", text).casefold()


def tokenise(text: str) -> list[str]:
    """Split normalised text into word tokens (keeps Indic words whole)."""
    tokens: list[str] = []
    current: list[str] = []
    for char in normalise(text):
        if unicodedata.category(char)[0] in _TOKEN_CATEGORIES:
            current.append(char)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tokens


def is_ascii(token: str) -> bool:
    return token.isascii()


def significant_tokens(text: str) -> list[str]:
    """Tokens worth matching: drops single characters and English stopwords."""
    return [
        t for t in tokenise(text) if len(t) > 1 and not (is_ascii(t) and t in ENGLISH_STOPWORDS)
    ]


def contains_phrase(haystack_norm: str, haystack_tokens: list[str], phrase: str) -> bool:
    """Phrase match. ASCII phrases must align with token boundaries (so "jsy" does not
    match inside another word). Non-ASCII phrases match as substrings so inflected
    forms ("வந்தனாவுக்கு") still match the alias ("வந்தனா")."""
    phrase_norm = normalise(phrase).strip()
    if not phrase_norm:
        return False
    if phrase_norm.isascii():
        phrase_tokens = tokenise(phrase_norm)
        if not phrase_tokens:
            return False
        n = len(phrase_tokens)
        return any(
            haystack_tokens[i : i + n] == phrase_tokens for i in range(len(haystack_tokens) - n + 1)
        )
    return phrase_norm in haystack_norm
