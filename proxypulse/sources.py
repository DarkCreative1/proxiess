"""Asynchronous, bounded and retrying public proxy source collection."""

from __future__ import annotations

import asyncio
import sys
import time
import warnings
from collections.abc import Callable, Iterable

import aiohttp

from .models import ProxyProtocol, ProxyRecord, ProxySource, SourceResult
from .parser import deduplicate, parse_feed

# Windows Proactor spam sustur (Collection sırasında da oluşur)
if sys.platform == "win32":
    warnings.filterwarnings("ignore", category=ResourceWarning)
    warnings.filterwarnings("ignore", message=".*Unclosed.*")
    try:
        from asyncio import proactor_events

        _orig_call_lost_src = proactor_events._ProactorBasePipeTransport._call_connection_lost

        def _patched_call_lost_src(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            try:
                return _orig_call_lost_src(self, *args, **kwargs)
            except (ConnectionResetError, OSError) as exc:
                if getattr(exc, "winerror", None) in (10054, 10053) or "10054" in str(exc):
                    return
                raise

        proactor_events._ProactorBasePipeTransport._call_connection_lost = _patched_call_lost_src  # type: ignore[method-assign]
    except Exception:
        pass


def _setup_loop_suppress_src() -> None:
    try:
        loop = asyncio.get_running_loop()
        orig = loop.get_exception_handler()

        def _handler(loop_, ctx):  # type: ignore[no-untyped-def]
            exc = ctx.get("exception")
            msg = str(ctx.get("message", "")) + str(exc)
            if isinstance(exc, (ConnectionResetError, OSError)) and (getattr(exc, "winerror", None) in (10054, 10053) or "10054" in msg):
                return
            if "10054" in msg or "_call_connection_lost" in msg or "Unclosed" in msg:
                return
            if orig:
                orig(loop_, ctx)
            else:
                loop_.default_exception_handler(ctx)

        loop.set_exception_handler(_handler)
    except RuntimeError:
        pass


def _src(name: str, url: str, protocol: str | None = None, repo: str = "") -> ProxySource:
    hint = ProxyProtocol(protocol) if protocol else None
    return ProxySource(name, url, "text", hint, attribution_url=repo or url)


def _gh(name: str, path: str, protocol: str | None = None, base: str = "https://raw.githubusercontent.com") -> ProxySource:
    repo_url = base + "/" + path.split("/", 1)[0] if "/" in path else ""
    return _src(name, f"{base}/{path}", protocol, repo_url)


GFP = "gfpcom/free-proxy-list/lists"
THORDATA = "Thordata/awesome-free-proxy-list/main/proxies"
VPSLAB = "VPSLabCloud/VPSLab-Free-Proxy-List/main"
KOMUTAN = "komutan234/Proxy-List-Free/main/proxies"
SHIFTYTR = "shiftytr/proxy-list/master"
PROXYGEN = "proxygenerator1/ProxyGenerator/main"
JETKAI = "jetkai/proxy-list/main/online-proxies/txt"
SPEEDX = "TheSpeedX/PROXY-List/master"
MONOSANS = "monosans/proxy-list/main/proxies"
ROOSTERKID = "roosterkid/openproxylist/main"
PROXYSCRAPE = "ProxyScrape/free-proxy-list/main/proxies/protocols"
PROXIFLY = "proxifly/free-proxy-list/main/proxies/protocols"

PS_API = "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&proxy_format=protocolipport&format=text"


def _proxyscrape_api(name: str, suffix: str, protocol: str | None) -> ProxySource:
    return _src(name, PS_API + suffix, protocol, "https://github.com/ProxyScrape/free-proxy-list")


DEFAULT_SOURCES: tuple[ProxySource, ...] = (
    # gfpcom
    _gh("GFP HTTP", f"{GFP}/http.txt", "http"),
    _gh("GFP HTTPS", f"{GFP}/https.txt", "https"),
    _gh("GFP SOCKS", f"{GFP}/socks.txt"),
    # Thordata
    _gh("Thordata ALL", f"{THORDATA}/all.txt"),
    _gh("Thordata HTTP", f"{THORDATA}/http.txt", "http"),
    _gh("Thordata HTTPS", f"{THORDATA}/https.txt", "https"),
    _gh("Thordata Top HTTP", f"{THORDATA}/top-http.txt", "http"),
    # VPSLab
    _gh("VPSLab Elite ALL", f"{VPSLAB}/all_elite.txt"),
    _gh("VPSLab HTTP ALL", f"{VPSLAB}/http_all.txt", "http"),
    _gh("VPSLab HTTP SSL", f"{VPSLAB}/http_ssl.txt", "https"),
    _gh("VPSLab SOCKS4", f"{VPSLAB}/socks4_all.txt", "socks4"),
    _gh("VPSLab SOCKS5", f"{VPSLAB}/socks5_all.txt", "socks5"),
    # komutan234
    _gh("Komutan HTTP", f"{KOMUTAN}/http.txt", "http"),
    _gh("Komutan SOCKS4", f"{KOMUTAN}/socks4.txt", "socks4"),
    _gh("Komutan SOCKS5", f"{KOMUTAN}/socks5.txt", "socks5"),
    # shiftytr
    _gh("ShiftyTr ALL", f"{SHIFTYTR}/proxy.txt"),
    _gh("ShiftyTr HTTP", f"{SHIFTYTR}/http.txt", "http"),
    _gh("ShiftyTr SOCKS4", f"{SHIFTYTR}/socks4.txt", "socks4"),
    _gh("ShiftyTr SOCKS5", f"{SHIFTYTR}/socks5.txt", "socks5"),
    # proxygenerator1
    _gh("ProxyGen Stable HTTP", f"{PROXYGEN}/Stable/http.txt", "http"),
    _gh("ProxyGen Stable SOCKS4", f"{PROXYGEN}/Stable/socks4.txt", "socks4"),
    _gh("ProxyGen Stable SOCKS5", f"{PROXYGEN}/Stable/socks5.txt", "socks5"),
    _gh("ProxyGen MostStable HTTP", f"{PROXYGEN}/MostStable/http.txt", "http"),
    _gh("ProxyGen MostStable SOCKS5", f"{PROXYGEN}/MostStable/socks5.txt", "socks5"),
    # jetkai
    _gh("JetKai ALL", f"{JETKAI}/proxies.txt"),
    _gh("JetKai HTTP", f"{JETKAI}/proxies-http.txt", "http"),
    _gh("JetKai HTTPS", f"{JETKAI}/proxies-https.txt", "https"),
    _gh("JetKai SOCKS4", f"{JETKAI}/proxies-socks4.txt", "socks4"),
    _gh("JetKai SOCKS5", f"{JETKAI}/proxies-socks5.txt", "socks5"),
    # TheSpeedX
    _gh("TheSpeedX HTTP", f"{SPEEDX}/http.txt", "http"),
    _gh("TheSpeedX SOCKS4", f"{SPEEDX}/socks4.txt", "socks4"),
    _gh("TheSpeedX SOCKS5", f"{SPEEDX}/socks5.txt", "socks5"),
    # monosans
    _gh("Monosans HTTP", f"{MONOSANS}/http.txt", "http"),
    _gh("Monosans SOCKS4", f"{MONOSANS}/socks4.txt", "socks4"),
    _gh("Monosans SOCKS5", f"{MONOSANS}/socks5.txt", "socks5"),
    # tekil kaynaklar
    _gh("Hookzof SOCKS5", "hookzof/socks5_list/master/proxy.txt", "socks5"),
    _gh("Clarketm RAW", "clarketm/proxy-list/master/proxy-list-raw.txt"),
    _gh("OpsxCQ ALL", "opsxcq/proxy-list/master/list.txt"),
    _gh("RoosterKid HTTPS", f"{ROOSTERKID}/HTTPS_RAW.txt", "https"),
    _gh("RoosterKid SOCKS5", f"{ROOSTERKID}/SOCKS5_RAW.txt", "socks5"),
    # ProxyScrape API
    _proxyscrape_api("ProxyScrapeAPI ALL", "", None),
    _proxyscrape_api("ProxyScrapeAPI HTTP", "&protocol=http", "http"),
    _proxyscrape_api("ProxyScrapeAPI SOCKS4", "&protocol=socks4", "socks4"),
    _proxyscrape_api("ProxyScrapeAPI SOCKS5", "&protocol=socks5", "socks5"),
    # ProxyScrape GitHub
    _gh("ProxyScrape HTTP", f"{PROXYSCRAPE}/http/data.txt", "http"),
    _gh("ProxyScrape SOCKS4", f"{PROXYSCRAPE}/socks4/data.txt", "socks4"),
    _gh("ProxyScrape SOCKS5", f"{PROXYSCRAPE}/socks5/data.txt", "socks5"),
    # Proxifly
    _gh("Proxifly HTTP", f"{PROXIFLY}/http/data.txt", "http"),
    _gh("Proxifly SOCKS4", f"{PROXIFLY}/socks4/data.txt", "socks4"),
    _gh("Proxifly SOCKS5", f"{PROXIFLY}/socks5/data.txt", "socks5"),
    # diğerleri
    _gh("Thenasty1337 Latest", "thenasty1337/free-proxy-list/main/data/latest/proxies.txt"),
    _gh("Riturajps Proxies", "theriturajps/proxy-list/main/proxies.txt"),
    _gh("Bes-js Public", "Bes-js/public-proxy-list/main/proxies.txt"),
    # === YENİ: Detaylı araştırma ile eklenen sağlam kaynaklar (2026) ===
    # FYVRI fresh-proxy-list (archive classic - hourly, çok stabil)
    _gh("FYVRI ALL", "fyvri/fresh-proxy-list/archive/storage/classic/all.txt"),
    _gh("FYVRI HTTP", "fyvri/fresh-proxy-list/archive/storage/classic/http.txt", "http"),
    _gh("FYVRI HTTPS", "fyvri/fresh-proxy-list/archive/storage/classic/https.txt", "https"),
    _gh("FYVRI SOCKS4", "fyvri/fresh-proxy-list/archive/storage/classic/socks4.txt", "socks4"),
    _gh("FYVRI SOCKS5", "fyvri/fresh-proxy-list/archive/storage/classic/socks5.txt", "socks5"),
    # Vakhov fresh-proxy-list
    _gh("Vakhov ALL", "vakhov/fresh-proxy-list/master/proxylist.txt"),
    _gh("Vakhov HTTP", "vakhov/fresh-proxy-list/master/http.txt", "http"),
    _gh("Vakhov HTTPS", "vakhov/fresh-proxy-list/master/https.txt", "https"),
    _gh("Vakhov SOCKS4", "vakhov/fresh-proxy-list/master/socks4.txt", "socks4"),
    _gh("Vakhov SOCKS5", "vakhov/fresh-proxy-list/master/socks5.txt", "socks5"),
    # r00tee Proxy-List (5 dakikada bir güncellenir)
    _gh("R00tee HTTPS", "r00tee/Proxy-List/main/Https.txt", "https"),
    _gh("R00tee SOCKS4", "r00tee/Proxy-List/main/Socks4.txt", "socks4"),
    _gh("R00tee SOCKS5", "r00tee/Proxy-List/main/Socks5.txt", "socks5"),
    # ClearProxy checked-proxy-list (ülke/ASN filtreli, çok temiz)
    _gh("ClearProxy HTTP", "ClearProxy/checked-proxy-list/main/http/raw/all.txt", "http"),
    _gh("ClearProxy SOCKS4", "ClearProxy/checked-proxy-list/main/socks4/raw/all.txt", "socks4"),
    _gh("ClearProxy SOCKS5", "ClearProxy/checked-proxy-list/main/socks5/raw/all.txt", "socks5"),
    # iplocate free-proxy-list (30 dakikada bir doğrulanmış, elite anonim)
    _gh("IPLocate ALL", "iplocate/free-proxy-list/main/all-proxies.txt"),
    _gh("IPLocate HTTP", "iplocate/free-proxy-list/main/protocols/http.txt", "http"),
    _gh("IPLocate HTTPS", "iplocate/free-proxy-list/main/protocols/https.txt", "https"),
    _gh("IPLocate SOCKS4", "iplocate/free-proxy-list/main/protocols/socks4.txt", "socks4"),
    _gh("IPLocate SOCKS5", "iplocate/free-proxy-list/main/protocols/socks5.txt", "socks5"),
    # gproxynet (küçük ama taze örnek)
    _gh("GProxyNet ALL", "gproxynet/free-proxy-list/main/all.txt"),
    _gh("GProxyNet HTTP", "gproxynet/free-proxy-list/main/http.txt", "http"),
    _gh("GProxyNet SOCKS4", "gproxynet/free-proxy-list/main/socks4.txt", "socks4"),
    _gh("GProxyNet SOCKS5", "gproxynet/free-proxy-list/main/socks5.txt", "socks5"),
    # TheSpeedX SOCKS-List (ayrı repo, PROXY-List'ten bağımsız)
    _gh("SpeedX SOCKS-List HTTP", "TheSpeedX/SOCKS-List/master/http.txt", "http"),
    _gh("SpeedX SOCKS-List SOCKS4", "TheSpeedX/SOCKS-List/master/socks4.txt", "socks4"),
    _gh("SpeedX SOCKS-List SOCKS5", "TheSpeedX/SOCKS-List/master/socks5.txt", "socks5"),
    # KangProxy (officialputuid, her 5 saatte güncellenir)
    _gh("KangProxy HTTP", "officialputuid/KangProxy/main/http/http.txt", "http"),
    _gh("KangProxy HTTPS", "officialputuid/KangProxy/main/https/https.txt", "https"),
    _gh("KangProxy SOCKS4", "officialputuid/KangProxy/main/socks4/socks4.txt", "socks4"),
    _gh("KangProxy SOCKS5", "officialputuid/KangProxy/main/socks5/socks5.txt", "socks5"),
    _gh("KangProxy ALL", "officialputuid/KangProxy/main/xResults/Proxies.txt"),
    # MuRongPIG Proxy-Master
    _gh("MuRongPIG HTTP", "MuRongPIG/Proxy-Master/main/http.txt", "http"),
    _gh("MuRongPIG HTTPS", "MuRongPIG/Proxy-Master/main/https.txt", "https"),
    _gh("MuRongPIG SOCKS4", "MuRongPIG/Proxy-Master/main/socks4.txt", "socks4"),
    _gh("MuRongPIG SOCKS5", "MuRongPIG/Proxy-Master/main/socks5.txt", "socks5"),
    # mmpx12 proxy-list
    _gh("mmpx12 HTTP", "mmpx12/proxy-list/master/http.txt", "http"),
    _gh("mmpx12 HTTPS", "mmpx12/proxy-list/master/https.txt", "https"),
    _gh("mmpx12 SOCKS4", "mmpx12/proxy-list/master/socks4.txt", "socks4"),
    _gh("mmpx12 SOCKS5", "mmpx12/proxy-list/master/socks5.txt", "socks5"),
    # Zaeem20
    _gh("Zaeem20 HTTP", "Zaeem20/FREE_PROXIES_LIST/master/http.txt", "http"),
    _gh("Zaeem20 HTTPS", "Zaeem20/FREE_PROXIES_LIST/master/https.txt", "https"),
    _gh("Zaeem20 SOCKS4", "Zaeem20/FREE_PROXIES_LIST/master/socks4.txt", "socks4"),
    _gh("Zaeem20 SOCKS5", "Zaeem20/FREE_PROXIES_LIST/master/socks5.txt", "socks5"),
    # Anonym0usWork1221
    _gh("Anonym0us HTTP", "Anonym0usWork1221/Free-Proxies/main/proxy_files/http_proxies.txt", "http"),
    _gh("Anonym0us HTTPS", "Anonym0usWork1221/Free-Proxies/main/proxy_files/https_proxies.txt", "https"),
    _gh("Anonym0us SOCKS4", "Anonym0usWork1221/Free-Proxies/main/proxy_files/socks4_proxies.txt", "socks4"),
    _gh("Anonym0us SOCKS5", "Anonym0usWork1221/Free-Proxies/main/proxy_files/socks5_proxies.txt", "socks5"),
    # zevtyardt
    _gh("Zevtyardt HTTP", "zevtyardt/proxy-list/main/http.txt", "http"),
    _gh("Zevtyardt SOCKS4", "zevtyardt/proxy-list/main/socks4.txt", "socks4"),
    _gh("Zevtyardt SOCKS5", "zevtyardt/proxy-list/main/socks5.txt", "socks5"),
    # yemixzy
    _gh("Yemixzy HTTP", "yemixzy/proxy-list/main/proxies/http.txt", "http"),
    _gh("Yemixzy SOCKS4", "yemixzy/proxy-list/main/proxies/socks4.txt", "socks4"),
    _gh("Yemixzy SOCKS5", "yemixzy/proxy-list/main/proxies/socks5.txt", "socks5"),
    # API - openproxylist.xyz (hızlı, anonim)
    _src("OpenProxyList HTTP", "https://api.openproxylist.xyz/http.txt", "http", "https://github.com/roosterkid/openproxylist"),
    _src("OpenProxyList SOCKS4", "https://api.openproxylist.xyz/socks4.txt", "socks4", "https://github.com/roosterkid/openproxylist"),
    _src("OpenProxyList SOCKS5", "https://api.openproxylist.xyz/socks5.txt", "socks5", "https://github.com/roosterkid/openproxylist"),
    # API - proxy-list.download
    _src("ProxyListDownload HTTP", "https://www.proxy-list.download/api/v1/get?type=http", "http", "https://www.proxy-list.download/"),
    _src("ProxyListDownload HTTPS", "https://www.proxy-list.download/api/v1/get?type=https", "https", "https://www.proxy-list.download/"),
    _src("ProxyListDownload SOCKS4", "https://www.proxy-list.download/api/v1/get?type=socks4", "socks4", "https://www.proxy-list.download/"),
    _src("ProxyListDownload SOCKS5", "https://www.proxy-list.download/api/v1/get?type=socks5", "socks5", "https://www.proxy-list.download/"),
    # Diğer sağlam topluluk listeleri
    _gh("Sunny9577 HTTP", "sunny9577/proxy-scraper/master/generated/http_proxies.txt", "http"),
    _gh("Sunny9577 SOCKS5", "sunny9577/proxy-scraper/master/generated/socks5_proxies.txt", "socks5"),
    _gh("HendrikBGR ALL", "hendrikbgr/Free-Proxy-Repo/master/proxy_list.txt"),
    _gh("Noctiro HTTP", "Noctiro/getproxy/master/file/http.txt", "http"),
    _gh("Noctiro SOCKS5", "Noctiro/getproxy/master/file/socks5.txt", "socks5"),
    _gh("ErcinDedeoglu HTTP", "ErcinDedeoglu/proxies/main/proxies/http.txt", "http"),
    _gh("ErcinDedeoglu SOCKS5", "ErcinDedeoglu/proxies/main/proxies/socks5.txt", "socks5"),
    _gh("ArrayIterator SOCKS5", "ArrayIterator/proxy-lists/main/proxies/socks5.txt", "socks5"),
    _gh("VannDev HTTP", "Vann-Dev/proxy-list/main/proxies/http.txt", "http"),
    _gh("VannDev SOCKS5", "Vann-Dev/proxy-list/main/proxies/socks5.txt", "socks5"),
)


class SourceCollector:
    def __init__(self, timeout_seconds: float = 20.0, max_download_bytes: int = 25_000_000) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_download_bytes = max_download_bytes

    async def _fetch_one(self, session: aiohttp.ClientSession, source: ProxySource) -> SourceResult:
        started = time.perf_counter()
        result = SourceResult(source=source)
        last_error = ""
        for attempt in range(3):
            try:
                timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
                async with session.get(source.url, timeout=timeout, allow_redirects=True) as response:
                    response.raise_for_status()
                    if response.content_length and response.content_length > self.max_download_bytes:
                        raise ValueError("Kaynak dosyası izin verilen boyutu aşıyor")
                    chunks: list[bytes] = []
                    downloaded = 0
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        downloaded += len(chunk)
                        if downloaded > self.max_download_bytes:
                            raise ValueError("Kaynak dosyası izin verilen boyutu aşıyor")
                        chunks.append(chunk)
                    raw = b"".join(chunks)
                    charset = response.charset or "utf-8"
                    content = raw.decode(charset, errors="replace")
                    result.proxies = parse_feed(content, source.name, source.format, source.protocol_hint)
                    if not result.proxies:
                        raise ValueError("Kaynak geçerli proxy döndürmedi")
                    break
            except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeError, ValueError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < 2:
                    await asyncio.sleep(0.5 * (2**attempt))
        if not result.proxies:
            result.error = last_error or "Bilinmeyen kaynak hatası"
        result.elapsed_ms = (time.perf_counter() - started) * 1000
        return result

    async def collect(
        self,
        sources: Iterable[ProxySource] = DEFAULT_SOURCES,
        on_source: Callable[[SourceResult], None] | None = None,
    ) -> tuple[list[ProxyRecord], list[SourceResult]]:
        _setup_loop_suppress_src()
        enabled = [source for source in sources if source.enabled]
        headers = {
            "User-Agent": "ProxyPulse/1.0 (+desktop proxy health checker)",
            "Accept": "application/json,text/plain,*/*",
        }
        connector = aiohttp.TCPConnector(limit=max(4, len(enabled)), ttl_dns_cache=300)
        async with aiohttp.ClientSession(headers=headers, connector=connector, trust_env=False) as session:
            tasks = [asyncio.create_task(self._fetch_one(session, source)) for source in enabled]
            results: list[SourceResult] = []
            for future in asyncio.as_completed(tasks):
                result = await future
                results.append(result)
                if on_source:
                    on_source(result)
        all_records = [proxy for result in results for proxy in result.proxies]
        return deduplicate(all_records), results
