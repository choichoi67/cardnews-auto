from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from html import unescape
from pathlib import PurePosixPath
import re
from textwrap import shorten
from urllib.parse import urljoin, urlparse

import feedparser
import requests

from .models import Article
from .workflow import _is_duplicate, candidate_time


_REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; CardnewsAuto/0.1; editorial review)"}


def _high_resolution_image_url(url: str) -> str:
    """Replace HotNewHipHop's 228px article thumbnails with its full editorial image."""
    return re.sub(
        r"/i/v1/\d+x\d+-cover-(\d+)-auto/",
        r"/imgprst/2292x1200-fit-\1-auto/",
        url,
    )


def _fetch_page(url: str):
    try:
        response = requests.get(url, timeout=20, headers=_REQUEST_HEADERS)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        return response
    except requests.RequestException:
        return None


class _PageContentParser(HTMLParser):
    def __init__(self, base_url: str = ""):
        super().__init__()
        self.base_url = base_url
        self.description = ""
        self.published = ""
        self.image_url = ""
        self.images: list[dict[str, str]] = []
        self._paragraph_depth = 0
        self._paragraph_text: list[str] = []
        self.paragraphs: list[str] = []
        self._link: tuple[str, list[str]] | None = None
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        values = {str(key).lower(): value for key, value in attrs if value is not None}
        if tag == "a" and re.search(r"/\d{6,}-", values.get("href", "")):
            self._link = (urljoin(self.base_url, values["href"]), [])
        elif tag == "meta":
            key = str(values.get("name") or values.get("property") or "").lower()
            content = str(values.get("content") or "").strip()
            if key in {"description", "og:description"} and content and not self.description:
                self.description = content
            if key == "article:published_time" and content and not self.published:
                self.published = content
            if key in {"og:image", "twitter:image"} and content and not self.image_url:
                self.image_url = urljoin(self.base_url, content)
                self.images.append({
                    "url": self.image_url,
                    "page_url": self.base_url,
                    "provider": "rss_article",
                    "caption": "",
                })
        elif tag in {"img", "source"}:
            srcset = str(values.get("srcset") or values.get("data-srcset") or "")
            candidates = [
                (int(match.group(2) or 0), match.group(1))
                for item in srcset.split(",")
                if (match := re.match(r"\s*(\S+)(?:\s+(\d+)w)?", item))
            ]
            source = str(
                (max(candidates)[1] if candidates else "")
                or values.get("data-original")
                or values.get("data-lazy-src")
                or values.get("data-src")
                or values.get("src")
                or ""
            ).strip()
            if source:
                self.images.append({
                    "url": urljoin(self.base_url, source),
                    "page_url": self.base_url,
                    "provider": "rss_article",
                    "caption": str(values.get("alt") or values.get("title") or "").strip(),
                })
        elif tag == "p":
            self._paragraph_depth = 1
            self._paragraph_text = []
        elif self._paragraph_depth:
            self._paragraph_depth += 1

    def handle_data(self, data: str) -> None:
        if self._link:
            self._link[1].append(data)
        if self._paragraph_depth:
            self._paragraph_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._link:
            url, text = self._link
            title = re.sub(r"\s+", " ", " ".join(text)).strip()
            if len(title) >= 8:
                self.links.append((url, title))
            self._link = None
        if not self._paragraph_depth:
            return
        if tag == "p" and self._paragraph_depth == 1:
            text = re.sub(r"\s+", " ", " ".join(self._paragraph_text)).strip()
            if len(text) >= 40:
                self.paragraphs.append(text)
            self._paragraph_depth = 0
            self._paragraph_text = []
        elif self._paragraph_depth > 1:
            self._paragraph_depth -= 1


def _extract_page_metadata(html: str, base_url: str = "") -> tuple[str, str, str]:
    parser = _PageContentParser(base_url)
    parser.feed(html)
    published = parser.published
    if not published:
        match = re.search(r'"datePublished"\s*:\s*"([^"]+)"', html)
        published = match.group(1) if match else ""
    text = re.sub(r"\s+", " ", unescape(re.sub(
        r"<[^>]+>", " ", parser.description or " ".join(parser.paragraphs[:2])
    ))).strip()
    return (text if len(text) <= 280 else shorten(text, width=280, placeholder="…")), published, parser.image_url


def discover_article_image(url: str) -> str:
    response = _fetch_page(url)
    if not response:
        return ""
    _summary, _published, image_url = _extract_page_metadata(response.text, response.url)
    return image_url


def discover_article_images(url: str, max_items: int = 12) -> list[dict[str, str]]:
    """Return useful article images with provenance; never implies reuse permission."""
    response = _fetch_page(url)
    if not response:
        return []
    parser = _PageContentParser(response.url)
    parser.feed(response.text)
    results: dict[str, tuple[int, dict[str, str]]] = {}
    blocked_terms = (
        "logo", "icon", "avatar", "sprite", "tracking", "pixel", "badge",
        "emoji", "spacer", "placeholder", "advert", "banner", "author",
    )
    blocked_hosts = ("scorecardresearch.com", "doubleclick.net", "googletagmanager.com")
    for item in parser.images:
        image_url = _high_resolution_image_url(item["url"].strip())
        parsed = urlparse(image_url)
        path = parsed.path.lower()
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        if any(term in parsed.netloc.lower() for term in blocked_hosts):
            continue
        if any(term in path for term in blocked_terms) or path.endswith((".svg", ".gif")):
            continue
        dimensions = re.search(r"(?:image|resize)_(\d+)_(\d+)", path)
        if dimensions and min(map(int, dimensions.groups())) < 200:
            continue
        identity = PurePosixPath(path).name or image_url.split("?", 1)[0]
        score = (
            int(dimensions.group(1)) * int(dimensions.group(2))
            if dimensions else
            1_000_000 if "content_image_intermediate" in path else
            800_000 if "og_thumbnail" in path else
            500_000
        )
        candidate = {
            **item,
            "url": image_url,
            "page_url": response.url,
        }
        if identity not in results or score > results[identity][0]:
            results[identity] = (score, candidate)
    return [item for _score, item in results.values()][:max_items]


def enrich_page_articles(articles: list[Article]) -> list[Article]:
    enriched: list[Article] = []
    for article in articles:
        response = _fetch_page(article.url)
        if response:
            summary, published, image_url = _extract_page_metadata(
                response.text, response.url,
            )
            article = article.model_copy(update={
                "summary": summary,
                "published": published,
                "image_url": image_url,
            })
        enriched.append(article)
    return enriched

def collect_page_articles(pages: list[dict]) -> list[Article]:
    articles: list[Article] = []
    seen: set[str] = set()
    headers = {"User-Agent": "CardnewsAuto/0.1 (+local editorial review workflow)"}
    for page in pages:
        response = requests.get(page["url"], timeout=20, headers=headers)
        response.raise_for_status()
        response.encoding = "utf-8"
        parser = _PageContentParser(page["url"])
        parser.feed(response.text)
        count = 0
        for url, title in parser.links:
            if url in seen:
                continue
            seen.add(url)
            articles.append(Article(title=title, url=url, source=page.get("source", page["url"])))
            count += 1
            if count >= page.get("max_items", 20):
                break
    return articles


def collect_articles(cfg: dict, existing_candidates: list[dict] | None = None) -> list[Article]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=cfg.get("lookback_hours", 48))
    required = [x.lower() for x in cfg.get("required_keywords", [])]
    blocked = [x.lower() for x in cfg.get("blocked_keywords", [])]
    seen: set[str] = set()
    articles: list[Article] = enrich_page_articles(collect_page_articles(cfg.get("pages", [])))
    seen.update(article.url for article in articles)

    for feed_url in cfg.get("feeds", []):
        feed = feedparser.parse(feed_url)
        for entry in feed.entries:
            url = entry.get("link", "")
            title = entry.get("title", "").strip()
            haystack = f"{title} {entry.get('summary', '')}".lower()
            if not url or url in seen or any(k in haystack for k in blocked):
                continue
            if required and not any(k in haystack for k in required):
                continue
            published = entry.get("published", "")
            if published:
                try:
                    if parsedate_to_datetime(published).astimezone(timezone.utc) < cutoff:
                        continue
                except (TypeError, ValueError, OverflowError):
                    pass
            seen.add(url)
            articles.append(Article(
                title=title, url=url, summary=entry.get("summary", ""),
                source=entry.get("source", {}).get("title", feed.feed.get("title", "")),
                published=published,
            ))
    def published_at(article: Article) -> datetime:
        if not article.published:
            return datetime.min.replace(tzinfo=timezone.utc)
        try:
            return parsedate_to_datetime(article.published).astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError):
            try:
                return datetime.fromisoformat(article.published.replace("Z", "+00:00")).astimezone(timezone.utc)
            except ValueError:
                return datetime.min.replace(tzinfo=timezone.utc)

    articles.sort(key=published_at, reverse=True)
    selected: list[Article] = []
    known = list(existing_candidates or [])
    new_count = 0
    for article in articles:
        incoming = article.model_dump()
        if candidate_time(incoming) != datetime.min.replace(tzinfo=timezone.utc) and candidate_time(incoming) < cutoff:
            continue
        duplicate = any(_is_duplicate(item, incoming) for item in known)
        selected.append(article)
        if not duplicate:
            known.append(incoming)
            new_count += 1
        if new_count >= cfg.get("max_candidates", 10):
            break
    return selected
