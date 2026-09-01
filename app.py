"""ProxyPulse desktop launcher."""

from __future__ import annotations

import sys

from proxypulse import __version__


def main() -> int:
    if "--self-test" in sys.argv:
        from proxypulse.sources import DEFAULT_SOURCES

        print(f"MODIFIED_OK version={__version__} sources={len(DEFAULT_SOURCES)}")
        return 0
    if "--cli" in sys.argv:
        from proxypulse.cli import main as cli_main

        args = [argument for argument in sys.argv[1:] if argument != "--cli"]
        return cli_main(args)
    from proxypulse.gui import run

    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
