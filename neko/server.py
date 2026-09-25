"""The neko daemon: collectors → personas → JSON/SSE on 127.0.0.1.

Stdlib only. One background thread polls the collectors every couple of
seconds (or immediately when a hook event arrives), attaches personas and
publishes a snapshot; SSE clients get a push only when the content changed.
"""
from __future__ import annotations

import json
import mimetypes
import os
import sys
import threading
import time
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from . import config
from .model import DONE, ERROR, IDLE, SLEEPING, WAITING, WORKING

MAX_BODY = 1024 * 1024
_BAD = object()  # sentinel: request already answered with an error
PING_EVERY = 15.0
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}

MIME = {
    ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
    ".ico": "image/x-icon", ".glb": "model/gltf-binary", ".gltf": "model/gltf+json",
    ".wasm": "application/wasm", ".woff": "font/woff", ".woff2": "font/woff2",
    ".ttf": "font/ttf", ".otf": "font/otf", ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8", ".mp3": "audio/mpeg", ".ogg": "audio/ogg",
    ".wav": "audio/wav", ".hdr": "application/octet-stream", ".ktx2": "image/ktx2",
}

FALLBACK_INDEX = b"""<!doctype html><meta charset=utf-8><title>neko</title>
<body style="font-family:system-ui;background:#fff6ee;color:#5a4636;padding:2em">
<h1>(=^&#65381;&#969;&#65381;^=) neko daemon is running</h1>
<p>The web front end (<code>web/index.html</code>) is not installed yet.
Live data: <a href="/api/agents">/api/agents</a> &middot; <a href="/api/stream">/api/stream</a></p>
"""


def _log(*a) -> None:
    print(time.strftime("[neko %H:%M:%S]"), *a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- optional modules

def _load_collectors():
    try:
        from . import collectors  # type: ignore
        return collectors
    except Exception as e:  # pragma: no cover - depends on W1
        _log("collectors unavailable:", repr(e))
        return None


def _load_persona():
    try:
        from . import persona  # type: ignore
        if hasattr(persona, "persona_for"):
            return persona
    except Exception as e:  # pragma: no cover - depends on W2
        _log("persona module unavailable, using tiny fallback:", repr(e))
    return None


# ---------------------------------------------------------------- hub

class Hub:
    """Owns the latest snapshot and wakes SSE clients when it changes."""

    def __init__(self, demo: bool = False, poll: float | None = None):
        self.demo = demo
        self.poll = poll or config.poll_interval()
        self.started = time.time()
        self.cond = threading.Condition()
        self.version = 0
        self.payload: dict = {"generated_at": time.time(), "agents": [], "totals": _totals([])}
        self.payload_json = json.dumps(self.payload).encode()
        self._content_key = ""
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.last_error = ""
        self.collect_ms = 0.0
        self._persona_cache: dict[str, tuple[str, dict]] = {}
        self.world = None
        self.collectors = None
        if demo:
            from .demo import DemoWorld
            self.world = DemoWorld()
        else:
            self.collectors = _load_collectors()
        self.persona_mod = _load_persona()
        self._store = None  # PersonaStore (persists hash → traits; demo mode doesn't persist)
        self._thread: threading.Thread | None = None

    # -- personas
    def _persona_for(self, seed: str, parent: dict | None, taken: list) -> dict:
        if self.persona_mod is not None:
            fn = self.persona_mod.persona_for
            if self._store is None and not self.demo and hasattr(self.persona_mod, "PersonaStore"):
                try:
                    self._store = self.persona_mod.PersonaStore()
                except Exception as e:
                    self.last_error = f"persona store: {e!r}"
                    self._store = False
            if self._store:
                fn = self._store.get_or_create
            for kwargs in ({"parent": parent, "taken_hues": taken}, {"parent": parent}, {}):
                try:
                    p = fn(seed, **kwargs)
                    if isinstance(p, dict) and p:
                        return p
                    break
                except TypeError:
                    continue
                except Exception as e:
                    self.last_error = f"persona: {e!r}"
                    break
        from .demo import tiny_persona
        return tiny_persona(seed, parent=parent, taken_hues=taken)

    def attach_personas(self, agents: list) -> None:
        from .demo import hue_of
        live = {a.id for a in agents}
        # forget personas of agents that are gone
        for k in [k for k in self._persona_cache if k not in live]:
            del self._persona_cache[k]
        # parents first so kittens can inherit
        order = sorted(agents, key=lambda a: 1 if a.parent_id else 0)
        by_id = {a.id: a for a in agents}
        for a in order:
            if a.persona:
                self._persona_cache[a.id] = ("<given>", a.persona)
                continue
            seed = a.creation_prompt or a.title or a.last_prompt or a.id
            parent_persona = None
            if a.parent_id:
                pa = by_id.get(a.parent_id)
                parent_persona = (pa.persona if pa else None) or \
                    (self._persona_cache.get(a.parent_id) or (None, None))[1]
            cached = self._persona_cache.get(a.id)
            if cached and cached[0] == seed:
                a.persona = cached[1]
                continue
            taken = []
            for k, (_s, p) in self._persona_cache.items():
                if k != a.id and isinstance(p, dict):
                    h = p.get("hue")
                    h = h if isinstance(h, (int, float)) else hue_of(p.get("fur", ""))
                    if h is not None:
                        taken.append(round(h, 1))
            a.persona = self._persona_for(seed, parent_persona, taken)
            self._persona_cache[a.id] = (seed, a.persona)

    # -- collection
    def collect_once(self) -> None:
        t0 = time.time()
        try:
            if self.world is not None:
                agents = self.world.collect()
            elif self.collectors is not None and hasattr(self.collectors, "collect_all"):
                agents = list(self.collectors.collect_all())
            else:
                agents = []
            self.attach_personas(agents)
            dicts = [a.to_dict() for a in agents]
            totals = _totals(dicts)
            key = json.dumps({"agents": dicts, "totals": totals}, sort_keys=True, default=str)
            self.collect_ms = (time.time() - t0) * 1000
            if key != self._content_key:
                payload = {"generated_at": time.time(), "agents": dicts, "totals": totals}
                data = json.dumps(payload, default=str, ensure_ascii=False).encode("utf-8")
                with self.cond:
                    self._content_key = key
                    self.payload = payload
                    self.payload_json = data
                    self.version += 1
                    self.cond.notify_all()
            self.last_error = "" if self.last_error.startswith("collect") else self.last_error
        except Exception as e:
            self.last_error = f"collect: {e!r}"
            _log("collect failed:\n" + traceback.format_exc())

    def run(self) -> None:
        while not self.stop.is_set():
            self.collect_once()
            self.wake.wait(self.poll)
            self.wake.clear()

    def start(self) -> None:
        self._thread = threading.Thread(target=self.run, name="neko-collect", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self.stop.set()
        self.wake.set()
        with self.cond:
            self.cond.notify_all()

    # -- ingest
    def ingest(self, kind: str, payload) -> None:
        if self.world is not None or self.collectors is None:
            return
        fn = getattr(self.collectors, "ingest_event" if kind == "event" else "ingest_sdk", None)
        if fn is None:
            return
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if isinstance(item, dict):
                fn(item)

    def health(self) -> dict:
        return {
            "ok": True, "version": config.__version__, "demo": self.demo,
            "uptime": round(time.time() - self.started, 1),
            "agents": len(self.payload.get("agents", [])),
            "collectors": self.world is not None or (self.collectors is not None
                                                      and hasattr(self.collectors, "collect_all")),
            "persona": "neko.persona" if self.persona_mod is not None else "tiny-fallback",
            "poll": self.poll, "collect_ms": round(self.collect_ms, 1),
            "last_error": self.last_error, "version_seq": self.version,
        }


def _totals(dicts: list) -> dict:
    t = {"agents": len(dicts), WORKING: 0, WAITING: 0, IDLE: 0, SLEEPING: 0, ERROR: 0, DONE: 0,
         "subagents": 0, "cost_10m": 0.0, "cost_total": 0.0}
    for d in dicts:
        st = d.get("state")
        if st in t:
            t[st] += 1
        if d.get("kind") == "subagent":
            t["subagents"] += 1
        t["cost_10m"] += float(d.get("cost_10m") or 0)
        t["cost_total"] += float(d.get("cost_usd") or 0)
    t["cost_10m"] = round(t["cost_10m"], 4)
    t["cost_total"] = round(t["cost_total"], 4)
    return t


# ---------------------------------------------------------------- HTTP

def _host_only(value: str) -> str:
    v = (value or "").strip().lower()
    if v.startswith("["):
        return v[: v.find("]") + 1] if "]" in v else v
    return v.rsplit(":", 1)[0] if v.count(":") == 1 else v


def is_local_host_header(value: str) -> bool:
    return _host_only(value) in LOCAL_HOSTS


def is_local_origin(origin: str) -> bool:
    if not origin:
        return True
    try:
        u = urlsplit(origin)
    except ValueError:
        return False
    if u.scheme == "file":
        return True
    return u.scheme in ("http", "https") and (u.hostname or "") in LOCAL_HOSTS


class Handler(BaseHTTPRequestHandler):
    server_version = "neko/" + config.__version__
    protocol_version = "HTTP/1.1"
    hub: Hub = None  # type: ignore  # set by make_server
    web_root: Path = config.web_dir()

    def log_message(self, fmt, *args):  # quiet by default
        if os.environ.get("NEKO_DEBUG"):
            _log(self.address_string(), fmt % args)

    # -- helpers
    def _send(self, code: int, body: bytes = b"", ctype: str = "application/json; charset=utf-8",
              extra: dict | None = None) -> None:
        self.send_response(code)
        if body or code not in (204, 304):
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"))

    def _guard(self, post: bool) -> bool:
        """DNS-rebinding / CSRF protection. Returns False (and replies) if rejected."""
        if not is_local_host_header(self.headers.get("Host", "")):
            self._json(403, {"error": "forbidden host"})
            return False
        origin = self.headers.get("Origin", "")
        if origin and not is_local_origin(origin):
            self._json(403, {"error": "forbidden origin"})
            return False
        if post:
            ref = self.headers.get("Referer", "")
            if ref and not origin and not is_local_origin(ref):
                self._json(403, {"error": "forbidden referer"})
                return False
        return True

    def _read_body(self):
        te = self.headers.get("Transfer-Encoding", "")
        if te and te.lower() != "identity":
            self._json(411, {"error": "chunked bodies not supported"})
            return _BAD
        try:
            n = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            self._json(400, {"error": "bad content-length"})
            return _BAD
        if n > MAX_BODY:
            self.close_connection = True
            self._json(413, {"error": "body too large"})
            return _BAD
        raw = self.rfile.read(n) if n > 0 else b""
        try:
            return json.loads(raw.decode("utf-8") or "null")
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"error": "invalid json"})
            return _BAD

    # -- verbs
    def do_HEAD(self):
        if urlsplit(self.path).path == "/api/stream":
            return self._send(200, b"", "text/event-stream")
        self.do_GET()

    def do_OPTIONS(self):
        if not self._guard(post=False):
            return
        self._send(204, extra={"Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                               "Access-Control-Allow-Headers": "Content-Type"})

    def do_GET(self):
        if not self._guard(post=False):
            return
        path = urlsplit(self.path).path
        if path == "/api/agents":
            with self.hub.cond:
                body = self.hub.payload_json
            return self._send(200, body)
        if path == "/api/health":
            return self._json(200, self.hub.health())
        if path == "/api/stream":
            return self._stream()
        if path.startswith("/api/"):
            return self._json(404, {"error": "not found"})
        return self._static(path)

    def do_POST(self):
        if not self._guard(post=True):
            return
        parts = urlsplit(self.path)
        if parts.path not in ("/api/event", "/api/ingest"):
            return self._json(404, {"error": "not found"})
        ctype = self.headers.get("Content-Type", "application/json").split(";")[0].strip().lower()
        if ctype not in ("application/json", ""):
            return self._json(415, {"error": "use application/json"})
        payload = self._read_body()
        if payload is _BAD:
            return
        q = parse_qs(parts.query)
        if parts.path == "/api/event":
            if isinstance(payload, dict) and q.get("source") and "neko_source" not in payload:
                payload["neko_source"] = q["source"][0]
            try:
                self.hub.ingest("event", payload)
            except Exception as e:
                self.hub.last_error = f"ingest_event: {e!r}"
                _log("ingest_event failed:", repr(e))
            self.hub.wake.set()
            # Claude Code http hooks: an empty 2xx body means "success, no decision".
            return self._send(204)
        try:
            self.hub.ingest("sdk", payload)
        except Exception as e:
            self.hub.last_error = f"ingest_sdk: {e!r}"
            _log("ingest_sdk failed:", repr(e))
            return self._json(500, {"ok": False, "error": str(e)})
        self.hub.wake.set()
        return self._json(200, {"ok": True})

    # -- SSE
    def _stream(self):
        hub = self.hub
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True
        seen = -1
        last_write = time.time()
        try:
            self.wfile.write(b"retry: 2000\n\n")
            while not hub.stop.is_set():
                with hub.cond:
                    if hub.version == seen:
                        hub.cond.wait(timeout=max(0.5, PING_EVERY - (time.time() - last_write)))
                    ver, data = hub.version, hub.payload_json
                if ver != seen:
                    seen = ver
                    self.wfile.write(b"id: %d\ndata: " % ver + data + b"\n\n")
                    self.wfile.flush()
                    last_write = time.time()
                elif time.time() - last_write >= PING_EVERY:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    last_write = time.time()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    # -- static
    def _static(self, path: str):
        root = self.web_root.resolve()
        rel = unquote(path).lstrip("/")
        if rel == "" or rel.endswith("/"):
            rel += "index.html"
        if "\x00" in rel or any(p == ".." for p in rel.replace("\\", "/").split("/")):
            return self._json(403, {"error": "forbidden"})
        try:
            target = (root / rel).resolve()
            target.relative_to(root)
        except (ValueError, OSError):
            return self._json(403, {"error": "forbidden"})
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            if rel == "index.html":
                if "overlay=1" in urlsplit(self.path).query:  # never cover the desktop
                    return self._send(200, b"<!doctype html><body style='background:transparent'>",
                                      "text/html; charset=utf-8")
                return self._send(200, FALLBACK_INDEX, "text/html; charset=utf-8")
            return self._json(404, {"error": "not found"})
        ctype = MIME.get(target.suffix.lower()) or mimetypes.guess_type(target.name)[0] \
            or "application/octet-stream"
        try:
            body = target.read_bytes()
        except OSError:
            return self._json(404, {"error": "not found"})
        self._send(200, body, ctype)


class NekoServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def server_bind(self):
        # HTTPServer.server_bind() calls socket.getfqdn(), which can stall for
        # ~30 s on macOS with flaky DNS. We only ever bind loopback.
        import socketserver
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name, self.server_port = host, port


def make_server(host: str | None = None, port: int | None = None, demo: bool = False,
                poll: float | None = None, web_root: Path | None = None):
    """Create (server, hub). Binds loopback only regardless of NEKO_HOST unless it is local."""
    h = host or config.host()
    if h not in ("127.0.0.1", "localhost", "::1"):
        _log(f"refusing to bind {h!r}; neko is local-only → using 127.0.0.1")
        h = "127.0.0.1"
    hub = Hub(demo=demo, poll=poll)
    handler = type("NekoHandler", (Handler,), {"hub": hub, "web_root": web_root or config.web_dir()})
    srv = NekoServer((h, config.port() if port is None else port), handler)
    return srv, hub


def serve(port: int | None = None, demo: bool = False, open_browser: bool = False) -> None:
    srv, hub = make_server(port=port, demo=demo)
    hub.start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    _log(f"neko {'demo ' if demo else ''}daemon on {url}  (=^･ω･^=)  persona={hub.health()['persona']}")
    if open_browser:
        import webbrowser
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        _log("bye~ (=｀ω´=)")
    finally:
        hub.shutdown()
        srv.server_close()
