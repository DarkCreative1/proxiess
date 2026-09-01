"""ProxyPulse - public proxy aggregation and health checking desktop app."""

from __future__ import annotations

import sys
import warnings

# Global Windows spam susturucu (erken importta devreye girsin)
if sys.platform == "win32":
    warnings.filterwarnings("ignore", category=ResourceWarning)
    warnings.filterwarnings("ignore", message=".*Unclosed.*")
    try:
        from asyncio import proactor_events

        _orig = proactor_events._ProactorBasePipeTransport._call_connection_lost

        def _patched(self, *a, **kw):  # type: ignore[no-untyped-def]
            try:
                return _orig(self, *a, **kw)
            except (ConnectionResetError, OSError) as exc:
                if getattr(exc, "winerror", None) in (10054, 10053) or "10054" in str(exc):
                    return
                raise

        proactor_events._ProactorBasePipeTransport._call_connection_lost = _patched  # type: ignore[method-assign]
    except Exception:
        pass

__version__ = "1.0.0"

