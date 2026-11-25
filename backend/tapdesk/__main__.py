import sys

USAGE = "usage: python -m tapdesk {serve|pair} [options]"


def main() -> None:
    cmd, rest = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("", [])
    if cmd == "serve":
        from .serve import main as run

        run(rest)
    elif cmd == "pair":
        from .serve import pair_main

        pair_main()
    else:
        raise SystemExit(USAGE)


main()
