# abs-molyhu-meta-provider
Audiobookshelf custom metadata provider for Hungarian books, using public Moly.hu book/search pages.

`abs-moly` is a small self-hosted HTTP service. Audiobookshelf sends it a title (and optionally an author); the service searches Moly.hu, opens the matching book pages, and converts the metadata to Audiobookshelf's custom-provider format.

> **Status:** This project is community software and is not affiliated with Audiobookshelf or Moly.hu.
> **Disclaimer:** This is a test / personal project and is not affiliated with or endorsed by Audiobookshelf or Moly.hu.
> It was fully **vibe coded** and may contain bugs, incomplete handling, or break when upstream sites change. Use it at your own risk.


## Features

- Audiobookshelf-compatible `GET /search` endpoint.
- Hungarian book metadata from Moly.hu.
- Title + optional author searches.
- Cleans common audiobook filename noise such as `(year)`, `[tags]`, `libgen`, and ebook file extensions.
- Parses title, author, publisher, publication year, description, cover, ISBN, tags, series, series sequence, and language when available.
- Prefers ebook editions when an edition list exposes that information.
- 24-hour in-memory caching by default.
- Serialized requests with a configurable delay between Moly requests.
- Authorization using the `AUTHORIZATION` HTTP header expected by Audiobookshelf.
- Docker / Docker Compose deployment.
- CLI debugging commands.
- Automated unit tests that do not require live access to Moly.hu.

## Repository layout

```text
abs-moly/
├── .dockerignore
├── .env.example
├── .gitignore
├── .github/
│   └── workflows/
│       └── test.yml
├── Dockerfile
├── LICENSE
├── README.md
├── app.py
├── docker-compose.yml
├── requirements.txt
├── requirements-dev.txt
└── tests/
    ├── fixtures/
    │   ├── moly_book.html
    │   └── moly_search.html
    └── test_app.py
```

## Requirements

On the Docker host:

- Docker Engine
- Docker Compose v2 (`docker compose`)
- Audiobookshelf with access to the host/IP and port where this service is published

You do **not** need Python installed on the server when using Docker.

## Quick start with Docker Compose

### 1. Create the project directory

Example:

```bash
sudo mkdir -p /opt/docker-stack/abs-moly
sudo chown -R "$USER":"$USER" /opt/docker-stack/abs-moly
cd /opt/docker-stack/abs-moly
```

### 2. Copy the repository files

The minimum runtime files are:

```text
app.py
docker-compose.yml
Dockerfile
requirements.txt
```

For a complete checkout, copy/upload the entire repository instead. The `.env.example`, README, and tests are useful but are not required at runtime.

### 3. Create a private `.env`

```bash
cp .env.example .env
```

Generate a random token, for example on Linux:

```bash
openssl rand -hex 32
```

Edit `.env` and replace:

```text
AUTH_TOKEN=replace-with-a-long-random-token
```

with the generated value.

**Do not commit `.env` to GitHub.** It is ignored by `.gitignore`.

### 4. Build and start

```bash
docker compose up -d --build
```

Check the container:

```bash
docker ps --filter name=abs-moly
```

Check logs:

```bash
docker logs --tail 100 abs-moly
```

### 5. Test the health endpoint

From the Docker host:

```bash
curl http://127.0.0.1:8787/
```

Expected:

```text
abs-moly ok
```

## Configuration

All values are configurable with `.env`:

| Variable | Default | Purpose |
|---|---:|---|
| `AUTH_TOKEN` | required | Token expected in the `AUTHORIZATION` request header. |
| `ABS_MOLY_PORT` | `8787` | Host port published by Docker. |
| `TZ` | `Europe/Budapest` | Container timezone. |
| `MAX_RESULTS` | `5` | Maximum Moly search URLs to inspect/return. |
| `MIN_INTERVAL` | `1.0` | Minimum delay, in seconds, between outbound Moly requests. |
| `CACHE_TTL` | `86400` | In-memory cache lifetime in seconds. |
| `REQUEST_TIMEOUT` | `20` | HTTP timeout for Moly requests. |
| `MOLY_BASE_URL` | `https://moly.hu` | Override only when testing against another compatible endpoint. |
| `USER_AGENT` | Firefox-like UA | HTTP User-Agent sent to Moly. |

The cache is intentionally in memory. Restarting the container clears it.

## Audiobookshelf configuration

Audiobookshelf's official custom-provider documentation says that a self-hosted provider is configured using the provider's address/IP and port, and that an authorization token can be supplied when needed. The official specification defines `GET /search`, requires `query`, allows `author`, returns `matches`, and uses the `AUTHORIZATION` header for API-key authentication.

### 1. Make sure Audiobookshelf can reach the provider

If Audiobookshelf is on another server, use the Docker host's LAN IP, for example:

```text
http://192.168.1.23:8787
```

If Audiobookshelf itself is running in Docker on the **same Docker network**, you can instead use the Compose service name, for example:

```text
http://abs-moly:8787
```

Do not expose the host port unnecessarily when an internal Docker network is enough.

### 2. Add the custom metadata provider

In Audiobookshelf, open:

```text
Settings
→ Item Metadata Utils
→ Custom Metadata Providers
→ Add
```

Create a **Book** custom provider with:

| Field | Value |
|---|---|
| Name | `Moly` |
| URL | `http://192.168.1.23:8787` |
| Authorization Header Value | the same value as `.env` → `AUTH_TOKEN` |

Use your actual server address instead of `192.168.1.23`.

**Important:** enter only the provider base URL. Do **not** append `/search`.

Use:

```text
http://192.168.1.23:8787
```

Audiobookshelf calls the `/search` path itself.

### 3. Select Moly for a library

Open the library's metadata settings and select **Moly** as an available/default online metadata provider where appropriate.

You can also choose Moly for an individual book from **Match**.

### 4. Match a book manually

For a book such as:

```text
A Dűne gyermekei
```

open the book in Audiobookshelf and choose **Match**.

Select **Moly**, search, then select the appropriate Moly result. Audiobookshelf lets you choose which metadata fields to apply during a manual match.

Audiobookshelf documents online matching as a manual operation; local metadata is scanned automatically, while online provider matching is intentionally initiated by an administrator or through the API.

### 5. Quick Match / Match Books

Audiobookshelf also supports Quick Match and bulk matching from the library tools. Because Moly is a search/scraping provider and does not expose audiobook duration data through this project, manual Match may be preferable when multiple editions of the same work exist.

## Testing before configuring Audiobookshelf

The fastest way to diagnose a problem is to test the container directly.

### Test Moly search

```bash
docker exec -it abs-moly python app.py --search "dűne gyermekei"
```

You can include the author:

```bash
docker exec -it abs-moly python app.py --search "dűne gyermekei" --author "Frank Herbert"
```

### Test a direct Moly book page

```bash
docker exec -it abs-moly python app.py --url \
  https://moly.hu/konyvek/frank-herbert-a-dune-gyermekei
```

### Test the exact endpoint Audiobookshelf calls

Replace the token with the value from `.env`:

```bash
curl \
  -H 'AUTHORIZATION: your-token-here' \
  'http://127.0.0.1:8787/search?query=d%C5%B1ne%20gyermekei'
```

Expected structure:

```json
{
  "matches": [
    {
      "title": "A Dűne gyermekei",
      "author": "Frank Herbert",
      "language": "hu"
    }
  ]
}
```

Exact optional fields depend on the Moly page.


## Troubleshooting

### Search returns an empty `matches` array

Run:

```bash
docker exec -it abs-moly python app.py --search "dűne gyermekei"
```

Then inspect the logs:

```bash
docker logs --tail 200 abs-moly
```

If Moly redirects the search to the homepage, the application logs a message similar to:

```text
Moly search redirected to homepage for 'dűne gyermekei': https://moly.hu/
```

This is deliberately surfaced because a successful HTTP 200 response from the wrong page is otherwise easy to mistake for a scraper returning no results.

### Direct book parsing works, but search does not

Test the direct URL:

```bash
docker exec -it abs-moly python app.py --url \
  https://moly.hu/konyvek/frank-herbert-a-dune-gyermekei
```

If that works, the problem is likely in Moly's search page or search URL rather than the book-page parser.

Moly has changed its HTML structure repeatedly over the years; the Calibre Moly plugin has a long history of site-layout fixes, including updates in 2024, 2025, and 2026. See the references below.

### Audiobookshelf returns a connection error

From the **Audiobookshelf host/container**, test:

```bash
curl http://container-ip-address:container-port/
```

If ABS runs in Docker, remember that `localhost` inside the ABS container means the ABS container itself, not the Docker host and not `abs-moly`.

When both containers share a Docker network, use:

```text
http://abs-moly:8787
```

### Audiobookshelf returns 401

Check that the value entered in **Authorization Header Value** is exactly the same as:

```text
AUTH_TOKEN=...
```

in `.env`.

Do not include:

```text
Authorization: 
```

in the value. The application compares the complete value received in the `AUTHORIZATION` header.

### Metadata is incomplete

Moly data varies by work and edition. The parser prefers structured JSON-LD and Open Graph metadata where available, then falls back to Moly-specific HTML elements.

The most edition-sensitive fields are:

- publisher
- publication year
- ISBN
- ebook/edition selection

If the HTML changes, update the parsing helpers in `app.py`; networking, throttling, and the ABS API do not need to change just because a selector changes.

## Development

Create a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

Run the tests:

```bash
pytest -q
```

Run the development server:

```bash
AUTH_TOKEN=test-token python app.py
```

Then:

```bash
curl -H 'AUTHORIZATION: test-token' \
  'http://127.0.0.1:8787/search?query=d%C5%B1ne%20gyermekei'
```

## Docker rebuild after code changes

When you change `app.py` or the dependency files:

```bash
docker compose down
docker compose up -d --build
```

A plain `docker compose restart` does **not** rebuild the image.

## GitHub publication

From the repository root:

```bash
git init
git add .
git commit -m "Initial abs-moly Audiobookshelf provider"
```

Create an empty GitHub repository, then:

```bash
git branch -M main
git remote add origin https://github.com/YOUR-USER/abs-moly.git
git push -u origin main
```

### Files that should be uploaded to GitHub

Upload/commit all of these:

```text
app.py
docker-compose.yml
Dockerfile
requirements.txt
requirements-dev.txt
.env.example
.gitignore
.dockerignore
LICENSE
README.md
.github/workflows/test.yml
tests/test_app.py
tests/fixtures/moly_search.html
tests/fixtures/moly_book.html
```

### File that must NOT be uploaded

Do not upload:

```text
.env
```

That file contains your real `AUTH_TOKEN`.

## Design notes

### Search URL

The provider intentionally uses:

```python
params = {"utf8": "✓", "query": search_query}
```

rather than the older:

```python
{"q": search_query}
```

The latter was observed redirecting to the Moly homepage in the supplied 2026 test.

### Throttling and caching

Every live Moly request is serialized through one lock and spaced by `MIN_INTERVAL` seconds. Search and book pages are cached in memory for `CACHE_TTL` seconds.

This is intended to keep repeated Audiobookshelf matching operations from hammering Moly unnecessarily.

### Authentication

If `AUTH_TOKEN` is non-empty, requests to `/search` must include:

```http
AUTHORIZATION: <token>
```

The health endpoint `/` is left unauthenticated so it can be used for basic connectivity checks.

### What this service does not do

- It does not modify audiobook files.
- It does not store a persistent database.
- It does not log into a Moly account.
- It does not provide a separate Moly API.
- It does not automatically match every Audiobookshelf book in the background.

## Respectful use

This project scrapes public web pages. Keep request rates modest, use the built-in cache, and monitor Moly behavior after deployment. If Moly changes its access rules or published structure, update the provider accordingly rather than trying to bypass restrictions.

## Sources and references

### Audiobookshelf custom metadata provider specification

Official Audiobookshelf repository specification:

https://github.com/advplyr/audiobookshelf/blob/master/custom-metadata-provider-specification.yaml

The current specification defines `/search`, `query`, optional `author`, the `matches` response, and `AUTHORIZATION` API-key authentication.

### Audiobookshelf metadata-provider documentation

Official Audiobookshelf documentation:

https://audiobookshelf.org/docs/documentation/community/community-providers/

This documents adding self-hosted custom providers through **Item Metadata Tools** and notes that community custom providers are not maintained or security-reviewed by the Audiobookshelf team.

### Audiobookshelf book metadata documentation

Official documentation:

https://github.com/audiobookshelf/audiobookshelf-docs/blob/master/docs/documentation/libraries/book-library/2.book-metadata.md

This explains manual Match, Quick Match, online metadata providers, and metadata precedence.

### Moly.hu search URL evidence

An indexed library document contains Moly search links using the current-style `query=` parameter, including:

https://moly.hu/kereses?utf8=%E2%9C%93&query=boldog+boldogtalan

Source document:

https://konyvtar.bmk.hu/documents/10180/3201061/olvasasi_kihivas%2B_%2Bkonyvlista_2025.pdf/071139d6-1ccc-486f-aa37-81fabef7864d

### Moly metadata plugin history

The long-running community Calibre Moly plugin is another useful reference because it has had to track Moly HTML changes over many years:

- GitHub: https://github.com/otapi/Calibre_Moly_hu
- Current Calibre plugin index: https://plugins.calibre-ebook.com/
- MobileRead discussion/history: https://www.mobileread.com/forums/showthread.php?t=193302

The current plugin index lists Moly_hu 5.1.3, released in August 2026, and the MobileRead history records a July/August 2026 web-layout/ISBN fix. These sources were used as cross-checks when designing the parser's fallbacks, not as copied code.

## Disclaimer

`abs-moly` is an independent community project. Moly.hu and Audiobookshelf are separate projects and trademarks belonging to their respective owners.
