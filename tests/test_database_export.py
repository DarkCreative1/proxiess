from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from proxypulse.database import ProxyRepository
from proxypulse.exporter import export_csv, export_txt
from proxypulse.models import ProxyProtocol, ProxyRecord, ProxyStatus


class DatabaseExportTests(unittest.TestCase):
    def test_upsert_roundtrip_and_merge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = ProxyRepository(Path(directory) / "test.db")
            first = ProxyRecord("8.8.8.8", 8080, ProxyProtocol.HTTP, {"A"})
            second = ProxyRecord(
                "8.8.8.8",
                8080,
                ProxyProtocol.HTTP,
                {"A", "B"},
                status=ProxyStatus.ALIVE,
                latency_ms=321.0,
                score=80,
                success_count=1,
            )
            repository.upsert_many([first])
            repository.upsert_many([second])
            loaded = repository.load_all()
            self.assertEqual(1, len(loaded))
            self.assertEqual({"A", "B"}, loaded[0].sources)
            self.assertIs(loaded[0].status, ProxyStatus.ALIVE)

    def test_exports_are_complete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            record = ProxyRecord("1.1.1.1", 1080, ProxyProtocol.SOCKS5, {"Türkçe Kaynak"}, status=ProxyStatus.ALIVE)
            csv_path = Path(directory) / "out.csv"
            txt_path = Path(directory) / "out.txt"
            self.assertEqual(1, export_csv([record], csv_path))
            self.assertEqual(1, export_txt([record], txt_path))
            self.assertTrue(csv_path.read_bytes().startswith(b"\xef\xbb\xbf"))
            self.assertEqual("socks5://1.1.1.1:1080\n", txt_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

