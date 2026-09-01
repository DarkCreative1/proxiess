"""Atomic TXT and CSV exporters."""

from __future__ import annotations

import csv
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path

from .models import ProxyRecord


CSV_FIELDS = (
    "proxy",
    "protocol",
    "status",
    "latency_ms",
    "score",
    "exit_ip",
    "hides_ip",
    "country_code",
    "country",
    "anonymity",
    "tested_at",
    "sources",
    "error",
)


def _atomic_target(path: str | Path) -> tuple[Path, Path]:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    os.close(handle)
    return target, Path(temp_name)


def _export_scheme(proxy: ProxyRecord) -> str:
    # proxy.url https için http döndürür (transport), dışa aktarımda gerçek protokolü yaz
    return f"{proxy.protocol.value}://{proxy.endpoint}"


def export_txt(records: Iterable[ProxyRecord], path: str | Path) -> int:
    rows = list(records)
    target, temporary = _atomic_target(path)
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            for proxy in rows:
                stream.write(f"{_export_scheme(proxy)}\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return len(rows)


def export_csv(records: Iterable[ProxyRecord], path: str | Path) -> int:
    rows = list(records)
    target, temporary = _atomic_target(path)
    try:
        with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
            writer.writeheader()
            for proxy in rows:
                writer.writerow(
                    {
                        "proxy": proxy.endpoint,
                        "protocol": proxy.protocol.value,
                        "status": proxy.status.value,
                        "latency_ms": "" if proxy.latency_ms is None else proxy.latency_ms,
                        "score": proxy.score,
                        "exit_ip": proxy.exit_ip,
                        "hides_ip": "" if proxy.hides_ip is None else proxy.hides_ip,
                        "country_code": proxy.country_code,
                        "country": proxy.country,
                        "anonymity": proxy.anonymity,
                        "tested_at": proxy.tested_at,
                        "sources": proxy.source_text,
                        "error": proxy.error,
                    }
                )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return len(rows)

