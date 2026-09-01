"""Headless GitHub Actions runner for collecting and publishing live proxies."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from proxypulse.checker import ProxyChecker
from proxypulse.models import CheckConfig, ProxyProtocol, ProxyRecord, ProxyStatus
from proxypulse.sources import DEFAULT_SOURCES, SourceCollector


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("değer 1 veya daha büyük olmalı")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ProxyPulse GitHub Actions tarayıcısı")
    parser.add_argument("--max-checks", type=positive_int, default=int(os.getenv("MAX_CHECKS", "10000")))
    parser.add_argument("--concurrency", type=positive_int, default=int(os.getenv("CONCURRENCY", "500")))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("PROXY_TIMEOUT", "3.0")))
    parser.add_argument("--output", type=Path, default=Path(os.getenv("OUTPUT_DIR", "results")))
    return parser


def priority(record: ProxyRecord) -> tuple[float, float, str]:
    latency = record.advertised_latency_ms if record.advertised_latency_ms is not None else 99_999.0
    uptime = -(record.advertised_uptime if record.advertised_uptime is not None else 0.0)
    return latency, uptime, record.endpoint


def write_outputs(output: Path, alive: list[ProxyRecord]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    ordered = sorted(alive, key=lambda item: (item.latency_ms or 99_999, -item.score, item.url))

    def save(name: str, records: list[ProxyRecord]) -> None:
        # İstenen biçim: tek satır ve virgülle ayrılmış tam proxy URL'leri.
        (output / name).write_text(",".join(item.url for item in records), encoding="utf-8")

    save("proxies.txt", ordered)
    for protocol in ProxyProtocol:
        save(f"{protocol.value}.txt", [item for item in ordered if item.protocol is protocol])

    details = {
        "count": len(ordered),
        "proxies": [item.to_dict() for item in ordered],
    }
    (output / "proxies.json").write_text(
        json.dumps(details, ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def run(args: argparse.Namespace) -> int:
    print(f"{len(DEFAULT_SOURCES)} kaynaktan proxyler çekiliyor…", flush=True)
    records, source_results = await SourceCollector().collect(DEFAULT_SOURCES)
    source_ok = sum(result.ok for result in source_results)
    print(f"Kaynak: {source_ok}/{len(source_results)} başarılı; {len(records)} benzersiz proxy.", flush=True)
    if not records:
        raise RuntimeError("Hiç proxy toplanamadı; mevcut yayın dosyaları korunuyor.")

    selected = sorted(records, key=priority)[: args.max_checks]
    config = CheckConfig(
        timeout_seconds=args.timeout,
        concurrency=args.concurrency,
        attempts=1,
    )
    checker = ProxyChecker(config)
    direct_ip = await checker.discover_direct_ip()
    print(f"{len(selected)} proxy kontrol ediliyor (concurrency={config.normalized().concurrency})…", flush=True)

    def progress(_: ProxyRecord, completed: int, total: int) -> None:
        if completed == total or completed % 1000 == 0:
            print(f"İlerleme: {completed}/{total}", flush=True)

    checked = await checker.check_many(selected, direct_ip=direct_ip, on_result=progress)
    alive = [record for record in checked if record.status is ProxyStatus.ALIVE]
    if not alive:
        raise RuntimeError("Çalışan proxy bulunamadı; mevcut yayın dosyaları korunuyor.")
    write_outputs(args.output, alive)
    print(f"Tamamlandı: {len(alive)} çalışan proxy {args.output} klasörüne yazıldı.", flush=True)
    return 0


def main() -> int:
    return asyncio.run(run(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
