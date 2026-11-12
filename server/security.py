from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit


class UnsafeURLError(ValueError):
    """Raised when a submitted URL is not safe for server-side fetching."""


def validate_external_url(value: str, trusted_hosts: set[str] | None = None) -> str:
    """Accept HTTP(S) URLs pointing to public IPs or explicitly trusted hosts.

    This is defense-in-depth against SSRF. Deployments should also apply an
    outbound network policy because DNS can change between validation and use.
    """
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise UnsafeURLError("Enter a valid public media URL.")
    try:
        parsed = urlsplit(value.strip())
        if parsed.scheme.lower() not in {"http", "https"}:
            raise UnsafeURLError("Only http:// and https:// links are supported.")
        if not parsed.hostname or parsed.username or parsed.password:
            raise UnsafeURLError("The URL is invalid or contains credentials.")
        host = parsed.hostname.rstrip(".").lower()
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError as exc:
        if isinstance(exc, UnsafeURLError):
            raise
        raise UnsafeURLError("The URL is malformed.") from exc

    trusted = {item.rstrip(".").lower() for item in (trusted_hosts or set()) if item}
    if host in trusted:
        return value.strip()
    try:
        literal = ipaddress.ip_address(host)
        addresses = [literal]
    except ValueError:
        try:
            records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            addresses = [ipaddress.ip_address(record[4][0].split("%", 1)[0]) for record in records]
        except (OSError, ValueError) as exc:
            raise UnsafeURLError("The host could not be resolved to a public address.") from exc

    if not addresses or any(not address.is_global for address in addresses):
        raise UnsafeURLError("Local, private, reserved, and non-public network addresses are blocked.")
    return value.strip()


def safe_filename(value: str, fallback: str = "media-download", limit: int = 120) -> str:
    cleaned = "".join(ch for ch in (value or "") if ch.isalnum() or ch in " ._-()[]")
    cleaned = " ".join(cleaned.split()).strip(" .")
    return (cleaned or fallback)[:limit]
