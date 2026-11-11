"""Example NOVA FETCH extractor module.

Copy this file into an importable Python package and replace the example host
with a source you are authorized to integrate.
"""
from urllib.parse import urlsplit

from server.extractors.registry import ExtractorRegistry
from server.security import validate_external_url


class ExamplePublicMediaExtractor:
    name = "example-public-media"
    priority = 50

    def supports(self, url: str) -> bool:
        parsed = urlsplit(url)
        return parsed.scheme in {"http", "https"} and parsed.hostname == "media.example.test"

    def extract(self, url: str):
        safe_url = validate_external_url(url)
        return {
            "media_url": safe_url,
            "title": "Example public media",
            "creator": "Example publisher",
            "thumbnail": None,
            "source": "NOVA adapter · example-public-media",
        }


def register(registry: ExtractorRegistry) -> None:
    registry.register(ExamplePublicMediaExtractor())
