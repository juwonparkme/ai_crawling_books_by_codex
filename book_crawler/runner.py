from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from .config import CrawlerConfig
from .crawler import SearchResult, analyze_result, collect_search_results, create_driver
from .downloader import build_pdf_filename, download_pdf


def build_queries(config: CrawlerConfig) -> List[str]:
    title = _quote_phrase(config.title)
    author = _quote_phrase(config.author) if config.author else None
    base = f"{title} {author}".strip() if author else title
    queries = [f"{base} filetype:pdf", f"{base} site:.edu"]

    if config.year_from is not None or config.year_to is not None:
        year_from = config.year_from or ""
        year_to = config.year_to or ""
        queries.append(f"{base} {year_from}..{year_to} filetype:pdf")

    return queries


def _quote_phrase(value: str) -> str:
    compact = " ".join((value or "").split())
    if not compact:
        return ""
    return f"\"{compact}\""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _initial_payload(config: CrawlerConfig, queries: List[str]) -> dict:
    run_id = str(uuid.uuid4())
    input_payload = asdict(config)
    input_payload["out_dir"] = str(config.out_dir)
    return {
        "run_id": run_id,
        "timestamp": _now_iso(),
        "input": input_payload,
        "query": queries,
        "results": [],
        "stats": {
            "total_results": 0,
            "total_candidates": 0,
            "downloaded": 0,
            "skipped": 0,
            "failed": 0,
        },
    }


def write_run_json(out_dir: Path, payload: dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    run_id = payload["run_id"]
    path = out_dir / f"run_{run_id}.json"
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return path


def run(config: CrawlerConfig) -> Path:
    queries = build_queries(config)
    payload = _initial_payload(config, queries)
    driver = create_driver(config)
    try:
        all_results = []
        for query in queries:
            results = collect_search_results(driver, config, query)
            all_results.extend(results)

        all_results = _prioritize_search_results(all_results, config.max_results)
        for result in all_results:
            payload_result = analyze_result(driver, config, result)
            if not payload_result:
                continue

            candidates = _prioritize_candidates(payload_result.get("candidates", []))
            decision = payload_result.get("decision", {})
            allowed = decision.get("status") == "allowed"
            selected_url = candidates[0]["url"] if candidates else None
            if selected_url:
                decision["selected_url"] = selected_url

            if allowed and selected_url and not config.dry_run:
                book = payload_result.get("book", {})
                filename = build_pdf_filename(
                    book.get("title"),
                    book.get("author"),
                    book.get("year"),
                )
                path, info = download_pdf(selected_url, config.out_dir, filename, config.timeout)
                if path:
                    payload_result["downloads"].append(
                        {
                            "path": str(path),
                            "size_bytes": info.get("size_bytes"),
                            "sha256": info.get("sha256"),
                            "status": info.get("status"),
                            "error": info.get("error"),
                        }
                    )
                    payload["stats"]["downloaded"] += 1
                else:
                    payload_result["downloads"].append(
                        {
                            "path": None,
                            "size_bytes": None,
                            "sha256": None,
                            "status": info.get("status"),
                            "error": info.get("error"),
                        }
                    )
                    payload["stats"]["failed"] += 1
            else:
                if selected_url:
                    payload_result["downloads"].append(
                        {
                            "path": None,
                            "size_bytes": None,
                            "sha256": None,
                            "status": "skipped",
                            "error": "dry_run_or_not_allowed",
                        }
                    )
                payload["stats"]["skipped"] += 1

            payload["results"].append(payload_result)

        payload["stats"]["total_results"] = len(payload["results"])
        payload["stats"]["total_candidates"] = sum(
            len(item.get("candidates", [])) for item in payload["results"]
        )
    finally:
        driver.quit()

    return write_run_json(config.out_dir, payload)


def _prioritize_candidates(candidates: List[dict]) -> List[dict]:
    seen = set()
    ordered: List[dict] = []

    def score(url: str) -> int:
        url_lower = url.lower()
        if url_lower.endswith(".pdf"):
            return 0
        if ".pdf" in url_lower:
            return 1
        return 2

    for candidate in candidates:
        url = candidate.get("url")
        if not url:
            continue
        key = url.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(candidate)

    ordered.sort(key=lambda item: score(item["url"]))
    return ordered


def _prioritize_search_results(results: List[SearchResult], limit: int) -> List[SearchResult]:
    best_by_url: dict[str, SearchResult] = {}
    for result in results:
        key = result.url.lower()
        current = best_by_url.get(key)
        if current is None or result.relevance_score > current.relevance_score:
            best_by_url[key] = result

    ordered = sorted(best_by_url.values(), key=lambda item: (-item.relevance_score, item.rank))
    for index, result in enumerate(ordered, start=1):
        result.rank = index
    return ordered[:limit]
