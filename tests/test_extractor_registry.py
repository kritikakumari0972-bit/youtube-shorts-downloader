import pytest

from server.extractors.registry import ExtractorRegistry, get_registry


class ExampleExtractor:
    name = "example"
    priority = 5

    def supports(self, url):
        return "example.test" in url

    def extract(self, url):
        return {"media_url": "https://cdn.example.test/video.mp4"}


class BrokenExtractor:
    name = "broken"
    priority = 100

    def supports(self, url):
        return True

    def extract(self, url):
        raise RuntimeError("adapter bug")


def test_registry_registers_and_extracts():
    registry = ExtractorRegistry()
    registry.register(ExampleExtractor())
    match = registry.extract("https://example.test/watch/1")
    assert match is not None
    assert match.extractor == "example"
    assert match.result["media_url"].endswith("video.mp4")


def test_registry_rejects_duplicate_names():
    registry = ExtractorRegistry()
    registry.register(ExampleExtractor())
    with pytest.raises(ValueError, match="already registered"):
        registry.register(ExampleExtractor())


def test_failed_adapter_does_not_block_later_adapter():
    registry = ExtractorRegistry()
    registry.register(BrokenExtractor())
    registry.register(ExampleExtractor())
    match = registry.extract("https://example.test/watch/1")
    assert match is not None
    assert match.extractor == "example"
    diagnostics = registry.diagnostics()
    assert diagnostics["recent_failures"][-1] == {
        "extractor": "broken",
        "error_type": "RuntimeError",
    }
    assert "adapter bug" not in str(diagnostics)


def test_registry_rejects_incomplete_adapter_contract():
    class IncompleteExtractor:
        name = "incomplete"
        priority = 1

        def extract(self, url):
            return None

    with pytest.raises(TypeError, match="supports"):
        ExtractorRegistry().register(IncompleteExtractor())


def test_registry_diagnostics_limit_failure_history():
    class AlwaysBroken:
        priority = 1

        def supports(self, url):
            return True

        def extract(self, url):
            raise RuntimeError("not retained")

    registry = ExtractorRegistry()
    for index in range(30):
        registry.register(type(
            f"Broken{index}",
            (),
            {
                "name": f"broken-{index}",
                "priority": 30-index,
                "supports": AlwaysBroken.supports,
                "extract": AlwaysBroken.extract,
            },
        )())
    registry.extract("https://example.test/watch/1")
    diagnostics = registry.diagnostics()
    assert len(diagnostics["recent_failures"]) == 25
    assert diagnostics["failure_history_limit"] == 25


def test_default_registry_contains_first_party_adapters():
    names = get_registry(reload_plugins=True).names()
    assert "nova-direct" in names
    assert "nova-public-html5" in names
    assert "nova-peertube" in names
