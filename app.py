"""Audiobookshelf custom metadata provider backed by moly.hu.

The provider implements the Audiobookshelf custom metadata provider endpoint:

    GET /search?query=<title>&author=<optional author>

and returns a JSON object containing ``matches``.

Moly-specific code is deliberately kept in ``search_urls`` and the parsing
helpers so that future Moly HTML changes can be fixed in one place.

CLI debugging:
    python app.py --search "dűne gyermekei"
    python app.py --search "dűne gyermekei" --author "Frank Herbert"
    python app.py --url https://moly.hu/konyvek/frank-herbert-a-dune-gyermekei
"""

from __future__ import annotations

import hmac
import json
import os
import re
import sys
import threading
import time
import unicodedata
from typing import Any
from urllib.parse import urljoin

import requests
from flask import Flask, jsonify, request
from lxml import html

BASE = os.environ.get("MOLY_BASE_URL", "https://moly.hu").rstrip("/")
TOKEN = os.environ.get("AUTH_TOKEN", "")
MAX_RESULTS = int(os.environ.get("MAX_RESULTS", "5"))
MIN_INTERVAL = float(os.environ.get("MIN_INTERVAL", "1.0"))
CACHE_TTL = int(os.environ.get("CACHE_TTL", "86400"))
REQUEST_TIMEOUT = float(os.environ.get("REQUEST_TIMEOUT", "20"))
PORT = int(os.environ.get("PORT", "8787"))

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) "
    "Gecko/20100101 Firefox/130.0"
)

_session = requests.Session()
_session.headers.update(
    {
        "User-Agent": os.environ.get("USER_AGENT", DEFAULT_USER_AGENT),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "hu-HU,hu;q=0.9,en;q=0.5",
    }
)

# url -> (timestamp, response body, final URL after redirects)
_cache: dict[str, tuple[float, str, str]] = {}
_lock = threading.Lock()
_last_request_at = 0.0


def _request_key(url: str, params: dict[str, str] | None = None) -> str:
    prepared = requests.Request("GET", url, params=params or {}).prepare()
    return prepared.url


def _cache_get(key: str) -> tuple[str, str] | None:
    hit = _cache.get(key)
    if not hit:
        return None
    created_at, body, final_url = hit
    if time.time() - created_at >= CACHE_TTL:
        return None
    return body, final_url


def fetch_with_url(
    url: str, params: dict[str, str] | None = None
) -> tuple[str, str]:
    """Fetch a Moly page with throttling and an in-memory TTL cache."""
    global _last_request_at

    key = _request_key(url, params)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    with _lock:
        # Re-check after waiting for another worker that might have filled it.
        cached = _cache_get(key)
        if cached is not None:
            return cached

        wait = MIN_INTERVAL - (time.time() - _last_request_at)
        if wait > 0:
            time.sleep(wait)

        response = _session.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
        _last_request_at = time.time()
        response.raise_for_status()

        # Moly is a UTF-8 site. Prefer an explicitly supplied charset; fall
        # back to apparent encoding only when Requests did not get one.
        if not response.encoding:
            response.encoding = response.apparent_encoding or "utf-8"

        body = response.text
        final_url = str(response.url)
        _cache[key] = (time.time(), body, final_url)
        return body, final_url


def fetch(url: str, params: dict[str, str] | None = None) -> str:
    """Fetch a Moly page; kept as a small convenience wrapper for tests/CLI."""
    return fetch_with_url(url, params)[0]


def txt(element: Any) -> str:
    return " ".join(element.text_content().split()) if element is not None else ""


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = value.replace("\u200b", "").replace("\ufeff", "")
    return " ".join(value.split()).strip()


def clean_query(value: str) -> str:
    """Remove common filename noise from ABS-derived search text."""
    value = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", value)
    value = re.sub(
        r"\blibgen(?:\.\w+)?\b|\.(?:epub|mobi|azw3|pdf)\b",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", value).strip(" -_")


def _is_moly_book_path(href: str) -> str | None:
    href = normalize_text(href)
    match = re.match(
        r"^(?:https?://(?:www\.)?moly\.hu)?(/konyvek/[^/?#]+)/?$",
        href,
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else None


def extract_book_urls(doc: html.HtmlElement) -> list[str]:
    """Extract unique /konyvek/... links from a Moly search page."""
    candidates: list[str] = []

    # The search results have historically used ordinary <a href=...> links;
    # data-href/data-url are included as cheap resilience for layout changes.
    for attr in ("href", "data-href", "data-url"):
        values = doc.xpath(f"//*[@{attr}]/@{attr}")
        for value in values:
            path = _is_moly_book_path(value)
            if path:
                candidates.append(BASE + path)

    seen: set[str] = set()
    result: list[str] = []
    for url in candidates:
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result


def _homepage_redirect(final_url: str) -> bool:
    path = final_url.split("?", 1)[0].rstrip("/")
    return path in ("", BASE.rstrip("/"))


def search_urls(query: str, author: str = "") -> list[str]:
    """Search Moly using its current ``query=`` search parameter.

    ``q=`` was used by older versions of Moly's search URLs, but the supplied
    live test shows that those requests now redirect to the homepage. Current
    Moly links observed in indexed material use ``query=``.
    """
    title_query = clean_query(query)
    author_query = clean_query(author)
    if not title_query:
        return []

    queries: list[str] = []
    if author_query:
        queries.append(f"{author_query} {title_query}".strip())
    queries.append(title_query)

    seen_queries: set[str] = set()
    for search_query in queries:
        if search_query in seen_queries:
            continue
        seen_queries.add(search_query)

        params = {"utf8": "✓", "query": search_query}
        try:
            body, final_url = fetch_with_url(f"{BASE}/kereses", params)
        except requests.RequestException as exc:
            print(
                f"Moly search failed for {search_query!r}: {exc}",
                file=sys.stderr,
            )
            continue

        if _homepage_redirect(final_url):
            print(
                f"Moly search redirected to homepage for {search_query!r}: "
                f"{final_url}",
                file=sys.stderr,
            )
            continue

        try:
            doc = html.fromstring(body)
        except (TypeError, ValueError) as exc:
            print(
                f"Moly search HTML parse failed for {search_query!r}: {exc}",
                file=sys.stderr,
            )
            continue

        urls = extract_book_urls(doc)
        if urls:
            return urls[:MAX_RESULTS]

    return []


# ---------------------------------------------------------------------------
# Moly book-page parsing. Keep Moly-specific selectors here.
# ---------------------------------------------------------------------------


def meta(doc: html.HtmlElement, name: str) -> str:
    values = doc.xpath(
        f'//meta[@property="{name}"]/@content | //meta[@name="{name}"]/@content'
    )
    return normalize_text(values[0]) if values else ""


def json_ld(doc: html.HtmlElement) -> dict[str, Any]:
    """Return the first schema.org Book object found in JSON-LD."""
    for script in doc.xpath('//script[@type="application/ld+json"]/text()'):
        try:
            data = json.loads(script)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue

        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue

            item_type = item.get("@type", "")
            types = item_type if isinstance(item_type, list) else [item_type]
            if any("Book" in str(value) for value in types):
                return item

            graph = item.get("@graph")
            if isinstance(graph, list):
                for node in graph:
                    if not isinstance(node, dict):
                        continue
                    node_type = node.get("@type", "")
                    node_types = (
                        node_type if isinstance(node_type, list) else [node_type]
                    )
                    if any("Book" in str(value) for value in node_types):
                        return node
    return {}


def first_nonempty(values: list[str]) -> str:
    for value in values:
        value = normalize_text(value)
        if value:
            return value
    return ""


def extract_author_from_jsonld(data: dict[str, Any]) -> str:
    author = data.get("author")
    if isinstance(author, dict):
        return normalize_text(str(author.get("name", "")))
    if isinstance(author, list):
        names: list[str] = []
        for item in author:
            if isinstance(item, dict) and item.get("name"):
                names.append(normalize_text(str(item["name"])))
            elif isinstance(item, str):
                names.append(normalize_text(item))
        return ", ".join(x for x in names if x)
    if isinstance(author, str):
        return normalize_text(author)
    return ""


def parse_year(value: str) -> str:
    match = re.search(r"\b((?:1[5-9]\d\d|20\d\d))\b", value or "")
    return match.group(1) if match else ""


def normalize_isbn(value: str) -> str:
    value = normalize_text(value)
    match = re.search(
        r"(?<!\d)(97[89][0-9\- ]{10,17}|[0-9][0-9\- ]{8,16}[0-9Xx])(?!\d)",
        value,
    )
    if not match:
        return ""
    isbn = re.sub(r"[^0-9Xx]", "", match.group(1))
    return isbn.upper() if len(isbn) in (10, 13) else ""


def parse_edition(element: html.HtmlElement) -> dict[str, Any]:
    text = txt(element)

    publisher = first_nonempty(
        [
            txt(node)
            for node in element.xpath(
                './/a[starts-with(@href,"/kiadok/")]'
                ' | .//*[contains(concat(" ", normalize-space(@class), " "), " publisher ")]'
            )
        ]
    )

    ebook = bool(
        element.xpath(
            './/*[@data-title="Ekönyv" or @title="Ekönyv"]'
            ' | .//a[@href="/cimkek/ekonyv"]'
            ' | .//*[contains(translate(normalize-space(.), '
            '"EKÖNYV", "ekönyv"), "ekönyv")]'
        )
    )

    return {
        "publisher": publisher,
        "year": parse_year(text),
        "isbn": normalize_isbn(text),
        "ebook": ebook,
    }


def _jsonld_publisher(data: dict[str, Any]) -> str:
    publisher = data.get("publisher")
    if isinstance(publisher, dict):
        return normalize_text(str(publisher.get("name", "")))
    if isinstance(publisher, str):
        return normalize_text(publisher)
    return ""


def _jsonld_image(data: dict[str, Any]) -> str:
    value = data.get("image")
    if isinstance(value, str):
        return normalize_text(value)
    if isinstance(value, dict):
        return normalize_text(str(value.get("url", "")))
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and normalize_text(item):
                return normalize_text(item)
            if isinstance(item, dict) and item.get("url"):
                return normalize_text(str(item["url"]))
    return ""


def _extract_series(doc: html.HtmlElement) -> list[dict[str, str]]:
    series: list[dict[str, str]] = []
    for link in doc.xpath('//a[starts-with(@href,"/sorozatok/")]'):
        name = normalize_text(txt(link))
        if not name:
            continue

        parent_text = ""
        parent = link.getparent()
        if parent is not None:
            parent_text = normalize_text(parent.text_content())
        surrounding = " ".join(
            part for part in (normalize_text(link.tail or ""), parent_text) if part
        )
        sequence_match = re.search(
            r"(?:#|[Rr]ész|[Kk]ötet|[Vv]ol(?:ume)?\.?)\s*"
            r"(\d+(?:[.,]\d+)?)",
            surrounding,
        )

        item = {"series": name}
        if sequence_match:
            item["sequence"] = sequence_match.group(1).replace(",", ".")
        series.append(item)
        if len(series) >= 3:
            break
    return series


def parse_book(url: str) -> dict[str, Any]:
    """Parse a Moly work page into ABS ``BookMetadata`` fields."""
    body = fetch(url)
    doc = html.fromstring(body)
    ld = json_ld(doc)

    page_title = normalize_text(doc.findtext(".//title") or "")
    parts = [normalize_text(part) for part in page_title.split(" · ")]

    title = first_nonempty(
        ([parts[0]] if parts else []) + [txt(node) for node in doc.xpath("//h1")]
    )
    author = parts[1] if len(parts) >= 3 else ""

    title = first_nonempty([title, normalize_text(str(ld.get("name", "")))])
    author = first_nonempty(
        [author, extract_author_from_jsonld(ld)]
        + [
            txt(node)
            for node in doc.xpath(
                '//a[contains(@class,"author") or contains(@class,"fn")]'
            )
        ]
    )

    # Moly can display a translated title as "Original – Hungarian title".
    if " – " in title:
        title = title.rsplit(" – ", 1)[-1].strip()

    edition_nodes = doc.xpath(
        '//div[contains(concat(" ",normalize-space(@class)," ")," edition ")]'
        ' | //*[@data-edition]'
        ' | //li[contains(concat(" ",normalize-space(@class)," ")," edition ")]'
    )
    editions = [parse_edition(node) for node in edition_nodes]
    editions.sort(key=lambda item: not item["ebook"])

    def pick(field: str) -> str:
        return next((item[field] for item in editions if item.get(field)), "")

    publisher = pick("publisher") or _jsonld_publisher(ld)
    published_year = pick("year") or parse_year(
        normalize_text(str(ld.get("datePublished", "")))
    )
    isbn = first_nonempty([pick("isbn"), normalize_isbn(str(ld.get("isbn", "")))])

    tags: list[str] = []
    for anchor in doc.xpath('//a[starts-with(@href,"/cimkek/")]'):
        tag = normalize_text(txt(anchor))
        if tag and tag.lower() != "ekönyv" and tag not in tags:
            tags.append(tag)

    if not tags:
        keywords = ld.get("keywords")
        if isinstance(keywords, str):
            tags = [
                normalize_text(value)
                for value in re.split(r"[,;]", keywords)
                if normalize_text(value)
            ]
        elif isinstance(keywords, list):
            tags = [
                normalize_text(str(value))
                for value in keywords
                if normalize_text(str(value))
            ]

    image = meta(doc, "og:image") or _jsonld_image(ld)

    description = first_nonempty(
        [
            normalize_text(str(ld.get("description", ""))),
            meta(doc, "og:description"),
            meta(doc, "description"),
        ]
    )

    book: dict[str, Any] = {
        "title": title,
        "author": author,
        "publisher": publisher,
        "publishedYear": published_year,
        "description": description,
        "cover": urljoin(BASE, image) if image else "",
        "isbn": isbn,
        "tags": tags[:8],
        "series": _extract_series(doc),
        "language": "hu",
    }

    # Audiobookshelf requires only title. Empty fields are omitted.
    return {key: value for key, value in book.items() if value not in ("", None, [], {})}


def search(query: str, author: str = "") -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for url in search_urls(query, author):
        try:
            book = parse_book(url)
        except Exception as exc:  # One bad result should not sink the search.
            print(f"parse failed for {url}: {exc}", file=sys.stderr)
            continue
        if book.get("title"):
            results.append(book)
    return results


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------

app = Flask(__name__)


@app.get("/")
def health() -> str:
    return "abs-moly ok"


@app.get("/search")
def search_endpoint():
    if TOKEN and not hmac.compare_digest(
        request.headers.get("AUTHORIZATION", ""), TOKEN
    ):
        return jsonify(error="Unauthorized"), 401

    query = request.args.get("query", "").strip()
    if not query:
        return jsonify(error="query is required"), 400

    author = request.args.get("author", "").strip()
    try:
        return jsonify(matches=search(query, author))
    except requests.RequestException as exc:
        return jsonify(error=f"moly.hu request failed: {exc}"), 500


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--search":
        author = ""
        if len(sys.argv) >= 5 and sys.argv[3] == "--author":
            author = sys.argv[4]
        print(
            json.dumps(
                search(sys.argv[2], author),
                ensure_ascii=False,
                indent=2,
            )
        )
    elif len(sys.argv) == 3 and sys.argv[1] == "--url":
        print(json.dumps(parse_book(sys.argv[2]), ensure_ascii=False, indent=2))
    else:
        app.run(host="0.0.0.0", port=PORT)
