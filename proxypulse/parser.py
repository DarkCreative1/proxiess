"""Strict parsers for plain-text and JSON proxy feeds."""

from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

from .models import ProxyProtocol, ProxyRecord


_ENDPOINT_RE = re.compile(
    r"(?:(?P<scheme>https?|socks4|socks5|socks5h)://)?"
    r"(?P<host>\[[0-9a-fA-F:]+\]|(?:\d{1,3}\.){3}\d{1,3})"
    r":(?P<port>\d{1,5})",
    re.IGNORECASE,
)


def _as_float(value: Any) -> float | None:
    if value in (None, "", "null"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "ssl", "https"}:
        return True
    if normalized in {"0", "false", "no", "n"}:
        return False
    return None


def valid_public_host(host: str) -> bool:
    """Accept only globally routable literal IP addresses from public feeds."""
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return bool(address.is_global)


def make_proxy(
    host: str,
    port: int | str,
    protocol: str | ProxyProtocol | None,
    source_name: str,
    protocol_hint: ProxyProtocol | None = None,
    **metadata: Any,
) -> ProxyRecord | None:
    host = str(host).strip().strip("[]")
    try:
        port_int = int(port)
    except (TypeError, ValueError):
        return None
    if not (1 <= port_int <= 65535) or not valid_public_host(host):
        return None
    try:
        default_protocol = protocol_hint or ProxyProtocol.HTTP
        proto = protocol if isinstance(protocol, ProxyProtocol) else ProxyProtocol.from_value(protocol, default_protocol)
    except ValueError:
        return None
    if proto is None:
        proto = ProxyProtocol.HTTP
    return ProxyRecord(
        host=host,
        port=port_int,
        protocol=proto,
        sources={source_name},
        country=str(metadata.get("country") or metadata.get("country_name") or "").strip(),
        country_code=str(metadata.get("country_code") or metadata.get("countryCode") or "").strip().upper(),
        city=str(metadata.get("city") or "").strip(),
        anonymity=str(metadata.get("anonymity") or metadata.get("anonymity_level") or "unknown").strip().lower(),
        advertised_ssl=_as_bool(metadata.get("ssl", metadata.get("https"))),
        advertised_uptime=_as_float(metadata.get("uptime_percent", metadata.get("uptime"))),
        advertised_latency_ms=_as_float(metadata.get("latency_ms", metadata.get("latency", metadata.get("speed")))),
    )


def parse_text(text: str, source_name: str, protocol_hint: ProxyProtocol | None = None) -> list[ProxyRecord]:
    results: list[ProxyRecord] = []
    for match in _ENDPOINT_RE.finditer(text):
        scheme = match.group("scheme")
        record = make_proxy(
            match.group("host"),
            match.group("port"),
            scheme,
            source_name,
            protocol_hint=protocol_hint,
        )
        if record:
            results.append(record)
    return deduplicate(results)


def _candidate_items(payload: Any) -> Iterable[Any]:
    if isinstance(payload, list):
        yield from payload
        return
    if isinstance(payload, dict):
        for key in ("data", "proxies", "results", "items"):
            if isinstance(payload.get(key), list):
                yield from payload[key]
                return
        # A single proxy object is a valid payload too.
        if any(key in payload for key in ("ip", "host", "proxy", "url")):
            yield payload


def _protocol_values(item: dict[str, Any], hint: ProxyProtocol | None) -> list[str | ProxyProtocol | None]:
    value = item.get("protocol", item.get("type", item.get("scheme")))
    if value is None:
        value = item.get("protocols")
    if isinstance(value, list):
        return value or [hint]
    if isinstance(value, str) and "," in value:
        return [part.strip() for part in value.split(",")]
    return [value or hint]


def parse_json(text: str, source_name: str, protocol_hint: ProxyProtocol | None = None) -> list[ProxyRecord]:
    payload = json.loads(text)
    results: list[ProxyRecord] = []
    for item in _candidate_items(payload):
        if isinstance(item, str):
            results.extend(parse_text(item, source_name, protocol_hint))
            continue
        if not isinstance(item, dict):
            continue
        host = item.get("ip", item.get("host", item.get("address")))
        port = item.get("port")
        embedded = item.get("proxy", item.get("url"))
        embedded_scheme: str | None = None
        if (not host or not port) and embedded:
            try:
                parsed = urlsplit(str(embedded) if "://" in str(embedded) else f"//{embedded}")
                host, port = parsed.hostname, parsed.port
                embedded_scheme = parsed.scheme or None
            except ValueError:
                continue
        if not host or not port:
            continue
        metadata = dict(item)
        ip_data = item.get("ip_data")
        if isinstance(ip_data, dict):
            metadata.setdefault("country", ip_data.get("country"))
            metadata.setdefault("country_code", ip_data.get("countryCode"))
            metadata.setdefault("city", ip_data.get("city"))
        metadata.setdefault("latency_ms", item.get("average_timeout", item.get("timeout")))
        extra_metadata = {
            key: value
            for key, value in metadata.items()
            if key not in {"host", "ip", "address", "port", "protocol", "type", "scheme", "protocols"}
        }
        protocols = _protocol_values(item, protocol_hint)
        if embedded_scheme and protocols == [protocol_hint]:
            protocols = [embedded_scheme]
        for protocol in protocols:
            record = make_proxy(host, port, protocol, source_name, protocol_hint=protocol_hint, **extra_metadata)
            if record:
                results.append(record)
    return deduplicate(results)


def parse_feed(
    content: str,
    source_name: str,
    feed_format: str = "auto",
    protocol_hint: ProxyProtocol | None = None,
) -> list[ProxyRecord]:
    fmt = feed_format.strip().lower()
    if fmt == "json" or (fmt == "auto" and content.lstrip().startswith(("[", "{"))):
        try:
            return parse_json(content, source_name, protocol_hint)
        except (json.JSONDecodeError, TypeError):
            if fmt == "json":
                raise
    return parse_text(content, source_name, protocol_hint)


def deduplicate(records: Iterable[ProxyRecord]) -> list[ProxyRecord]:
    merged: dict[tuple[str, str, int], ProxyRecord] = {}
    for record in records:
        current = merged.get(record.key)
        if current is None:
            merged[record.key] = record
        else:
            current.merge(record)
    return list(merged.values())
