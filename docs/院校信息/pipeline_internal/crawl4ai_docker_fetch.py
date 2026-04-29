#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
from contextlib import contextmanager

from crawl4ai import BrowserConfig, CacheMode, CrawlerRunConfig, Crawl4aiDockerClient
from crawl4ai.content_filter_strategy import PruningContentFilter
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator


def docker_base_url() -> str:
    return os.getenv("CRAWL4AI_DOCKER_URL", "http://127.0.0.1:11235").rstrip("/")


def docker_timeout() -> float:
    return float(os.getenv("CRAWL4AI_DOCKER_TIMEOUT", "60"))


@contextmanager
def local_docker_proxy_bypass():
    """
    Ensure local Crawl4AI Docker calls do not get routed to shell proxy settings.
    Users often keep http_proxy/all_proxy enabled for external sites, but the
    Docker API itself is on 127.0.0.1 and must be reached directly.
    """
    keys = [
        "http_proxy", "https_proxy", "all_proxy",
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
        "no_proxy", "NO_PROXY",
    ]
    old = {key: os.environ.get(key) for key in keys}
    try:
        for key in ["http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"]:
            os.environ.pop(key, None)
        no_proxy_hosts = "127.0.0.1,localhost"
        os.environ["no_proxy"] = no_proxy_hosts
        os.environ["NO_PROXY"] = no_proxy_hosts
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


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
    with local_docker_proxy_bypass():
        async with Crawl4aiDockerClient(base_url=docker_base_url(), timeout=docker_timeout(), verbose=False) as client:
            result = await client.crawl([url], browser_config=browser_config, crawler_config=run_config)
    markdown = getattr(result, "markdown", "") or ""
    raw_markdown, fit_markdown = extract_markdown(markdown)
    html = getattr(result, "html", "") or raw_markdown
    success = getattr(result, "success", True)
    if not success:
        raise RuntimeError(getattr(result, "error_message", "") or "crawl4ai_docker_failed")
    return {"html": html, "raw_markdown": raw_markdown, "fit_markdown": fit_markdown}
