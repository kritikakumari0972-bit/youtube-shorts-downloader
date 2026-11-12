from __future__ import annotations

from typing import Any

from ..registry import ExtractorRegistry


class PublicHTML5Extractor:
    """Extracts media directly exposed in public HTML5/Open Graph/JSON-LD markup."""

    name = "nova-public-html5"
    priority = 100

    def supports(self, url: str) -> bool:
        from ...native_engine import is_native_url
        return not is_native_url(url)

    def extract(self, url: str) -> dict[str, Any] | None:
        from ...native_engine import resolve_html5_media
        return resolve_html5_media(url)


def register(registry: ExtractorRegistry) -> None:
    registry.register(PublicHTML5Extractor())
