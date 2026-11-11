"""First-party NOVA FETCH extractor SDK.

Extractors are local Python adapters. They never call a public downloader
service; each adapter may inspect a public source URL and return a media URL.
"""
from .registry import Extractor, ExtractorRegistry, get_registry

__all__ = ["Extractor", "ExtractorRegistry", "get_registry"]
