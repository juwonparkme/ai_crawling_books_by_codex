from __future__ import annotations

import re
import urllib.parse
from typing import List, Tuple


STOPWORDS = {
    "a",
    "an",
    "and",
    "at",
    "by",
    "de",
    "for",
    "from",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}

NOISY_DOMAIN_PENALTIES = (
    ("larousse", 70, "dictionary_domain"),
    ("wiktionary", 70, "dictionary_domain"),
    ("dictionary", 70, "dictionary_domain"),
    ("reddit.com", 45, "forum_domain"),
    ("quora.com", 45, "forum_domain"),
    ("zhihu.com", 45, "forum_domain"),
    ("stackoverflow.com", 45, "forum_domain"),
    ("stackexchange.com", 45, "forum_domain"),
    ("forum", 40, "forum_domain"),
    ("community", 35, "community_domain"),
    ("tistory.com", 30, "blog_domain"),
    ("medium.com", 30, "blog_domain"),
)

NOISY_TEXT_PENALTIES = (
    ("dictionary", 25, "dictionary_text"),
    ("dictionnaire", 25, "dictionary_text"),
    ("translation", 25, "translation_text"),
    ("traduction", 25, "translation_text"),
    ("forum", 20, "forum_text"),
    ("discussion", 20, "forum_text"),
    ("question", 18, "qa_text"),
    ("뜻", 18, "dictionary_text"),
    ("번역", 18, "translation_text"),
    ("conjugaison", 18, "dictionary_text"),
)

POSITIVE_SIGNAL_BONUSES = (
    ("pdf", 18, "pdf_signal"),
    ("ebook", 12, "ebook_signal"),
    ("e book", 12, "ebook_signal"),
    ("open access", 14, "open_access_signal"),
    ("free access", 10, "free_access_signal"),
    ("textbook", 8, "textbook_signal"),
    ("download", 6, "download_signal"),
)


def score_search_result(
    book_title: str,
    book_author: str | None,
    result_title: str,
    result_url: str,
    result_snippet: str,
    domain: str,
) -> Tuple[int, List[str]]:
    normalized_book_title = _normalize_text(book_title)
    normalized_book_author = _normalize_text(book_author or "")
    normalized_result_title = _normalize_text(result_title)
    normalized_result_snippet = _normalize_text(result_snippet)
    normalized_result_url = _normalize_text(urllib.parse.unquote(result_url))
    combined = " ".join(
        part
        for part in (normalized_result_title, normalized_result_snippet, normalized_result_url)
        if part
    )

    score = 0
    reasons: List[str] = []

    title_tokens = _significant_tokens(book_title)
    combined_tokens = set(_significant_tokens(combined))
    title_token_matches = len(title_tokens & combined_tokens)

    if normalized_book_title and normalized_book_title in normalized_result_title:
        score += 90
        reasons.append("exact_title_match")
    elif normalized_book_title and normalized_book_title in combined:
        score += 55
        reasons.append("exact_title_context_match")
    elif title_token_matches:
        partial_score = title_token_matches * 12
        if title_tokens and title_token_matches == len(title_tokens):
            partial_score += 18
            reasons.append("all_title_tokens_match")
        else:
            reasons.append(f"partial_title_tokens:{title_token_matches}")
        score += partial_score
    else:
        score -= 35
        reasons.append("missing_title_match")

    author_tokens = _significant_tokens(book_author or "")
    author_token_matches = len(author_tokens & combined_tokens)
    if normalized_book_author and normalized_book_author in normalized_result_title:
        score += 35
        reasons.append("exact_author_match")
    elif normalized_book_author and normalized_book_author in combined:
        score += 22
        reasons.append("author_context_match")
    elif author_token_matches:
        score += author_token_matches * 10
        reasons.append(f"partial_author_tokens:{author_token_matches}")
    elif normalized_book_author:
        score -= 10
        reasons.append("missing_author_match")

    score += _apply_positive_signal_bonuses(combined, reasons)
    score -= _apply_noisy_domain_penalties(domain.lower(), reasons)
    score -= _apply_noisy_text_penalties(combined, reasons)

    return score, reasons


def is_supported_search_language(result_title: str, result_snippet: str) -> Tuple[bool, str]:
    text = f"{result_title} {result_snippet}"
    supported_letters = 0
    unsupported_letters = 0

    for char in text:
        if _is_english_letter(char) or _is_hangul(char):
            supported_letters += 1
        elif char.isalpha():
            unsupported_letters += 1

    if supported_letters == 0:
        return False, "missing_en_ko_text"
    if unsupported_letters > 0:
        return False, "unsupported_language_chars"
    return True, "en_or_ko_only"


def _normalize_text(text: str) -> str:
    normalized = re.sub(r"[_\W]+", " ", (text or "").casefold(), flags=re.UNICODE)
    return " ".join(normalized.split())


def _significant_tokens(text: str) -> set[str]:
    tokens = _normalize_text(text).split()
    return {token for token in tokens if len(token) > 1 and token not in STOPWORDS}


def _apply_positive_signal_bonuses(text: str, reasons: List[str]) -> int:
    bonus = 0
    for marker, value, reason in POSITIVE_SIGNAL_BONUSES:
        if marker in text:
            bonus += value
            reasons.append(reason)
    return min(bonus, 36)


def _apply_noisy_domain_penalties(domain: str, reasons: List[str]) -> int:
    penalty = 0
    for marker, value, reason in NOISY_DOMAIN_PENALTIES:
        if marker in domain:
            penalty += value
            reasons.append(reason)
    return min(penalty, 90)


def _apply_noisy_text_penalties(text: str, reasons: List[str]) -> int:
    penalty = 0
    for marker, value, reason in NOISY_TEXT_PENALTIES:
        if marker in text:
            penalty += value
            reasons.append(reason)
    return min(penalty, 60)


def _is_english_letter(char: str) -> bool:
    return ("a" <= char <= "z") or ("A" <= char <= "Z")


def _is_hangul(char: str) -> bool:
    code = ord(char)
    return (
        0x1100 <= code <= 0x11FF
        or 0x3130 <= code <= 0x318F
        or 0xAC00 <= code <= 0xD7A3
    )
