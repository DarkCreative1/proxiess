from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock

import aiohttp

from proxypulse.checker import ProxyChecker, calculate_score, extract_exit_ip
from proxypulse.models import CheckConfig, ProxyProtocol, ProxyRecord, ProxyStatus


class CheckerPureTests(unittest.TestCase):
    def test_exit_ip_plain_and_json(self) -> None:
        self.assertEqual("8.8.8.8", extract_exit_ip("8.8.8.8\n"))
        self.assertEqual("1.1.1.1", extract_exit_ip('{"ip":"1.1.1.1"}'))

    def test_exit_ip_rejects_invalid_body(self) -> None:
        with self.assertRaises(ValueError):
            extract_exit_ip("<html>not an ip</html>")

    def test_score_decreases_with_latency(self) -> None:
        fast = ProxyRecord("8.8.8.8", 80, status=ProxyStatus.ALIVE, latency_ms=200, success_count=1)
        slow = ProxyRecord("1.1.1.1", 80, status=ProxyStatus.ALIVE, latency_ms=3000, success_count=1)
        self.assertGreater(calculate_score(fast), calculate_score(slow))


class CheckerAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_check_one_updates_alive_record(self) -> None:
        checker = ProxyChecker(CheckConfig(attempts=1))
        checker._probe_http = AsyncMock(return_value=("1.1.1.1", 123.4))  # type: ignore[method-assign]
        proxy = ProxyRecord("8.8.8.8", 8080, ProxyProtocol.HTTP)
        session = AsyncMock(spec=aiohttp.ClientSession)
        result = await checker.check_one(proxy, session, direct_ip="9.9.9.9")
        self.assertIs(result.status, ProxyStatus.ALIVE)
        self.assertEqual(123.4, result.latency_ms)
        self.assertTrue(result.hides_ip)
        self.assertGreater(result.score, 0)

    async def test_check_many_honors_concurrency(self) -> None:
        checker = ProxyChecker(CheckConfig(concurrency=3, attempts=1))
        active = 0
        peak = 0
        lock = asyncio.Lock()

        async def fake_check(proxy: ProxyRecord, _session: aiohttp.ClientSession, _direct: str = "") -> ProxyRecord:
            nonlocal active, peak
            async with lock:
                active += 1
                peak = max(peak, active)
            await asyncio.sleep(0.01)
            proxy.status = ProxyStatus.ALIVE
            proxy.latency_ms = 10
            async with lock:
                active -= 1
            return proxy

        checker.check_one = fake_check  # type: ignore[method-assign]
        records = [ProxyRecord(f"8.8.8.{index}", 8000 + index) for index in range(1, 8)]
        await checker.check_many(records)
        self.assertLessEqual(peak, 3)


if __name__ == "__main__":
    unittest.main()

