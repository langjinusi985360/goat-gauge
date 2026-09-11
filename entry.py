"""GOAT Gauge launcher."""

from __future__ import annotations

import argparse


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Command Code GOAT usage dashboard")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--chrome", action="store_true", help="run the server and open Chrome")
    mode.add_argument("--serve", action="store_true", help="run only the local server")
    parser.add_argument("--host", default="127.0.0.1", help="server host")
    parser.add_argument("--port", type=int, default=0, help="server port; 0 chooses a free port")
    parser.add_argument("--demo", action="store_true", help="show generated demo data")
    parser.add_argument("--no-browser", action="store_true", help="do not open Chrome in --chrome mode")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.chrome:
        from app.chrome_mode import main as chrome_main

        chrome_main(
            host=args.host,
            port=args.port,
            demo=args.demo,
            open_browser=not args.no_browser,
        )
        return

    from app.server import run_foreground

    run_foreground(host=args.host, port=args.port, demo=args.demo)


if __name__ == "__main__":
    main()
