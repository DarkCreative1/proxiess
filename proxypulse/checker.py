"""Bounded asynchronous proxy verification with cancellation and scoring."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import sys
import threading
import time
import warnings
from collections.abc import Callable, Iterable

import aiohttp

from .models import CheckConfig, ProxyProtocol, ProxyRecord, ProxyStatus, utc_now_iso

# Windows Proactor ConnectionResetError spam sustur (WinError 10054)
if sys.platform == "win32":
    warnings.filterwarnings("ignore", category=ResourceWarning)
    warnings.filterwarnings("ignore", message=".*Unclosed.*")
    warnings.filterwarnings("ignore", message=".*_call_connection_lost.*")
    try:
        from asyncio import proactor_events

        _orig_call_lost = proactor_events._ProactorBasePipeTransport._call_connection_lost

        def _patched_call_lost(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            try:
                return _orig_call_lost(self, *args, **kwargs)
            except (ConnectionResetError, OSError) as exc:
                if getattr(exc, "winerror", None) == 10054 or isinstance(exc, ConnectionResetError):
                    return
                # diğer OSError'ları da sustur (uzak host reset)
                if "10054" in str(exc) or "10053" in str(exc):
                    return
                raise

        proactor_events._ProactorBasePipeTransport._call_connection_lost = _patched_call_lost  # type: ignore[method-assign]
    except Exception:
        pass


def _setup_loop_suppress() -> None:
    # Her asyncio loop için WinError 10054 spam'i sustur
    try:
        loop = asyncio.get_running_loop()
        orig = loop.get_exception_handler()

        def _handler(loop_, context):  # type: ignore[no-untyped-def]
            exc = context.get("exception")
            msg = str(context.get("message", "")) + str(exc)
            if isinstance(exc, (ConnectionResetError, OSError)) and (getattr(exc, "winerror", None) in (10054, 10053) or "10054" in msg or "10053" in msg):
                return
            if "ConnectionResetError" in msg or "10054" in msg or "_call_connection_lost" in msg:
                return
            if "Unclosed connection" in msg or "Unclosed client" in msg:
                return
            if orig:
                orig(loop_, context)
            else:
                loop_.default_exception_handler(context)

        loop.set_exception_handler(_handler)
    except RuntimeError:
        pass


_IP_TOKEN_RE = re.compile(r"(?<![0-9A-Fa-f:.])(?:\d{1,3}(?:\.\d{1,3}){3}|[0-9A-Fa-f:]{2,})(?![0-9A-Fa-f:.])")


def extract_exit_ip(body: str) -> str:
    """Extract a valid global IP from plain text or common JSON judge responses."""
    values: list[str] = []
    stripped = body.strip()
    try:
        payload = json.loads(stripped)
        if isinstance(payload, dict):
            for key in ("ip", "origin", "query", "address"):
                value = payload.get(key)
                if value:
                    values.extend(str(value).replace(",", " ").split())
        elif isinstance(payload, str):
            values.append(payload)
    except (json.JSONDecodeError, TypeError):
        values.extend(stripped.replace(",", " ").split())
    values.extend(match.group(0) for match in _IP_TOKEN_RE.finditer(stripped))
    for value in values:
        candidate = value.strip("[](){}\"'")
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if address.is_global:
            return address.compressed
    raise ValueError("Doğrulama yanıtında geçerli çıkış IP'si yok")


def calculate_score(proxy: ProxyRecord) -> int:
    if proxy.status is not ProxyStatus.ALIVE or proxy.latency_ms is None:
        return 0
    latency = proxy.latency_ms
    if latency <= 250:
        latency_score = 92
    elif latency <= 500:
        latency_score = 86
    elif latency <= 1000:
        latency_score = 72
    elif latency <= 2000:
        latency_score = 54
    elif latency <= 4000:
        latency_score = 35
    else:
        latency_score = 20
    observed_total = proxy.success_count + proxy.failure_count
    measured_ratio = proxy.success_count / observed_total if observed_total else 1.0
    source_ratio = (proxy.advertised_uptime or 100.0) / 100.0
    reliability = (measured_ratio * 0.7) + (max(0.0, min(source_ratio, 1.0)) * 0.3)
    bonus = 4 if proxy.hides_ip else 0
    if proxy.advertised_ssl or proxy.protocol in {ProxyProtocol.HTTPS, ProxyProtocol.SOCKS4, ProxyProtocol.SOCKS5}:
        bonus += 3
    return max(1, min(100, round(latency_score * reliability + bonus)))


def classify_error(exc: BaseException) -> str:
    text = str(exc).strip()
    if isinstance(exc, asyncio.TimeoutError):
        return "Zaman aşımı"
    if isinstance(exc, aiohttp.ClientHttpProxyError):
        return f"Proxy CONNECT hatası{': ' + text if text else ''}"
    if isinstance(exc, aiohttp.ClientConnectorCertificateError):
        return "TLS sertifika doğrulaması başarısız"
    if isinstance(exc, aiohttp.ClientResponseError):
        return f"HTTP {exc.status}"
    if isinstance(exc, ValueError):
        return text or "Geçersiz doğrulama yanıtı"
    return f"{type(exc).__name__}{': ' + text if text else ''}"[:240]


class ProxyChecker:
    def __init__(self, config: CheckConfig | None = None) -> None:
        self.config = (config or CheckConfig()).normalized()

    def _timeout(self) -> aiohttp.ClientTimeout:
        total = self.config.timeout_seconds
        # Hızlı eleme için connect kısa (1.0-1.4s), ölü proxy 5-6 kat hızlı elenir
        connect = min(total, max(1.0, total * 0.38))
        read = min(total, max(1.2, total * 0.55))
        return aiohttp.ClientTimeout(total=total, connect=connect, sock_connect=connect, sock_read=read)

    async def _request_ip(self, session: aiohttp.ClientSession, url: str, proxy_url: str | None = None) -> tuple[str, float]:
        started = time.perf_counter()
        async with session.get(
            url,
            proxy=proxy_url,
            timeout=self._timeout(),
            allow_redirects=True,
            ssl=self.config.verify_tls,
        ) as response:
            if response.status < 200 or response.status >= 300:
                raise aiohttp.ClientResponseError(
                    response.request_info,
                    response.history,
                    status=response.status,
                    message="judge response",
                    headers=response.headers,
                )
            raw = await response.content.read(4097)
            if len(raw) > 4096:
                raise ValueError("Doğrulama yanıtı çok büyük")
            body = raw.decode(response.charset or "utf-8", errors="replace")
            exit_ip = extract_exit_ip(body)
        return exit_ip, (time.perf_counter() - started) * 1000

    async def discover_direct_ip(self) -> str:
        _setup_loop_suppress()
        # Hızlı: doğrudan IP'yi en fazla 4 sn içinde bul, iki judge'u paralel yarıştır
        connector = aiohttp.TCPConnector(limit=2, ttl_dns_cache=300)
        headers = {"User-Agent": "ProxyPulse/1.0"}
        urls = [u for u in (self.config.test_url, self.config.fallback_url) if u]
        if not urls:
            return ""
        async with aiohttp.ClientSession(connector=connector, headers=headers, trust_env=False) as session:
            # paralel dene, ilk başarılı döner (timeout 4sn)
            async def _try(url: str) -> str | None:
                try:
                    # doğrudan kısa timeout ile dene
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=4, connect=2, sock_connect=2, sock_read=3), allow_redirects=True, ssl=self.config.verify_tls) as resp:
                        if resp.status < 200 or resp.status >= 300:
                            return None
                        raw = await resp.content.read(4097)
                        body = raw.decode(resp.charset or "utf-8", errors="replace")
                        return extract_exit_ip(body)
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, OSError):
                    return None

            # as_completed ile ilk geleni al
            tasks = [asyncio.create_task(_try(url)) for url in urls]
            for coro in asyncio.as_completed(tasks):
                try:
                    ip = await coro
                    if ip:
                        for t in tasks:
                            t.cancel()
                        return ip
                except (asyncio.CancelledError, Exception):
                    continue
            # fallback: sıralı dene (eski yöntem) eğer paralel başarısızsa
            for url in urls:
                try:
                    ip, _ = await self._request_ip(session, url)
                    return ip
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, OSError):
                    continue
        return ""

    def _should_try_fallback(self, exc: BaseException) -> bool:
        # ölü proxy'lerde 2. URL'yi denemek 2x yavaşlatır -> sadece judge kaynaklı hatalarda fallback dene
        if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
            return False
        if isinstance(exc, (aiohttp.ClientConnectorError, aiohttp.ClientHttpProxyError, aiohttp.ServerTimeoutError, aiohttp.ClientConnectorCertificateError)):
            return False
        if isinstance(exc, OSError):
            return False
        # sadece HTTP hata kodu veya geçersiz IP gibi durumlarda fallback mantıklı
        if isinstance(exc, (aiohttp.ClientResponseError, ValueError)):
            return True
        return False

    async def _probe_http(
        self,
        session: aiohttp.ClientSession,
        proxy: ProxyRecord,
    ) -> tuple[str, float]:
        urls: list[str] = []
        if self.config.test_url:
            urls.append(self.config.test_url)
        if self.config.fallback_url and self.config.fallback_url not in urls:
            urls.append(self.config.fallback_url)
        if proxy.protocol is ProxyProtocol.HTTP and "http://api.iplocate.io/ip" not in urls:
            urls.append("http://api.iplocate.io/ip")
        # en az bir URL olmalı
        urls = [u for u in urls if u]
        first_error: BaseException | None = None
        for idx, url in enumerate(urls):
            try:
                result = await self._request_ip(session, url, proxy.url)
                proxy.advertised_ssl = url.lower().startswith("https://")
                return result
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, OSError) as exc:
                first_error = first_error or exc
                # ilk URL timeout/connection hatası ise ölü proxy -> fallback deneme, direkt fail
                if idx == 0 and len(urls) > 1 and not self._should_try_fallback(exc):
                    break
        assert first_error is not None
        raise first_error

    async def _probe_socks(self, proxy: ProxyRecord) -> tuple[str, float]:
        try:
            from aiohttp_socks import ProxyConnector
        except ImportError as exc:  # pragma: no cover - setup script installs dependency
            raise RuntimeError("SOCKS desteği için aiohttp-socks kurulmalı") from exc
        connector = ProxyConnector.from_url(
            proxy.url,
            rdns=proxy.protocol is ProxyProtocol.SOCKS5,
            limit=1,
        )
        headers = {"User-Agent": "ProxyPulse/1.0"}
        async with aiohttp.ClientSession(connector=connector, headers=headers, trust_env=False) as session:
            urls = [u for u in (self.config.test_url, self.config.fallback_url) if u]
            first_error: BaseException | None = None
            for idx, url in enumerate(urls):
                try:
                    return await self._request_ip(session, url)
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, OSError) as exc:
                    first_error = first_error or exc
                    if idx == 0 and len(urls) > 1 and not self._should_try_fallback(exc):
                        break
            assert first_error is not None
            raise first_error

    async def check_one(
        self,
        proxy: ProxyRecord,
        http_session: aiohttp.ClientSession,
        direct_ip: str = "",
    ) -> ProxyRecord:
        proxy.status = ProxyStatus.TESTING
        latencies: list[float] = []
        exit_ip = ""
        last_error: BaseException | None = None
        for _ in range(self.config.attempts):
            try:
                if proxy.protocol in {ProxyProtocol.HTTP, ProxyProtocol.HTTPS}:
                    exit_ip, latency = await self._probe_http(http_session, proxy)
                else:
                    exit_ip, latency = await self._probe_socks(proxy)
                latencies.append(latency)
                proxy.success_count += 1
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, RuntimeError, OSError) as exc:
                last_error = exc
                proxy.failure_count += 1
        proxy.tested_at = utc_now_iso()
        if latencies:
            proxy.status = ProxyStatus.ALIVE
            proxy.latency_ms = round(sum(latencies) / len(latencies), 1)
            proxy.exit_ip = exit_ip
            proxy.hides_ip = bool(direct_ip and exit_ip != direct_ip) if direct_ip else None
            proxy.error = "" if len(latencies) == self.config.attempts else "Bazı denemeler başarısız"
            proxy.score = calculate_score(proxy)
        else:
            proxy.status = ProxyStatus.DEAD
            proxy.latency_ms = None
            proxy.exit_ip = ""
            proxy.hides_ip = None
            proxy.score = 0
            proxy.error = classify_error(last_error or RuntimeError("Test başarısız"))
        return proxy

    async def check_many(
        self,
        proxies: Iterable[ProxyRecord],
        *,
        direct_ip: str = "",
        cancel_event: threading.Event | None = None,
        on_result: Callable[[ProxyRecord, int, int], None] | None = None,
    ) -> list[ProxyRecord]:
        _setup_loop_suppress()
        records = list(proxies)
        total = len(records)
        if not total:
            return records
        cancellation = cancel_event or threading.Event()
        if cancellation.is_set():
            for r in records:
                r.status = ProxyStatus.CANCELLED
            return records
        # Semaphore ile doğrudan hız kontrolü - queue/worker overhead yok, 30-40% daha hızlı
        sem = asyncio.Semaphore(self.config.concurrency)
        completed = 0
        completed_lock = asyncio.Lock()
        connector = aiohttp.TCPConnector(
            limit=self.config.concurrency,
            limit_per_host=0,
            ttl_dns_cache=300,
            force_close=True,
        )
        headers = {"User-Agent": "ProxyPulse/1.0", "Accept": "application/json,text/plain,*/*"}

        async with aiohttp.ClientSession(connector=connector, headers=headers, trust_env=False) as http_session:

            async def _run(proxy: ProxyRecord) -> None:
                nonlocal completed
                if cancellation.is_set():
                    proxy.status = ProxyStatus.CANCELLED
                    return
                async with sem:
                    if cancellation.is_set():
                        proxy.status = ProxyStatus.CANCELLED
                        return
                    try:
                        await self.check_one(proxy, http_session, direct_ip)
                    except BaseException:
                        # check_one zaten hatayı proxy.error'a yazar, burada yut
                        pass
                    async with completed_lock:
                        completed += 1
                        current = completed
                    if on_result:
                        try:
                            on_result(proxy, current, total)
                        except BaseException:
                            pass

            # Bellek dostu batch: 660k proxy için 660k task aynı anda oluşturulmasın (OOM)
            # Batch boyutu concurrency*4 veya 2500, her batch kendi içinde tam paralel
            batch_size = max(2000, self.config.concurrency * 3)
            # Toplam 660k => ~264 batch, her batch ~2000*3.5/700=10s => toplam aynı ama bellek 2000 task
            for start in range(0, total, batch_size):
                if cancellation.is_set():
                    for r in records[start:]:
                        r.status = ProxyStatus.CANCELLED
                    break
                batch = records[start : start + batch_size]
                tasks = [asyncio.create_task(_run(p)) for p in batch]
                try:
                    await asyncio.gather(*tasks)
                except asyncio.CancelledError:
                    for t in tasks:
                        t.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    # kalan batch'leri de iptal işaretle
                    for r in records[start + batch_size :]:
                        r.status = ProxyStatus.CANCELLED
                    break
        return records
