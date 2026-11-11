# NOVA FETCH — Universal Media Toolkit

**A self-hosted media workflow built around yt-dlp + FFmpeg, with an optional Cobalt API connector.**

NOVA FETCH expands the original YouTube-only prototype into a browser dashboard with metadata preview, batch jobs, progress, playlists, subtitles, MP3 extraction, FFmpeg merging, download history and a configurable provider architecture.

> **Realistic compatibility note:** No app can guarantee every website or mobile app. NOVA FETCH uses yt-dlp's changing extractor catalogue and can also call a Cobalt instance you configure. Site changes, region restrictions, app-only links, private content and protected streams can fail. This software does not bypass DRM, paywalls, login gates or access controls.

## v1 features

- **yt-dlp:** hundreds/thousands of public extractors, metadata preview, quality selection, playlist jobs (maximum 50 entries), subtitles where available and retries.
- **FFmpeg:** merge separate audio/video streams, extract MP3 and optionally embed metadata and thumbnails.
- **Cobalt:** optional adapter for a Cobalt API instance that you own or are explicitly allowed to use. The first version supports `tunnel` and `redirect` results; picker/local-processing flows are not implemented.
- **Dashboard:** responsive UI, batch submit (up to 10 URLs), live queue, progress, cancellation, save completed files and remove history.
- **Persistence:** SQLite job history plus a Docker volume for downloaded files.
- **Safety:** HTTP(S)-only URLs, blocking of private/local/reserved IP addresses, required permission confirmation, queue/rate/size limits and optional API-key authentication.

## Quick start — Docker

Requirements: Docker Engine and the Docker Compose plugin.

```bash
git clone https://github.com/kritikakumari0972-bit/youtube-shorts-downloader.git
cd youtube-shorts-downloader
git checkout nova-fetch-v1
cp .env.example .env
```

Generate a long API key:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Put the generated value in `.env` as `APP_API_KEY=...`, then launch:

```bash
docker compose up -d --build
```

Open **http://127.0.0.1:8080**, choose Settings, and paste that same API key. The default Docker port is bound to localhost only.

```bash
docker compose logs -f nova-fetch
docker compose down
```

Media and SQLite data live in the named Docker volume `nova_fetch_data` and survive container rebuilds.

## Local development

Requirements: Python 3.12+, FFmpeg/FFprobe and Node.js or Deno (a JavaScript runtime is recommended for modern YouTube support).

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .venv\\Scripts\\Activate.ps1
pip install -r requirements-dev.txt
cp .env.example .env
uvicorn server.main:app --reload --host 127.0.0.1 --port 8080
```

Export values from `.env` into your shell before starting the server. Then open **http://127.0.0.1:8080**.

## Cobalt connector

NOVA FETCH does not rely on an arbitrary shared endpoint. Configure a Cobalt instance you host or have permission to use:

```env
COBALT_API_URL=http://your-cobalt-host:9000
COBALT_API_KEY=
```

Restart the app. Cobalt will appear as connected in the engine status when configured. See the [Cobalt API docs](https://github.com/imputnet/cobalt/blob/main/docs/api.md) and [deployment instructions](https://github.com/imputnet/cobalt).

## Configuration

| Variable | Default | Purpose |
|---|---:|---|
| `APP_API_KEY` | empty | Protects every `/api/*` route. Use a strong secret. |
| `COBALT_API_URL` | empty | Optional self-hosted/authorized Cobalt API URL. |
| `COBALT_API_KEY` | empty | Optional Cobalt API credential. |
| `NOVA_FETCH_PORT` | `8080` | Host port in Docker Compose. |
| `MAX_ACTIVE_JOBS` | `2` | Worker concurrency. Keep one Uvicorn worker because the job queue is process-local. |
| `MAX_QUEUED_JOBS` | `12` | Maximum queued/active jobs. |
| `MAX_DOWNLOAD_GB` | `8` | Per-file size cap. |
| `DATA_DIR` | `./data` / `/data` | SQLite and media storage location. |

## API

All API routes require the `X-API-Key` header when `APP_API_KEY` is configured.

| Method | Path | Function |
|---|---|---|
| GET | `/api/health` | Health check |
| GET | `/api/config` | Installed tool and provider status |
| GET | `/api/preview?url=...` | Metadata and available video heights |
| POST | `/api/downloads` | Add a download job |
| GET | `/api/downloads` | Recent job history |
| GET | `/api/downloads/{id}` | Job details |
| POST | `/api/downloads/{id}/cancel` | Cancel a job |
| GET | `/api/downloads/{id}/file` | Retrieve completed output |
| DELETE | `/api/downloads/{id}` | Remove completed/failed history and files |
| GET | `/api/docs` | Interactive API documentation |

Example request:

```json
{
  "url": "https://example.com/public-video",
  "mode": "video",
  "quality": "1080",
  "container": "mp4",
  "provider": "auto",
  "playlist": false,
  "subtitles": true,
  "subtitle_lang": "en",
  "embed_metadata": true,
  "embed_thumbnail": false,
  "rights_confirmed": true
}
```

Provider values: `auto`, `yt-dlp`, `cobalt`. Auto tries yt-dlp first and then the configured Cobalt instance if yt-dlp fails.

## Supported sites and expectations

See yt-dlp's maintained [supported-sites list](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md) and Cobalt's [supported-service list](https://github.com/imputnet/cobalt/blob/main/api/README.md). Extractor coverage is not a guarantee that every individual media item will work: site APIs, players, access conditions and terms change. No app-only or private-app access is promised.

NOVA FETCH does not import browser cookies or ask for service passwords. It does not decrypt DRM or circumvent paywalls, private accounts or source restrictions. Save only media you created, have permission to download, or are otherwise legally entitled to store, and follow the source's terms.

## Security and deployment

- Docker publishes only to `127.0.0.1` by default. Do not expose an unauthenticated instance to the internet.
- For remote use, keep `APP_API_KEY` enabled and use HTTPS, an authenticated reverse proxy, outbound firewall/egress restrictions and sensible quotas.
- URL checks block non-public IPs as a defense-in-depth SSRF measure. DNS can change between validation and network connection; network-level egress filtering is still needed for an internet-facing service.
- One Uvicorn worker is configured because the in-memory job queue is process-local.
- This v1 is intended for a trusted, self-hosted user. A public multi-user service needs user accounts, isolated workers, per-user quotas and stronger abuse controls.

## Tests

```bash
pip install -r requirements-dev.txt
python -m compileall -q server tests
pytest -q
```

GitHub Actions runs syntax and unit tests on pushes and pull requests.

## Third-party acknowledgements

NOVA FETCH integrates with independent open-source tools. Their source code is not copied into this repository.

- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — GPL-3.0-or-later.
- [FFmpeg](https://ffmpeg.org/) — LGPL/GPL depending on build configuration.
- [Cobalt](https://github.com/imputnet/cobalt) — AGPL-3.0; called via its documented API when an authorized instance is configured.
- [VidBee](https://github.com/nexmoe/VidBee) — independent app built around yt-dlp and FFmpeg; no VidBee code is copied here.

Check every dependency's current license and the license of any binary you distribute. The repository's license applies only to NOVA FETCH's original UI and integration code, not third-party components.

## License

See [LICENSE](LICENSE) and the licenses of all integrated tools.
