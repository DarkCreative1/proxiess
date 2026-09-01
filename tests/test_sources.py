from __future__ import annotations

import asyncio
import json
import unittest

from proxypulse.models import ProxySource
from proxypulse.sources import SourceCollector


class SourceCollectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_chunked_network_delivery_is_read_to_eof(self) -> None:
        payload = json.dumps(
            {
                "proxies": [
                    {"ip": "8.8.8.8", "port": 8080, "protocol": "http"},
                    {"ip": "1.1.1.1", "port": 1080, "protocol": "socks5"},
                ]
            }
        ).encode()

        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json; charset=utf-8\r\n"
                + f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
            )
            split = len(payload) // 2
            writer.write(payload[:split])
            await writer.drain()
            await asyncio.sleep(0.03)
            writer.write(payload[split:])
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            source = ProxySource("chunk-test", f"http://127.0.0.1:{port}/list", "json")
            records, results = await SourceCollector(timeout_seconds=2).collect([source])
        finally:
            server.close()
            await server.wait_closed()
        self.assertEqual(2, len(records))
        self.assertTrue(results[0].ok)


if __name__ == "__main__":
    unittest.main()
