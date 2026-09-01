"""Diagnostic command line entry points used by tests and operators."""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import tkinter as tk
from pathlib import Path

from .checker import ProxyChecker
from .database import ProxyRepository
from .gui import ProxyPulseApp
from .models import CheckConfig, ProxyStatus
from .sources import DEFAULT_SOURCES, SourceCollector


async def _collect(limit: int, as_json: bool) -> int:
    collector = SourceCollector()
    proxies, results = await collector.collect(DEFAULT_SOURCES)
    selected = proxies[: max(0, limit)]
    if as_json:
        print(json.dumps([proxy.to_dict() for proxy in selected], ensure_ascii=False, indent=2))
    else:
        print(f"SOURCE_OK={sum(result.ok for result in results)}/{len(results)}")
        print(f"COLLECTED={len(proxies)}")
        for proxy in selected:
            print(f"{proxy.protocol.value}://{proxy.endpoint}")
    return 0 if proxies else 2


async def _live_check(limit: int, timeout: float) -> int:
    collector = SourceCollector()
    proxies, _ = await collector.collect(DEFAULT_SOURCES)
    sample = sorted(proxies, key=lambda proxy: proxy.advertised_latency_ms or 99_999)[:limit]
    checker = ProxyChecker(CheckConfig(timeout_seconds=timeout, concurrency=min(20, max(1, limit))))
    direct_ip = await checker.discover_direct_ip()
    checked = await checker.check_many(sample, direct_ip=direct_ip)
    alive = [proxy for proxy in checked if proxy.status is ProxyStatus.ALIVE]
    print(f"DIRECT_IP={'OK' if direct_ip else 'UNKNOWN'}")
    print(f"CHECKED={len(checked)} ALIVE={len(alive)}")
    for proxy in alive:
        print(f"ALIVE {proxy.url} {proxy.latency_ms:.0f}ms score={proxy.score}")
    # A live public pool can legitimately have zero survivors; diagnostics still ran.
    return 0


def _gui_smoke() -> int:
    with tempfile.TemporaryDirectory(prefix="proxypulse-gui-") as directory:
        root = tk.Tk()
        root.withdraw()
        repository = ProxyRepository(Path(directory) / "smoke.db")
        ProxyPulseApp(root, repository)
        root.update_idletasks()
        root.update()
        root.destroy()
    print("GUI_SMOKE_OK")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ProxyPulse", description="ProxyPulse tanılama aracı")
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect = subparsers.add_parser("collect", help="Etkin kaynaklardan güncel proxyleri getir")
    collect.add_argument("--limit", type=int, default=10)
    collect.add_argument("--json", action="store_true")
    check = subparsers.add_parser("live-check", help="Küçük bir canlı örneği doğrula")
    check.add_argument("--limit", type=int, default=5)
    check.add_argument("--timeout", type=float, default=5.0)
    subparsers.add_parser("gui-smoke", help="GUI'yi görünmeden oluşturup kapat")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.command == "collect":
        return asyncio.run(_collect(arguments.limit, arguments.json))
    if arguments.command == "live-check":
        return asyncio.run(_live_check(arguments.limit, arguments.timeout))
    if arguments.command == "gui-smoke":
        return _gui_smoke()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

