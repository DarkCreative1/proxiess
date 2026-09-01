# ProxyPulse

ProxyPulse; güncel ücretsiz/açık proxy listelerini toplayan, tekilleştiren, gerçek bir IP doğrulama uç noktası üzerinden test eden ve sonuçları hız/skor sırasıyla gösteren Türkçe Windows masaüstü uygulamasıdır.

## Özellikler

- ProxyScrape v4 ve IPLocate etkin; Proxifly/TheSpeedX ayarlardan isteğe bağlı açılabilir.
- HTTP, HTTPS-CONNECT, SOCKS4 ve SOCKS5 desteği.
- Geçerli global IP/port kontrolü ve `(protokol, IP, port)` tekilleştirme.
- Sınırlı eşzamanlı asenkron test, zaman aşımı, durdurma ve yedek judge.
- Gecikme, başarı durumu, çıkış IP'si, IP gizleme sonucu ve 0–100 kalite skoru.
- SQLite/WAL kalıcı havuz; açık/koyu tema; arama, filtre, sıralama.
- TXT/CSV içe aktarma ve atomik UTF-8 CSV/TXT dışa aktarma.
- Ağ işleri ayrı worker thread'inde; Tk arayüzü yalnız ana thread'de güncellenir.

## Hızlı başlangıç (Windows)

1. `KURULUM.bat` dosyasını bir kez çalıştırın.
2. Ardından `BASLAT.bat` dosyasını açın.
3. Uygulamada **Topla + Test Et** düğmesini kullanın.

Python 3.11 veya daha yeni sürüm gerekir. Kurulum yalnız proje içindeki `.venv` klasörüne bağımlılık ekler.

## Komut satırı tanıları

```powershell
.\.venv\Scripts\python.exe app.py --self-test
.\.venv\Scripts\python.exe app.py --cli collect --limit 10
.\.venv\Scripts\python.exe app.py --cli live-check --limit 5 --timeout 5
.\.venv\Scripts\python.exe app.py --cli gui-smoke
```

## Test

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Testler gerçek ücretsiz proxy durumuna bağlı değildir; canlı tanı ayrıca çalıştırılır.

## EXE üretme

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

Çıktı: `dist\ProxyPulse\ProxyPulse.exe` (onedir paket).

## Veri konumu

Veritabanı ve ayarlar `%LOCALAPPDATA%\ProxyPulse\proxypulse.db` altında saklanır.

## Kaynaklar

- [ProxyScrape free proxy API/list](https://github.com/proxyscrape/free-proxy-list)
- [IPLocate free proxy list](https://github.com/iplocate/free-proxy-list)
- [aiohttp proxy support](https://docs.aiohttp.org/en/stable/client_advanced.html#proxy-support)
- [aiohttp-socks](https://github.com/romis2012/aiohttp-socks)
- [Python Tkinter threading model](https://docs.python.org/3/library/tkinter.html#threading-model)

## GitHub Actions ile otomatik yayın

Repo GitHub'a yüklendiğinde `.github/workflows/check-proxies.yml` her 30 dakikada
bir kaynakları toplar, proxyleri test eder ve çalışanları `results/` klasörüne
commit eder. Actions sekmesindeki **Proxy listesini güncelle > Run workflow** ile
elle de başlatılabilir.

Yayın dosyaları tek satır ve virgülle ayrılmış URL biçimindedir:

- `results/proxies.txt`: tüm çalışan proxyler
- `results/http.txt`, `https.txt`, `socks4.txt`, `socks5.txt`: protokole göre listeler
- `results/proxies.json`: gecikme, skor ve diğer ayrıntılar

Raw bağlantı biçimi:

```text
https://raw.githubusercontent.com/KULLANICI/REPO/main/results/proxies.txt
```

Varsayılan olarak en iyi görünen 10.000 aday, 500 eşzamanlı bağlantıyla ve proxy
başına 3 saniyelik zaman aşımıyla kontrol edilir. Bu değerler workflow içindeki
`MAX_CHECKS`, `CONCURRENCY` ve `PROXY_TIMEOUT` alanlarından değiştirilebilir.
