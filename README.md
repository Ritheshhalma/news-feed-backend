# News Feed Backend

Django-based news aggregator backend with a Celery scraping pipeline, real-time WebSocket updates, and a dead-letter queue for fault tolerance.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Tech Stack](#tech-stack)
- [Source Onboarding](#1-source-onboarding)
- [Periodic Scraping](#2-periodic-scraping)
- [Ingest Pipeline](#3-ingest-pipeline)
- [Image Processing](#4-image-processing)
- [Reading the Feed](#5-reading-the-feed)
- [Real-time Updates](#6-real-time-updates)
- [On-demand Refresh](#7-on-demand-refresh)
- [Error Handling & DLQ](#8-error-handling--dead-letter-queue)
- [Queue Reference](#queue-reference)
- [Tool Decisions](#tool-decisions)
- [Running Locally](#running-locally)
- [Management Commands](#management-commands)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         USER ACTIONS                                │
│  Add Source URL  →  Read Feed  →  Filter/Search  →  Live Rates     │
└────────────┬────────────────────────────────────────────────────────┘
             │
┌────────────▼────────────────────────────────────────────────────────┐
│                     NGINX (port 80)                                 │
│  /api/*  → Django   /ws/*  → Daphne   /media/* → local files       │
└────────────┬──────────────────────────┬─────────────────────────────┘
             │                          │
    ┌────────▼────────┐      ┌──────────▼──────────┐
    │  Django + DRF   │      │  Django Channels     │
    │  REST API       │      │  WebSocket server    │
    └────────┬────────┘      └──────────┬───────────┘
             │                          │
    ┌────────▼──────────────────────────▼───────────┐
    │           RabbitMQ (4 queues + DLQ)           │
    │  scrape.scheduled  scrape.ondemand             │
    │  media.process     live.poll  dead.letter      │
    └────────┬──────────────────────────────────────┘
             │
    ┌────────▼──────────────────────────┐
    │         Celery Workers            │
    │  General (×4)  Playwright (×2)    │
    └────────┬──────────────────────────┘
             │
    ┌────────▼──────────────────────────┐
    │     PostgreSQL + Redis            │
    │  Articles, Sources, Tasks  Cache  │
    └───────────────────────────────────┘
```

---

## Tech Stack

| Layer | Tool | Why |
|---|---|---|
| Web framework | Django 5 + DRF | REST API, ORM, admin |
| WebSocket server | Django Channels + Daphne | Real-time feed/live updates |
| Task queue | Celery + RabbitMQ | Async scraping, scheduling |
| Task scheduler | django-celery-beat | Per-source periodic tasks in DB |
| Cache | Redis | API response cache + channel layer |
| RSS parsing | feedparser | Structured feed entries, no HTML needed |
| HTTP client | httpx (async) | Listing page + article page fetches |
| HTML parsing | BeautifulSoup + lxml | Extract article URLs from listing pages |
| Article extraction | trafilatura | Full text, author, date, tags from any article page |
| JS rendering | Camoufox + Playwright | Render Next.js / SPA sites that httpx cannot read |
| Image processing | Pillow | Resize + compress thumbnails, save locally |
| API docs | drf-spectacular | Auto-generated OpenAPI / Swagger UI |
| Dead-letter queue | RabbitMQ `dead.letter` | Capture permanently failed tasks for replay |

---

## 1. Source Onboarding

The user pastes **any URL** — they never need to choose RSS or HTML. The backend auto-detects the correct type.

```
User: POST /api/v1/sources/
      { "url": "https://www.bbc.com/news", "portal_name": "BBC News" }

              │
              ▼
    Source saved (status = "pending")
    validate_source.delay() → scrape.ondemand queue

              │
              ▼
    ┌─────────────────────────────────────────────┐
    │           AUTO-DETECT TYPE                  │
    │                                             │
    │  Step 1 — URL pattern                       │
    │    /rss  /feed  /atom  .xml  → RSS          │
    │                                             │
    │  Step 2 — HTTP GET + Content-Type           │
    │    application/rss+xml → RSS                │
    │    text/html           → continue           │
    │                                             │
    │  Step 3 — RSS autodiscovery in HTML         │
    │    <link rel="alternate"                    │
    │          type="application/rss+xml"         │
    │          href="/news/rss.xml">              │
    │    → FOUND: switch URL to RSS feed URL      │
    │    → NOT FOUND: type = html                 │
    │                                             │
    │  Step 4 — JS rendering detection            │
    │    __NEXT_DATA__ or thin <div id="root">    │
    │    → flag: needs_playwright = True          │
    └─────────────────────────────────────────────┘

              │
              ▼
    Trial fetch (validates source before activating)
    → Success: source.status = "active"
    → Fail:    source.status = "failed", error stored

              │
              ▼
    Django signal → django-celery-beat creates PeriodicTask
    (scrape every 5 minutes, runs indefinitely)
```

**Example auto-detection outcomes:**

| User pastes | Detected | Stored as |
|---|---|---|
| `bbc.com/news` | `<link rel=alternate rss>` found | RSS: `bbc.com/news/rss.xml` |
| `ndtv.com/india-news` | Content-Type: text/xml | RSS (URL used directly) |
| `timesofindia.com` | No RSS found, static HTML | HTML two-stage |
| `reuters.com` | Next.js detected | HTML + Playwright fallback |

---

## 2. Periodic Scraping

Celery Beat fires `scrape_source` into `scrape.scheduled` every 5 minutes per active source.

### Path A — RSS Source

```
RSSAdapter.fetch()
      │
      └── feedparser.parse(feed_url)          ← 1 HTTP request
              │
              ▼
          For each <entry> in feed:
            title        ← <title>
            source_url   ← <link>
            content      ← <content> or <summary> (full body)
            image_url    ← <media:thumbnail> or <enclosure>
            published_at ← <published> (struct_time → ISO 8601)
            author       ← <author> or <dc:creator>
            category     ← URL path segment (/india-news/ → "India")
            tags         ← all <category> terms

          Returns 20–40 complete RawArticle objects
          (no follow-up requests needed)
```

RSS is the preferred path — one request, complete structured data per article.

### Path B — HTML Source

```
HTMLAdapter.fetch()
      │
      ├─ STAGE 1: Listing page  (1 HTTP request)
      │
      │   httpx GET listing_url
      │   if needs_playwright → Camoufox renders JS first
      │        │
      │        ▼
      │   BeautifulSoup scans all <a href> tags
      │   Filters out: nav links, external domains,
      │                section pages, share/social URLs
      │   Collects: article URL + card thumbnail
      │   Result: up to 60 article URLs
      │        │
      │        ▼
      │   DB DEDUP (1 query):
      │     known = Article.objects.filter(
      │         portal=source.portal,
      │         source_url__in=discovered_urls
      │     ).values_list('source_url', flat=True)
      │
      │     new_urls = discovered_urls − known
      │     (on regular runs: typically 0–5 new URLs)
      │
      ├─ STAGE 2: Article pages  (async, new_urls only)
      │
      │   httpx.AsyncClient + asyncio.Semaphore(5)
      │   Max 20 article pages fetched concurrently
      │        │
      │        ▼
      │   For each new article URL:
      │     html = await httpx.get(article_url)
      │
      │     trafilatura.extract(html) returns:
      │       title        — cleaned headline
      │       text         — full body (ads/nav stripped)
      │       author       — byline / meta author
      │       date         — article:published_time or JSON-LD datePublished
      │       tags         — keywords meta / article:tag
      │       language     — auto-detected
      │       image        — og:image (full size)
      │
      │     if result empty (JS-rendered or WAF blocked):
      │       → Camoufox fallback (scrape.playwright queue)
      │       → full browser render → re-run trafilatura
      │
      └─ Returns enriched RawArticle list
```

**Most scheduled runs cost**: 1 listing request + 0–5 article page requests. Not 60 requests every 5 minutes — Stage 2 only runs for URLs not already in the database.

---

## 3. Ingest Pipeline

Shared by both RSS and HTML adapters. Each article is its own database savepoint — one bad item cannot roll back the rest of the batch.

```
ingest_articles(portal, raw_articles)
      │
      For each RawArticle:
      │
      ├─ title_hash(title) → 64-bit fingerprint
      │   Article.filter(hashed_key=hash).first()
      │        │
      │        ├─ NEW article:
      │        │    get_or_create Category
      │        │    get_or_create Author
      │        │    parse published_at (RFC 2822 / ISO 8601 / naive → aware)
      │        │    INSERT Article(
      │        │      title, source_url, hashed_key,
      │        │      content, content_hash,
      │        │      thumbnail_url,   ← remote URL for now
      │        │      portal, category, author, published_at
      │        │    )
      │        │    attach tags (get_or_create each)
      │        │    enqueue process_article_image task
      │        │    counts["created"] += 1
      │        │
      │        └─ EXISTING article:
      │             compare content_hash
      │             ├─ Changed → UPDATE content, content_hash
      │             └─ Same   → skip (idempotent)
      │             backfill category / author / date if missing
      │             counts["updated"] / counts["unchanged"] += 1
      │
      ▼
  if created > 0 or updated > 0:
    WebSocket push → "feed" group:
    { "type": "feed.update", "new_count": N, "updated_count": M }
```

---

## 4. Image Processing

Triggered asynchronously for every new article that has an image URL. Runs in the `media.process` queue, separate from scraping.

```
process_article_image(article_id, image_url)
      │
      ▼
  httpx GET image_url
  (Referer header set to source domain)
      │
      ├─ Success:
      │    PIL.Image.open(content bytes)
      │    resize → max 800px width, maintain aspect ratio
      │    compress → JPEG quality 85
      │    save → /app/mediafiles/{article_id}.jpg
      │    UPDATE Article.thumbnail_url = "/media/{article_id}.jpg"
      │    (Nginx serves /media/* directly from the mediafiles volume)
      │
      └─ Failure (403, timeout, unreadable):
           autoretry × 3  (30s → 60s → 120s backoff)
                │
           Still fails:
                ▼
           DLQTask.on_failure() → dead.letter queue
           Article retains original remote thumbnail_url
           (still visible to users, just not locally cached)
```

---

## 5. Reading the Feed

```
GET /api/v1/articles/

Query parameters:
  ?source=<portal_uuid>    filter by news portal
  ?category=<id>           filter by category
  ?search=keyword          full-text search on title + content
  ?is_live=true            live-rate articles only
  ?page=1&page_size=20     pagination

Response:
  {
    "count": 592,
    "next": "/api/v1/articles/?page=2",
    "results": [
      {
        "id": "...",
        "title": "...",
        "content": "...",
        "source_url": "https://...",
        "thumbnail_url": "/media/{id}.jpg",
        "published_at": "2026-07-02T10:30:00Z",
        "portal_name": "Times of India",
        "category": { "id": 1, "name": "Business" },
        "author": { "id": 5, "name": "Rahul Sharma" },
        "tags": ["Economy", "Budget"],
        "is_live": false
      }
    ]
  }

Redis cache: second request for same params = 0 ms (5-minute TTL)
```

---

## 6. Real-time Updates

### Feed Updates — new articles arrive

```
Browser ◄──── ws://host/ws/feed/ ────── Django Channels
   │                                          │
   │  on connect: join "feed" channel group   │
   │                                          │
   │  [scrape cycle ingests 3 new articles]   │
   │                              ingest pushes:
   │                              layer.group_send("feed", {
   │                                "type": "feed.update",
   │                                "data": { "new_count": 3 }
   │                              })
   │
   │  ◄── { "new_count": 3 } ────────────────┘
   │
   UI: "3 new articles — click to refresh"
```

### Live Articles — Forex rates, stock prices

```
Browser ◄── ws://host/ws/live/{article_id}/ ── Django Channels
   │                                                  │
   │  [Celery Beat fires poll_live_articles]          │
   │                                                  │
   │    fetch current value from external source      │
   │    compare with RealtimeArticleState.value       │
   │    if changed:                                   │
   │      UPDATE RealtimeArticleState                 │
   │      layer.group_send(f"live_{article_id}", {    │
   │        "type": "live.update",                    │
   │        "data": { "value": 95.39 }                │
   │      })                                          │
   │                                                  │
   │  ◄── { "value": 95.39 } ────────────────────────┘
   │
   UI updates the live rate in place (no page reload)
```

---

## 7. On-demand Refresh

User triggers a manual re-scrape of a source from the admin UI.

```
POST /api/v1/sources/{id}/refresh/
          │
          ▼
    refresh_source.delay(source_id)
    → scrape.ondemand queue (processed ahead of scheduled tasks)
          │
          ▼
    Same scrape → ingest flow as periodic scraping
    Returns: { "task_id": "abc-123" }

Poll for result:
GET /api/v1/jobs/abc-123/
→ { "status": "SUCCESS", "articles_created": 5, "articles_updated": 2 }
```

---

## 8. Error Handling & Dead-Letter Queue

```
Task fails (network error, site blocked, image corrupt, etc.)
      │
      ▼
autoretry up to 3 times with exponential backoff
  attempt 1 → wait 30s → attempt 2 → wait 60s → attempt 3 → wait 120s
      │
      ├─ Recovers on retry → normal flow continues
      │
      └─ Still failing after 3 attempts:
              ▼
         DLQTask.on_failure() fires automatically
         Publishes to dead.letter queue:
         {
           "task": "articles.tasks.process_article_image",
           "id":   "task-uuid",
           "args": ["article-id", "https://source.com/img.jpg"],
           "error": "403 Forbidden"
         }
```

Admin recovery commands (run inside the backend container):

```bash
# Inspect what failed and why
python manage.py requeue_dead_letters --list

# Re-queue to original queue (for transient errors)
python manage.py requeue_dead_letters --replay

# Replay only the first N messages
python manage.py requeue_dead_letters --replay --limit 10

# Discard permanently failed messages
python manage.py requeue_dead_letters --purge
```

---

## Queue Reference

| Queue | Workers | Tasks routed here |
|---|---|---|
| `scrape.scheduled` | General ×4 | `scrape_source` (Celery Beat) |
| `scrape.ondemand` | General ×4 | `refresh_source`, `validate_source` |
| `media.process` | General ×4 | `process_article_image` |
| `live.poll` | General ×4 | `poll_live_articles` |
| `scrape.playwright` | Playwright ×2 | JS-rendered listing + article page fallback |
| `dead.letter` | None (inspection only) | Permanently failed tasks |

---

## Tool Decisions

| Scenario | Tool | Reason |
|---|---|---|
| Site has RSS feed | feedparser | One request, complete structured data per article |
| HTML listing page | httpx + BeautifulSoup | Fast, async, extracts URLs from server-rendered HTML |
| Article content extraction | trafilatura | 94.5% F1 score, multilingual, returns structured fields directly |
| JS-rendered pages | Camoufox + Playwright | Firefox fork with C++-level stealth patches, 0% headless detection rate |
| Auto-detect source type | Built-in (Content-Type + RSS autodiscovery) | User pastes any URL, no manual type selection required |
| Firecrawl | Not used | 63% success rate, outputs Markdown not structured fields, self-hosted doubles infrastructure |
| Scrapy / Crawlee | Not used | Full crawler frameworks — overkill inside Celery workers |

---

## Running Locally

```bash
# From the project root (requires deploy/.env)
docker-compose -f deploy/docker-compose.yml -p unicourt-newsfeed up -d

# Run database migrations
docker-compose -f deploy/docker-compose.yml -p unicourt-newsfeed \
  exec backend python manage.py migrate

# Run tests
docker-compose -f deploy/docker-compose.yml -p unicourt-newsfeed \
  exec backend python -m pytest articles/tests/ -v
```

Service ports:

| Service | URL |
|---|---|
| API + Frontend | `http://localhost` |
| Swagger UI | `http://localhost/api/schema/swagger-ui/` |
| RabbitMQ Management | `http://localhost:15672` (guest / guest) |
| Flower (Celery monitor) | `http://localhost:5555` |

---

## Management Commands

```bash
# Inspect / replay / purge dead-letter queue
python manage.py requeue_dead_letters --list
python manage.py requeue_dead_letters --replay
python manage.py requeue_dead_letters --replay --limit 10
python manage.py requeue_dead_letters --purge

# Django standard
python manage.py migrate
python manage.py createsuperuser
python manage.py collectstatic
```
