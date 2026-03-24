from __future__ import annotations

import unittest

from book_crawler.search_ranker import is_supported_search_language, score_search_result


class SearchRankerTests(unittest.TestCase):
    def test_exact_title_author_match_beats_dictionary_page(self) -> None:
        strong_score, strong_reasons = score_search_result(
            "Think Python",
            "Downey",
            "Think Python 2e Open Access PDF",
            "https://greenteapress.com/wp/think-python-2e/",
            "Think Python by Allen Downey available as an open access PDF textbook.",
            "greenteapress.com",
        )
        noisy_score, noisy_reasons = score_search_result(
            "Think Python",
            "Downey",
            "Traduction : think - Dictionnaire anglais-francais Larousse",
            "https://www.larousse.fr/dictionnaires/anglais-francais/think/618086",
            "think - traduction anglais-francais",
            "www.larousse.fr",
        )

        self.assertGreater(strong_score, noisy_score)
        self.assertIn("exact_title_match", strong_reasons)
        self.assertIn("author_context_match", strong_reasons)
        self.assertIn("dictionary_domain", noisy_reasons)

    def test_pdf_and_open_access_signals_raise_score(self) -> None:
        boosted_score, boosted_reasons = score_search_result(
            "Database System Concepts",
            "Silberschatz",
            "Database System Concepts PDF",
            "https://example.edu/database-system-concepts.pdf",
            "Open access ebook download",
            "example.edu",
        )
        plain_score, plain_reasons = score_search_result(
            "Database System Concepts",
            "Silberschatz",
            "Database System Concepts",
            "https://example.edu/database-system-concepts",
            "Course page",
            "example.edu",
        )

        self.assertGreater(boosted_score, plain_score)
        self.assertIn("pdf_signal", boosted_reasons)
        self.assertIn("open_access_signal", boosted_reasons)
        self.assertNotIn("pdf_signal", plain_reasons)

    def test_forum_domain_is_penalized(self) -> None:
        forum_score, forum_reasons = score_search_result(
            "Think Python",
            "Downey",
            "Think Python discussion thread",
            "https://reddit.com/r/python/comments/example",
            "Forum discussion about the book",
            "reddit.com",
        )
        clean_score, clean_reasons = score_search_result(
            "Think Python",
            "Downey",
            "Think Python by Allen Downey",
            "https://allendowney.com/books/think-python.html",
            "Book homepage and download links",
            "allendowney.com",
        )

        self.assertLess(forum_score, clean_score)
        self.assertIn("forum_domain", forum_reasons)
        self.assertNotIn("forum_domain", clean_reasons)

    def test_language_filter_allows_english_and_korean_only(self) -> None:
        allowed, reason = is_supported_search_language(
            "Think Python 입문서",
            "Allen Downey 책 소개와 PDF 링크",
        )
        blocked, blocked_reason = is_supported_search_language(
            "think和think of的区别 - 百度知道",
            "think of与think about区别如下",
        )

        self.assertTrue(allowed)
        self.assertEqual(reason, "en_or_ko_only")
        self.assertFalse(blocked)
        self.assertEqual(blocked_reason, "unsupported_language_chars")


if __name__ == "__main__":
    unittest.main()
