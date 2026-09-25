#!/usr/bin/env python3
"""neko hook shim: read a Claude Code / Codex hook payload on stdin, POST it to
the local neko daemon, and ALWAYS exit 0 silently (never blocks the agent).

usage: neko-hook.py [--source claude|codex] [--port 8765]
"""
import json
import os
import sys


def main():
    try:
        import urllib.request
        args = sys.argv[1:]
        source, port = "claude", os.environ.get("NEKO_PORT") or "8765"
        if "--source" in args:
            source = args[args.index("--source") + 1]
        if "--port" in args:
            port = args[args.index("--port") + 1]
        raw = b"" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.buffer.read(1024 * 1024)
        try:
            payload = json.loads(raw.decode("utf-8", "replace") or "{}")
        except ValueError:
            payload = {"raw": raw.decode("utf-8", "replace")[:4000]}
        if not isinstance(payload, dict):
            payload = {"payload": payload}
        payload.setdefault("neko_source", source)
        req = urllib.request.Request(
            "http://127.0.0.1:%d/api/event?source=%s" % (int(port), source),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=0.3).close()
    except BaseException:
        pass


if __name__ == "__main__":
    main()
    os._exit(0)
