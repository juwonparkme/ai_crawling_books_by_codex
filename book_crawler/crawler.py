from __future__ import annotations

# pyright: ignore[reportMissingImports]

import base64
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import List

import importlib
import re

from .config import CrawlerConfig
from .license_detector import decision_for, merge_text_parts
from .search_ranker import is_supported_search_language, score_search_result


class SearchEngineBlockedError(RuntimeError):
    """Raised when the search engine blocks automated access with an interstitial page."""


@dataclass
class SearchResult:
    rank: int
    title: str
    url: str
    domain: str
    snippet: str
    relevance_score: int = 0
    relevance_reasons: List[str] = field(default_factory=list)


def build_search_url(query: str, lang: str) -> str:
    params = {"q": query, "count": 10, "setlang": _bing_lang(lang)}
    return "https://www.bing.com/search?" + urllib.parse.urlencode(params)


def create_driver(config: CrawlerConfig):
    webdriver = importlib.import_module("selenium.webdriver")
    options_module = importlib.import_module("selenium.webdriver.chrome.options")
    Options = options_module.Options

    options = Options()
    if config.headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    try:
        manager_module = importlib.import_module("webdriver_manager.chrome")
        service_module = importlib.import_module("selenium.webdriver.chrome.service")
        service = service_module.Service(manager_module.ChromeDriverManager().install())
        return webdriver.Chrome(service=service, options=options)
    except Exception:
        return webdriver.Chrome(options=options)


def _random_delay(config: CrawlerConfig) -> None:
    delay = config.delay_min
    if config.delay_max > config.delay_min:
        delay = config.delay_min + (config.delay_max - config.delay_min) * 0.5
    time.sleep(delay)


def _page_text(driver) -> str:
    try:
        return (driver.page_source or "").lower()
    except Exception:
        return ""


def _bing_lang(lang: str) -> str:
    normalized = (lang or "").strip().lower().replace("_", "-")
    if normalized == "ko":
        return "ko-kr"
    if normalized == "en":
        return "en-us"
    return normalized or "ko-kr"


def _search_block_reason(driver) -> str | None:
    current_url = (getattr(driver, "current_url", "") or "").lower()
    if "google.com/sorry/" in current_url:
        return "google_sorry"

    text = _page_text(driver)
    markers = (
        "g-recaptcha",
        "captcha-form",
        "unusual traffic",
        "our systems have detected unusual traffic",
        "verify you are human",
        "please solve this challenge",
        "비정상적인 트래픽",
        "로봇이 아닙니다",
        "보안문자",
    )
    if any(marker in text for marker in markers):
        return "search_challenge"
    return None


def _has_search_results(driver) -> bool:
    by_module = importlib.import_module("selenium.webdriver.common.by")
    By = by_module.By
    selectors = (
        "li.b_algo",
        "#b_results .b_algo",
    )
    for selector in selectors:
        try:
            if driver.find_elements(By.CSS_SELECTOR, selector):
                return True
        except Exception:
            continue
    return False


def _has_no_results(driver) -> bool:
    by_module = importlib.import_module("selenium.webdriver.common.by")
    By = by_module.By
    selectors = (
        ".b_no",
        "#b_results .b_no",
    )
    for selector in selectors:
        try:
            if driver.find_elements(By.CSS_SELECTOR, selector):
                return True
        except Exception:
            continue

    text = _page_text(driver)
    markers = (
        "there are no results for",
        "do not contain the terms",
        "검색 결과가 없습니다",
        "포함한 결과 없음",
    )
    return any(marker in text for marker in markers)


def collect_search_results(driver, config: CrawlerConfig, query: str) -> List[SearchResult]:
    by_module = importlib.import_module("selenium.webdriver.common.by")
    ec_module = importlib.import_module("selenium.webdriver.support.expected_conditions")
    ui_module = importlib.import_module("selenium.webdriver.support.ui")
    exc_module = importlib.import_module("selenium.common.exceptions")
    By = by_module.By
    EC = ec_module
    WebDriverWait = ui_module.WebDriverWait
    TimeoutException = exc_module.TimeoutException

    url = build_search_url(query, config.lang)
    driver.get(url)
    WebDriverWait(driver, config.timeout).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )

    block_reason = _search_block_reason(driver)
    if block_reason:
        raise SearchEngineBlockedError(f"{block_reason}: {driver.current_url}")

    try:
        WebDriverWait(driver, config.timeout).until(
            lambda current_driver: (
                _search_block_reason(current_driver)
                or _has_search_results(current_driver)
                or _has_no_results(current_driver)
            )
        )
    except TimeoutException as exc:
        block_reason = _search_block_reason(driver)
        if block_reason:
            raise SearchEngineBlockedError(f"{block_reason}: {driver.current_url}") from exc
        return []

    if _has_no_results(driver):
        return []

    blocks = driver.find_elements(By.CSS_SELECTOR, "li.b_algo")
    results: List[SearchResult] = []

    for block in blocks:
        try:
            link_el = block.find_element(By.CSS_SELECTOR, "h2 a")
            title = _element_text(link_el)
            url = _extract_result_url(link_el.get_attribute("href") or "")
            if not title or not url:
                continue

            snippet = ""
            snippet_els = block.find_elements(By.CSS_SELECTOR, ".b_caption p")
            if snippet_els:
                snippet = _element_text(snippet_els[0])

            allowed_language, language_reason = is_supported_search_language(title, snippet)
            if not allowed_language:
                continue

            domain = urllib.parse.urlparse(url).netloc
            results.append(
                SearchResult(
                    rank=len(results) + 1,
                    title=title,
                    url=url,
                    domain=domain,
                    snippet=snippet,
                )
            )
        except Exception:
            continue

    for result in results:
        result.relevance_score, result.relevance_reasons = score_search_result(
            config.title,
            config.author,
            result.title,
            result.url,
            result.snippet,
            result.domain,
        )

    results.sort(key=lambda item: (-item.relevance_score, item.rank))
    for index, result in enumerate(results, start=1):
        result.rank = index
    return results


def _element_text(element) -> str:
    for value in (
        element.text,
        element.get_attribute("textContent"),
        element.get_attribute("innerText"),
    ):
        if value:
            return value.strip()
    return ""


def _extract_result_url(url: str) -> str:
    if not url:
        return ""

    parsed = urllib.parse.urlparse(url)
    if parsed.netloc.lower().endswith("bing.com") and parsed.path.startswith("/ck/a"):
        params = urllib.parse.parse_qs(parsed.query)
        encoded = params.get("u", [None])[0]
        decoded = _decode_bing_redirect(encoded)
        if decoded:
            return decoded

    return url


def _decode_bing_redirect(value: str | None) -> str | None:
    if not value or len(value) < 3:
        return None

    if value.startswith("a1"):
        payload = value[2:]
        padding = "=" * (-len(payload) % 4)
        try:
            return base64.b64decode(payload + padding).decode("utf-8")
        except Exception:
            return None

    return None


def _collect_page_text(driver) -> str:
    by_module = importlib.import_module("selenium.webdriver.common.by")
    By = by_module.By

    parts: List[str] = []
    try:
        parts.append(driver.title or "")
    except Exception:
        pass

    for selector in ("meta[name='description']", "meta[property='og:description']"):
        try:
            meta = driver.find_element(By.CSS_SELECTOR, selector)
            content = meta.get_attribute("content")
            if content:
                parts.append(content)
        except Exception:
            continue

    try:
        body = driver.find_element(By.TAG_NAME, "body")
        parts.append(body.text)
    except Exception:
        pass

    return merge_text_parts(parts)


def _extract_metadata(text: str, fallback_title: str) -> dict:
    normalized = re.sub(r"\s+", " ", text)
    lower = normalized.lower()

    title = fallback_title or None
    author = None
    publisher = None
    year = None
    isbn = None

    author_match = re.search(r"\bby\s+([A-Z][A-Za-z.'\-\s]{2,60})", normalized)
    if author_match:
        author = author_match.group(1).strip()

    publisher_match = re.search(
        r"\b(?:published by|publisher|출판사)\s*[:\-]?\s*([A-Za-z0-9.&'\-\s]{2,80})",
        normalized,
        re.IGNORECASE,
    )
    if publisher_match:
        publisher = publisher_match.group(1).strip()

    year_match = re.search(r"\b(19\d{2}|20\d{2})\b", normalized)
    if year_match:
        year = int(year_match.group(1))

    isbn_match = re.search(
        r"\b(?:ISBN(?:-1[03])?:?\s*)?((97[89][\-\s]?\d{1,5}[\-\s]?\d{1,7}"
        r"[\-\s]?\d{1,7}[\-\s]?\d)|([0-9][\-\s]?\d{1,5}[\-\s]?\d{1,7}"
        r"[\-\s]?\d{1,7}[\-\s]?[0-9X]))\b",
        normalized,
        re.IGNORECASE,
    )
    if isbn_match:
        isbn = isbn_match.group(1).replace(" ", "").replace("-", "")

    if "isbn" in lower and isbn is None:
        isbn_token = re.search(r"isbn\s*[:\-]?\s*([0-9X\-\s]{10,20})", lower)
        if isbn_token:
            isbn = isbn_token.group(1).replace(" ", "").replace("-", "")

    return {
        "title": title,
        "author": author,
        "publisher": publisher,
        "year": year,
        "isbn": isbn,
    }


def _find_pdf_candidates(driver) -> List[dict]:
    by_module = importlib.import_module("selenium.webdriver.common.by")
    By = by_module.By

    candidates: List[dict] = []
    anchors = driver.find_elements(By.CSS_SELECTOR, "a")
    for anchor in anchors:
        href = anchor.get_attribute("href") or ""
        text = (anchor.text or "").lower()
        if not href:
            continue
        href_lower = href.lower()
        direct_pdf = href_lower.endswith(".pdf") or ".pdf" in href_lower
        hinted_pdf = "pdf" in text
        if direct_pdf or hinted_pdf:
            candidates.append(
                {
                    "url": href,
                    "direct_pdf": direct_pdf,
                    "hinted_pdf": hinted_pdf,
                }
            )
    deduped = {}
    for item in candidates:
        key = item["url"].lower()
        if key in deduped:
            continue
        deduped[key] = item
    return list(deduped.values())


def _follow_pdf_hints(driver, config: CrawlerConfig, candidates: List[dict]) -> List[str]:
    by_module = importlib.import_module("selenium.webdriver.common.by")
    ec_module = importlib.import_module("selenium.webdriver.support.expected_conditions")
    ui_module = importlib.import_module("selenium.webdriver.support.ui")
    By = by_module.By
    EC = ec_module
    WebDriverWait = ui_module.WebDriverWait

    found: List[str] = []
    follow_limit = 3
    followed = 0

    for item in candidates:
        if followed >= follow_limit:
            break
        if item.get("direct_pdf"):
            continue
        if not item.get("hinted_pdf"):
            continue
        url = item.get("url")
        if not url:
            continue
        try:
            driver.get(url)
            WebDriverWait(driver, config.timeout).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            _random_delay(config)
            nested = _find_pdf_candidates(driver)
            for nested_item in nested:
                if nested_item.get("direct_pdf"):
                    found.append(nested_item["url"])
        except Exception:
            continue
        followed += 1

    return list(dict.fromkeys(found))


def analyze_result(driver, config: CrawlerConfig, result: SearchResult) -> dict:
    exc_module = importlib.import_module("selenium.common.exceptions")
    by_module = importlib.import_module("selenium.webdriver.common.by")
    ec_module = importlib.import_module("selenium.webdriver.support.expected_conditions")
    ui_module = importlib.import_module("selenium.webdriver.support.ui")
    TimeoutException = exc_module.TimeoutException
    StaleElementReferenceException = exc_module.StaleElementReferenceException
    By = by_module.By
    EC = ec_module
    WebDriverWait = ui_module.WebDriverWait

    for attempt in range(config.retries + 1):
        try:
            driver.get(result.url)
            WebDriverWait(driver, config.timeout).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            _random_delay(config)
            text = _collect_page_text(driver)
            metadata = _extract_metadata(text, result.title)
            candidates = _find_pdf_candidates(driver)
            hinted_urls = _follow_pdf_hints(driver, config, candidates)
            candidate_urls = [item["url"] for item in candidates]
            candidate_urls.extend(hinted_urls)
            candidate_urls = list(dict.fromkeys(candidate_urls))
            decision = decision_for(text, result.domain)
            return {
                "rank": result.rank,
                "source": {
                    "title": result.title,
                    "url": result.url,
                    "domain": result.domain,
                    "snippet": result.snippet,
                    "relevance_score": result.relevance_score,
                    "relevance_reasons": result.relevance_reasons,
                },
                "book": metadata,
                "candidates": [
                    {
                        "url": url,
                        "content_type": None,
                        "license_signals": decision.get("license_signals", []),
                        "confidence": decision.get("confidence", "low"),
                    }
                    for url in candidate_urls
                ],
                "decision": {
                    "status": decision["status"],
                    "reason": decision["reason"],
                    "selected_url": None,
                },
                "downloads": [],
            }
        except (TimeoutException, StaleElementReferenceException):
            if attempt >= config.retries:
                return {
                    "rank": result.rank,
                    "source": {
                        "title": result.title,
                        "url": result.url,
                        "domain": result.domain,
                        "snippet": result.snippet,
                        "relevance_score": result.relevance_score,
                        "relevance_reasons": result.relevance_reasons,
                    },
                    "book": {
                        "title": None,
                        "author": None,
                        "publisher": None,
                        "year": None,
                        "isbn": None,
                    },
                    "candidates": [],
                    "decision": {
                        "status": "blocked",
                        "reason": "page_timeout",
                        "selected_url": None,
                    },
                    "downloads": [],
                }
            continue

    return {}
