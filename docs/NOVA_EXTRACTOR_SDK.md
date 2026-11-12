# NOVA FETCH Extractor SDK

NOVA FETCH extractors are ordinary Python modules loaded inside the self-hosted process. They do not call a public downloader API. A module implements an adapter and registers it with the local registry.

## Minimal adapter

```python
from urllib.parse import urlsplit

from server.extractors.registry import ExtractorRegistry
from server.security import validate_external_url


class ExamplePublicMediaExtractor:
    name = "example-public-media"
    priority = 50

    def supports(self, url: str) -> bool:
        parsed = urlsplit(url)
        return (
            parsed.scheme in {"http", "https"}
            and parsed.hostname == "media.example.test"
        )

    def extract(self, url: str):
        safe_url = validate_external_url(url)
        # Resolve a public media URL using the site's documented,
        # public metadata format. Do not scrape credentials or private APIs.
        return {
            "media_url": safe_url,
            "title": "Example public media",
            "creator": "Example publisher",
            "thumbnail": None,
            "source": "NOVA adapter · example-public-media",
        }


def register(registry: ExtractorRegistry) -> None:
    registry.register(ExamplePublicMediaExtractor())
```

## Load an adapter

Place your module in the Python environment used by the container, make sure it can be imported, and set:

```env
NOVA_EXTRACTOR_MODULES=my_package.my_adapter
```

Multiple adapters can be comma-separated:

```env
NOVA_EXTRACTOR_MODULES=my_package.video_site,my_package.audio_site
```

Restart NOVA FETCH after changing the variable. The configured adapters appear in `GET /api/config` under `tools.extractors`.

## Built-in public PeerTube adapter

The built-in `nova-peertube` adapter recognizes public `/videos/watch/{id}` and `/w/{shortUUID}` page URLs. It requests the video's public metadata from the same instance using `GET /api/v1/videos/{id}`, without authentication. The adapter prefers a public HLS playlist so NOVA Native can apply its HLS quality selection. When HLS is disabled and public progressive files are listed, it chooses the highest-resolution public file. Live streams are left to the compatibility path.

The adapter does not use account credentials, retrieve private videos, use owner-only source-download endpoints, or bypass any privacy/access restrictions. Every returned media and thumbnail URL is validated before it is used.

## Safe diagnostics

`GET /api/config` exposes adapter names and bounded recent-failure diagnostics under `tools.extractor_diagnostics`. A failure record contains only the adapter name and exception class. It deliberately omits request URLs, query parameters, exception messages and other values that could contain signed URLs or tokens.

## Contract

Each adapter must provide:

- `name`: unique, stable name.
- `priority`: integer; higher priority runs first.
- `supports(url)`: fast predicate that should not perform network requests.
- `extract(url)`: return a dictionary containing `media_url`; optional metadata fields are `title`, `creator`, `thumbnail`, and `source`. Return `None` when the adapter cannot resolve the source.

The returned media URL is validated again by NOVA Native before it is fetched. The current registry isolates a plugin exception and continues to the next adapter.

## Safety and reliability

- Only handle public URLs and media the user is entitled to save.
- Use the official public embed/metadata/stream interface documented by the source where applicable.
- Do not use browser cookies, passwords, session tokens, hidden private endpoints, or authentication scraping.
- Do not bypass paywalls, DRM, geo/access restrictions, or private-account controls.
- Apply URL validation to every resolved URL. Do not trust a redirect or a CDN URL just because the original page was public.
- Keep network requests bounded by timeouts and size caps.
- Do not download a playlist without an explicit user action and a clear item limit.
- Include tests for malformed metadata, redirects, unsupported content, and denied hosts.

A plugin can improve compatibility for one public source. No generic plugin system can guarantee every website or app; website formats and policies change.
