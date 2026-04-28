#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os

from crawl4ai import BrowserConfig, CacheMode, CrawlerRunConfig, Crawl4aiDockerClient
from crawl4ai.content_filter_strategy import PruningContentFilter
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator


def docker_base_url() -> str:
    return os.getenv("CRAWL4AI_DOCKER_URL", "http://127.0.0.1:11235").rstrip("/")


def docker_timeout() -> float:
    return float(os.getenv("CRAWL4AI_DOCKER_TIMEOUT", "60"))


def extract_markdown(markdown) -> tuple[str, str]:
    if hasattr(markdown, "raw_markdown"):
        return getattr(markdown, "raw_markdown", "") or "", getattr(markdown, "fit_markdown", "") or ""
    return str(markdown or ""), ""


async def fetch_url_with_docker(url: str) -> dict:
    """Fetch one URL through the Crawl4AI Docker server API."""
    browser_config = BrowserConfig(
        browser_type="chromium",
        headless=True,
        verbose=False,
    )
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        markdown_generator=DefaultMarkdownGenerator(
            content_filter=PruningContentFilter(threshold=0.48, threshold_type="fixed", min_word_threshold=0)
        ),
    )
    async with Crawl4aiDockerClient(base_url=docker_base_url(), timeout=docker_timeout(), verbose=False) as client:
        result = await client.crawl([url], browser_config=browser_config, crawler_config=run_config)
    markdown = getattr(result, "markdown", "") or ""
    raw_markdown, fit_markdown = extract_markdown(markdown)
    html = getattr(result, "html", "") or raw_markdown
    success = getattr(result, "success", True)
    if not success:
        raise RuntimeError(getattr(result, "error_message", "") or "crawl4ai_docker_failed")
    return {"html": html, "raw_markdown": raw_markdown, "fit_markdown": fit_markdown}
