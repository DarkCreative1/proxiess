from __future__ import annotations

import json
import unittest

from proxypulse.models import ProxyProtocol
from proxypulse.parser import parse_feed, parse_text


class ParserTests(unittest.TestCase):
    def test_text_parses_protocols_rejects_private_and_merges_duplicates(self) -> None:
        content = """
        http://8.8.8.8:8080
        HTTP://8.8.8.8:8080
        socks5://1.1.1.1:1080
        127.0.0.1:9999
        10.0.0.1:80
        8.8.8.8:70000
        """
        records = parse_text(content, "fixture", ProxyProtocol.HTTP)
        self.assertEqual(2, len(records))
        self.assertEqual({"http", "socks5"}, {record.protocol.value for record in records})

    def test_json_reads_nested_proxyscrape_metadata(self) -> None:
        payload = {
            "proxies": [
                {
                    "ip": "8.8.4.4",
                    "port": 3128,
                    "protocol": "http",
                    "anonymity": "elite",
                    "ssl": True,
                    "uptime": 98.4,
                    "average_timeout": 245.7,
                    "ip_data": {"country": "United States", "countryCode": "US", "city": "Ashburn"},
                }
            ]
        }
        record = parse_feed(json.dumps(payload), "ProxyScrape", "json")[0]
        self.assertEqual("US", record.country_code)
        self.assertEqual("United States", record.country)
        self.assertEqual(245.7, record.advertised_latency_ms)
        self.assertTrue(record.advertised_ssl)

    def test_ipv6_is_emitted_with_brackets(self) -> None:
        records = parse_text("socks5://[2606:4700:4700::1111]:1080", "fixture")
        self.assertEqual(1, len(records))
        self.assertEqual("[2606:4700:4700::1111]:1080", records[0].endpoint)

    def test_plain_ip_port_defaults_to_http(self) -> None:
        records = parse_text("8.8.8.8:3128", "import")
        self.assertEqual(1, len(records))
        self.assertIs(records[0].protocol, ProxyProtocol.HTTP)


if __name__ == "__main__":
    unittest.main()
