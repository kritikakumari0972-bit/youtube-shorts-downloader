from __future__ import annotations

import importlib
import os
import pkgutil
import threading
from collections import deque
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit


class Extractor(Protocol):
    """Contract for a first-party or locally installed NOVA extractor."""

    name: str
    priority: int

    def supports(self, url: str) -> bool:
        """Return whether this adapter can inspect the URL."""

    def extract(self, url: str) -> dict[str, Any] | None:
        """Return a media candidate, or None when the adapter does not match."""


@dataclass(frozen=True)
class ExtractorMatch:
    extractor: str
    result: dict[str, Any]


class ExtractorRegistry:
    """Ordered extractor registry with deterministic priority."""

    def __init__(self) -> None:
        self._extractors: list[Extractor] = []
        self._names: set[str] = set()
        self._lock = threading.RLock()
        self._recent_failures = deque(maxlen=25)

    def register(self, extractor: Extractor) -> None:
        name = str(getattr(extractor, "name", "")).strip()
        if not name:
            raise ValueError("Every extractor must have a non-empty name.")
        if not callable(getattr(extractor, "supports", None)):
            raise TypeError(f"Extractor '{name}' must implement supports(url).")
        if not callable(getattr(extractor, "extract", None)):
            raise TypeError(f"Extractor '{name}' must implement extract(url).")
        try:
            int(getattr(extractor, "priority", 100))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Extractor '{name}' priority must be an integer.") from exc
        with self._lock:
            if name in self._names:
                raise ValueError(f"Extractor '{name}' is already registered.")
            self._names.add(name)
            self._extractors.append(extractor)
            self._extractors.sort(
                key=lambda item: int(getattr(item, "priority", 100)),
                reverse=True,
            )

    def names(self) -> list[str]:
        with self._lock:
            return [str(item.name) for item in self._extractors]

    def diagnostics(self) -> dict[str, Any]:
        """Return safe adapter diagnostics without exposing source URLs or tokens."""
        with self._lock:
            return {
                "registered": [str(item.name) for item in self._extractors],
                "recent_failures": list(self._recent_failures),
                "failure_history_limit": self._recent_failures.maxlen,
            }

    def extract(self, url: str) -> ExtractorMatch | None:
        # Avoid passing non-web schemes or malformed hosts to plugins.
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return None
        with self._lock:
            adapters = tuple(self._extractors)
        for adapter in adapters:
            try:
                if not adapter.supports(url):
                    continue
                result = adapter.extract(url)
                if result and result.get("media_url"):
                    return ExtractorMatch(str(adapter.name), result)
            except Exception as exc:
                # One adapter failing must not disable other independently
                # registered adapters. Do not retain the URL or exception text:
                # either may contain user-supplied tokens or signed query values.
                with self._lock:
                    self._recent_failures.append({
                        "extractor": str(getattr(adapter, "name", "unknown")),
                        "error_type": type(exc).__name__,
                    })
                continue
        return None


class DirectMediaExtractor:
    name = "nova-direct"
    priority = 200

    def supports(self, url: str) -> bool:
        from ..native_engine import is_native_url
        return is_native_url(url)

    def extract(self, url: str) -> dict[str, Any] | None:
        from ..native_engine import preview
        metadata = preview(url)
        return {
            "media_url": url,
            "title": metadata["title"],
            "creator": metadata["creator"],
            "thumbnail": metadata["thumbnail"],
            "source": "NOVA Native · direct media",
            "metadata": metadata,
        }


_registry: ExtractorRegistry | None = None
_registry_lock = threading.Lock()


def get_registry(reload_plugins: bool = False) -> ExtractorRegistry:
    global _registry
    with _registry_lock:
        if _registry is not None and not reload_plugins:
            return _registry
        registry = ExtractorRegistry()
        registry.register(DirectMediaExtractor())
        # Built-in and user-maintained adapters live under this package.
        package_name = "server.extractors.plugins"
        try:
            package = importlib.import_module(package_name)
            for module_info in pkgutil.iter_modules(package.__path__):
                module = importlib.import_module(f"{package_name}.{module_info.name}")
                register = getattr(module, "register", None)
                if callable(register):
                    register(registry)
        except ModuleNotFoundError:
            pass

        # Optional Python modules may be installed beside NOVA FETCH. They must
        # expose register(registry); comma-separated import paths are explicit.
        modules = [part.strip() for part in os.getenv("NOVA_EXTRACTOR_MODULES", "").split(",") if part.strip()]
        for module_name in modules:
            module = importlib.import_module(module_name)
            register = getattr(module, "register", None)
            if not callable(register):
                raise RuntimeError(f"Extractor module '{module_name}' has no register(registry) function.")
            register(registry)

        _registry = registry
        return _registry
