"""Domain models shared by collectors, checker, storage and GUI."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class ProxyProtocol(StrEnum):
    HTTP = "http"
    HTTPS = "https"
    SOCKS4 = "socks4"
    SOCKS5 = "socks5"

    @classmethod
    def from_value(cls, value: str | None, default: "ProxyProtocol" | None = None) -> "ProxyProtocol":
        normalized = (value or "").strip().lower().replace("socks5h", "socks5")
        aliases = {"ssl": "https", "socks": "socks5"}
        normalized = aliases.get(normalized, normalized)
        try:
            return cls(normalized)
        except ValueError:
            if default is not None:
                return default
            raise


class ProxyStatus(StrEnum):
    NEW = "new"
    TESTING = "testing"
    ALIVE = "alive"
    DEAD = "dead"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class ProxyRecord:
    host: str
    port: int
    protocol: ProxyProtocol = ProxyProtocol.HTTP
    sources: set[str] = field(default_factory=set)
    country: str = ""
    country_code: str = ""
    city: str = ""
    anonymity: str = "unknown"
    advertised_ssl: bool | None = None
    advertised_uptime: float | None = None
    advertised_latency_ms: float | None = None
    status: ProxyStatus = ProxyStatus.NEW
    latency_ms: float | None = None
    exit_ip: str = ""
    hides_ip: bool | None = None
    score: int = 0
    error: str = ""
    tested_at: str = ""
    success_count: int = 0
    failure_count: int = 0

    @property
    def key(self) -> tuple[str, str, int]:
        return self.protocol.value, self.host.lower(), self.port

    @property
    def endpoint(self) -> str:
        host = f"[{self.host}]" if ":" in self.host and not self.host.startswith("[") else self.host
        return f"{host}:{self.port}"

    @property
    def url(self) -> str:
        # Public lists commonly label CONNECT-capable HTTP proxies as HTTPS.
        # They are still reached over an HTTP proxy transport.
        scheme = "http" if self.protocol is ProxyProtocol.HTTPS else self.protocol.value
        return f"{scheme}://{self.endpoint}"

    @property
    def source_text(self) -> str:
        return ", ".join(sorted(self.sources))

    @property
    def speed_label(self) -> str:
        if self.latency_ms is None:
            return "—"
        if self.latency_ms <= 500:
            return "Çok hızlı"
        if self.latency_ms <= 1000:
            return "Hızlı"
        if self.latency_ms <= 2000:
            return "Orta"
        return "Yavaş"

    def merge(self, other: "ProxyRecord") -> None:
        """Merge richer source metadata without overwriting measured fields."""
        self.sources.update(other.sources)
        for field_name in ("country", "country_code", "city"):
            if not getattr(self, field_name) and getattr(other, field_name):
                setattr(self, field_name, getattr(other, field_name))
        if self.anonymity in {"", "unknown"} and other.anonymity not in {"", "unknown"}:
            self.anonymity = other.anonymity
        if self.advertised_ssl is None:
            self.advertised_ssl = other.advertised_ssl
        if self.advertised_uptime is None:
            self.advertised_uptime = other.advertised_uptime
        if self.advertised_latency_ms is None:
            self.advertised_latency_ms = other.advertised_latency_ms

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["protocol"] = self.protocol.value
        data["status"] = self.status.value
        data["sources"] = sorted(self.sources)
        return data


@dataclass(frozen=True, slots=True)
class ProxySource:
    name: str
    url: str
    format: str = "auto"
    protocol_hint: ProxyProtocol | None = None
    enabled: bool = True
    attribution_url: str = ""


@dataclass(slots=True)
class SourceResult:
    source: ProxySource
    proxies: list[ProxyRecord] = field(default_factory=list)
    elapsed_ms: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


@dataclass(slots=True)
class CheckConfig:
    test_url: str = "https://api.iplocate.io/ip"
    fallback_url: str = "https://api.ipify.org?format=json"
    timeout_seconds: float = 2.6
    concurrency: int = 850
    attempts: int = 1
    verify_tls: bool = True

    def normalized(self) -> "CheckConfig":
        return CheckConfig(
            test_url=self.test_url.strip(),
            fallback_url=self.fallback_url.strip(),
            timeout_seconds=max(1.0, min(float(self.timeout_seconds), 60.0)),
            concurrency=max(1, min(int(self.concurrency), 1000)),
            attempts=max(1, min(int(self.attempts), 3)),
            verify_tls=bool(self.verify_tls),
        )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

