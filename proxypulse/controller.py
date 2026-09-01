"""Background scan controller and thread-safe GUI event bridge."""

from __future__ import annotations

import asyncio
import queue
import threading
import traceback
from dataclasses import dataclass
from typing import Any

from .checker import ProxyChecker
from .database import ProxyRepository
from .models import CheckConfig, ProxyRecord, ProxySource, ProxyStatus
from .sources import DEFAULT_SOURCES, SourceCollector


@dataclass(frozen=True, slots=True)
class UiEvent:
    run_id: int
    kind: str
    payload: Any = None


class ScanController:
    def __init__(self, repository: ProxyRepository, event_queue: "queue.Queue[UiEvent]") -> None:
        self.repository = repository
        self.events = event_queue
        self._lock = threading.Lock()
        self._run_id = 0
        self._thread: threading.Thread | None = None
        self._cancel_event = threading.Event()

    @property
    def active_run_id(self) -> int:
        with self._lock:
            return self._run_id

    @property
    def running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive())

    def _emit(self, run_id: int, kind: str, payload: Any = None) -> None:
        event = UiEvent(run_id, kind, payload)
        try:
            self.events.put_nowait(event)
        except queue.Full:
            # Keep terminal/error events deliverable under extreme update load.
            if kind in {"done", "error", "cancelled"}:
                try:
                    self.events.get_nowait()
                except queue.Empty:
                    pass
                self.events.put_nowait(event)

    def _start(self, operation: str, **options: Any) -> int | None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return None
            self._run_id += 1
            run_id = self._run_id
            self._cancel_event = threading.Event()
            self._thread = threading.Thread(
                target=self._worker,
                args=(run_id, operation, options),
                name=f"ProxyPulse-{run_id}",
                daemon=True,
            )
            self._thread.start()
            return run_id

    def start_collect(self, sources: list[ProxySource] | None = None) -> int | None:
        return self._start("collect", sources=sources or list(DEFAULT_SOURCES))

    def start_check(self, records: list[ProxyRecord], config: CheckConfig) -> int | None:
        return self._start("check", records=records, config=config)

    def start_pipeline(
        self,
        sources: list[ProxySource],
        config: CheckConfig,
        max_checks: int,
    ) -> int | None:
        return self._start("pipeline", sources=sources, config=config, max_checks=max_checks)

    def cancel(self) -> None:
        self._cancel_event.set()

    def _worker(self, run_id: int, operation: str, options: dict[str, Any]) -> None:
        try:
            asyncio.run(self._run_async(run_id, operation, options))
        except Exception as exc:  # noqa: BLE001 - converted to GUI event at process boundary
            self._emit(
                run_id,
                "error",
                {"message": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()},
            )

    async def _collect(self, run_id: int, sources: list[ProxySource]) -> list[ProxyRecord]:
        self._emit(run_id, "phase", "Kaynaklardan güncel listeler alınıyor…")
        collector = SourceCollector()

        def source_callback(result: Any) -> None:
            self._emit(run_id, "source", result)

        records, results = await collector.collect(sources, source_callback)
        if self._cancel_event.is_set():
            return []
        self.repository.replace_snapshot(records)
        self._emit(
            run_id,
            "collected",
            {
                "records": records,
                "source_count": len(results),
                "source_ok": sum(1 for result in results if result.ok),
            },
        )
        return records

    @staticmethod
    def _priority(record: ProxyRecord) -> tuple[float, float, str]:
        latency = record.advertised_latency_ms if record.advertised_latency_ms is not None else 99_999.0
        uptime = -(record.advertised_uptime if record.advertised_uptime is not None else 0.0)
        return latency, uptime, record.endpoint

    async def _check(
        self,
        run_id: int,
        records: list[ProxyRecord],
        config: CheckConfig,
    ) -> list[ProxyRecord]:
        if not records:
            return []
        checker = ProxyChecker(config)
        self._emit(run_id, "phase", "Doğrudan bağlantı IP'si ölçülüyor…")
        direct_ip = await checker.discover_direct_ip()
        self._emit(run_id, "direct_ip", direct_ip)
        self._emit(run_id, "phase", f"{len(records):,} proxy doğrulanıyor…")

        def result_callback(record: ProxyRecord, completed: int, total: int) -> None:
            self._emit(run_id, "result", {"record": record, "completed": completed, "total": total})

        checked = await checker.check_many(
            records,
            direct_ip=direct_ip,
            cancel_event=self._cancel_event,
            on_result=result_callback,
        )
        self.repository.upsert_many(checked)
        return checked

    async def _run_async(self, run_id: int, operation: str, options: dict[str, Any]) -> None:
        self._emit(run_id, "started", operation)
        records: list[ProxyRecord] = []
        if operation in {"collect", "pipeline"}:
            records = await self._collect(run_id, options["sources"])
            if operation == "collect" and not self._cancel_event.is_set():
                self._emit(run_id, "done", {"operation": operation, "records": records})
                return
        if self._cancel_event.is_set():
            self._emit(run_id, "cancelled")
            return
        if operation == "check":
            records = options["records"]
        if operation == "pipeline":
            raw_max = int(options["max_checks"])
            # 0 veya negatif = sınırsız : ne kadar bulunursa hepsini test et
            if raw_max and raw_max > 0:
                max_checks = max(1, min(raw_max, 100_000))
                records = sorted(records, key=self._priority)[:max_checks]
            else:
                records = sorted(records, key=self._priority)
            self._emit(run_id, "selection", len(records))
        checked = await self._check(run_id, records, options["config"])
        if self._cancel_event.is_set():
            self._emit(run_id, "cancelled", {"records": checked})
        else:
            self._emit(run_id, "done", {"operation": operation, "records": checked})

