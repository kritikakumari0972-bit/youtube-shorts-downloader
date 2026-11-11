# NOVA FETCH

**A self-hosted media downloader with a built-in native engine.**

NOVA FETCH is being developed as its own product and codebase. It does not call a public downloader website or a Cobalt-hosted API. Downloads run on the machine/container that runs NOVA FETCH.

The project has two layers:

1. **NOVA Native** — first-party code in this repository for direct HTTP media files, public HTML5/Open Graph/JSON-LD discovery, static unencrypted HLS with quality selection, separate audio rendition muxing, HTTP byte-range segments and declared WebVTT subtitle extraction, DASH SegmentList and SegmentTemplate with separate audio/video adaptation muxing, bounded request retries, download queue/history, and local MP3 conversion through FFmpeg.
2. **Local site-extractor compatibility pack** — yt-dlp runs as a local Python library inside the same application process. It is not a remote API, but it is third-party source code and remains separately licensed. It expands website coverage when a website has a maintained extractor.

## Important compatibility boundary

No downloader can honestly guarantee every website or mobile app. Different sites use different page formats, signed requests, short-lived URLs, authentication systems and media protocols. Site integrations must be created and maintained one by one. NOVA Native currently supports direct media URLs, public HTML5 pages that expose direct media in HTML/Open Graph/JSON-LD metadata, static unencrypted HLS playlists (including quality selection, separately declared audio renditions, initialization maps and explicit or implicit byte ranges). For HLS master playlists that declare a plain WebVTT subtitle rendition, NOVA can select a subtitle language, normalize WebVTT timestamp maps and package the subtitle sidecar with the media file. One-period static DASH manifests use SegmentList, SegmentTemplate/SegmentTimeline, or a direct BaseURL. When DASH audio and video are split into separate AdaptationSets, NOVA selects the requested video quality and a compatible audio representation, downloads both tracks and muxes them locally with FFmpeg. Live media, encrypted streams, DASH SegmentBase, multi-Period DASH and native DASH subtitles are not implemented. Native HLS subtitles do not currently support fragmented-MP4 subtitle tracks or direct media playlists.

Automatic mode uses NOVA Native first for direct media/manifest URLs and public HTML5 pages with a directly exposed media source, then the local compatibility extractor when appropriate. It does not execute the page's JavaScript or use browser cookies. For pages requiring custom site-specific logic, it uses the locally installed compatibility extractor. No outside downloader service is required.

NOVA FETCH does not bypass DRM, paywalls, private accounts, login gates, or other access controls. Use it only for media you own or have permission to save.

## Features

- Responsive web dashboard for desktop and mobile browsers
- Paste up to 10 source links in one batch
- Metadata preview for supported sources
- Native direct-file downloads
- Native static HLS with quality selection, separate audio renditions, initialization maps and HTTP byte-range support
- Native HLS WebVTT subtitles with language selection, timestamp-map normalization and media-plus-subtitle ZIP output
- Native DASH SegmentList, SegmentTemplate and SegmentTimeline downloads
- Separate DASH audio/video adaptation selection and local FFmpeg muxing
- Bounded retry handling for temporary network and server errors
- Local MP3 conversion with FFmpeg
- Optional local website extractor pack for supported public sources
- Quality selection, playlists and subtitle export through the compatibility pack
- Progress, cancellation, download history and file retrieval
- SQLite job store and persistent Docker volume
- API-key protection, URL validation, queue limits and download-size limits

## Quick start — Docker

Requirements: Docker Engine and Docker Compose.

```bash
git clone https://github.com/kritikakumari0972-bit/youtube-shorts-downloader.git
cd youtube-shorts-downloader
git checkout nova-fetch-v1
cp .env.example .env
```

Create a strong local API key and put it in `.env` as `APP_API_KEY=...`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Build and start:

```bash
docker compose up -d --build
```

Open **http://127.0.0.1:8080**, choose Settings and paste the same API key.

The Docker Compose port is bound to localhost by default. Do not expose the service to the public internet without HTTPS, authentication, an appropriately configured reverse proxy and network-level egress controls.

```bash
docker compose logs -f nova-fetch
docker compose down
```

## Local development

Requirements: Python 3.12+, FFmpeg/FFprobe and Node.js or Deno if you enable the website compatibility pack.

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .venv\\Scripts\\Activate.ps1
pip install -r requirements-dev.txt
uvicorn server.main:app --reload --host 127.0.0.1 --port 8080
```

Set the variables in `.env.example` in your shell before starting. Open **http://127.0.0.1:8080**.

## Engine selection

- **Automatic · NOVA first** — use NOVA Native for direct media/HLS/DASH links; use the local compatibility pack for supported website pages.
- **NOVA Native only** — first-party native engine and registered first-party adapters only. It accepts direct media files, exposed HTML5 media, and the supported HLS/DASH subset; native subtitle extraction is not yet included.
- **Local site-extractor pack** — use the local yt-dlp compatibility component for its supported extractors.

The optional extractor component is installed in the same container. It does not send a request to a third-party downloader service. It does request the original media site and media CDN, as any downloader must.

## API routes

When `APP_API_KEY` is set, send the key in the `X-API-Key` header.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Health check |
| GET | `/api/config` | Native engine and local tool status |
| GET | `/api/preview?url=...` | Metadata or direct-source summary |
| POST | `/api/downloads` | Queue a download |
| GET | `/api/downloads` | Recent job history |
| GET | `/api/downloads/{id}` | Job state |
| POST | `/api/downloads/{id}/cancel` | Request cancellation |
| GET | `/api/downloads/{id}/file` | Download completed output |
| DELETE | `/api/downloads/{id}` | Remove a finished job and its files |
| GET | `/api/docs` | Interactive API documentation |

Example request body:

```json
{
  "url": "https://media.example/public-video.m3u8",
  "mode": "video",
  "quality": "best",
  "container": "mp4",
  "provider": "native",
  "playlist": false,
  "subtitles": false,
  "embed_metadata": true,
  "embed_thumbnail": false,
  "rights_confirmed": true
}
```

Provider values: `auto`, `native`, `yt-dlp`.

## Configuration

| Variable | Default | Purpose |
|---|---:|---|
| `APP_API_KEY` | empty | Protect all `/api/*` routes. Set a strong value. |
| `NOVA_FETCH_PORT` | `8080` | Docker host port. |
| `MAX_ACTIVE_JOBS` | `2` | Number of queue workers. |
| `MAX_QUEUED_JOBS` | `12` | Maximum active/queued jobs. |
| `MAX_DOWNLOAD_GB` | `8` | Per-file download cap. |
| `DATA_DIR` | local / `/data` in Docker | SQLite database and media directory. |\n| `NOVA_EXTRACTOR_MODULES` | empty | Comma-separated Python module import paths, each exposing `register(registry)`. |

## Tests

```bash
pip install -r requirements-dev.txt
python -m compileall -q server tests
python -m pytest -q
```

GitHub Actions runs Python tests and builds the Docker image.

## Code ownership and third-party software

NOVA FETCH's first-party UI, API, security, job management and native-engine code is maintained in this repository. Third-party source remains owned by its respective authors and must retain its applicable notices and license. You may modify and distribute code where its license permits; that does not transfer ownership of the upstream code or remove its license conditions.

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for the current component and license register. The repository's MIT license applies to NOVA FETCH's original code only, not to third-party components.
