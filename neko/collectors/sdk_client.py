"""Send OpenAI Agents SDK traces to a running neko daemon (optional, tiny, stdlib only).

Usage::

    from agents import add_trace_processor
    from neko.collectors.sdk_client import NekoTracingProcessor

    add_trace_processor(NekoTracingProcessor())          # keeps the default OpenAI exporter
    # or: set_trace_processors([NekoTracingProcessor()])  # neko only, nothing leaves the machine

Every trace/span start and end is exported (``trace.export()`` / ``span.export()``)
and POSTed as ``{"event": "span_end", "item": {...}}`` to
``http://127.0.0.1:8765/api/ingest`` (override with ``url=`` or ``$NEKO_URL``) from a
background daemon thread. It never blocks the agent and never raises: if the daemon
is not running, events are silently dropped (bounded queue of 1000).

Note: ``span.export()`` includes model inputs/outputs unless tracing is configured
with ``trace_include_sensitive_data=False``; the data only goes to localhost, where
neko uses the first user message to seed the character's look.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8765/api/ingest"


def _base():
    try:  # subclass the real interface when the SDK is importable (lazy, optional)
        from agents.tracing import TracingProcessor  # type: ignore
        return TracingProcessor
    except Exception:
        return object


class NekoTracingProcessor(_base()):  # type: ignore[misc]
    def __init__(self, url: str | None = None, timeout: float = 0.5, max_queue: int = 1000):
        self.url = url or os.environ.get("NEKO_URL") or DEFAULT_URL
        if self.url.endswith(":8765") or self.url.endswith(":8765/"):
            self.url = self.url.rstrip("/") + "/api/ingest"
        self.timeout = timeout
        self._q: "queue.Queue" = queue.Queue(maxsize=max_queue)
        self._t = threading.Thread(target=self._run, name="neko-tracing", daemon=True)
        self._t.start()

    # --- TracingProcessor interface
    def on_trace_start(self, trace) -> None:
        self._put("trace_start", trace)

    def on_trace_end(self, trace) -> None:
        self._put("trace_end", trace)

    def on_span_start(self, span) -> None:
        self._put("span_start", span)

    def on_span_end(self, span) -> None:
        self._put("span_end", span)

    def shutdown(self) -> None:
        self.force_flush()

    def force_flush(self, timeout: float = 2.0) -> None:
        """Wait (at most ``timeout`` s) for queued events to be sent."""
        import time
        deadline = time.time() + timeout
        while self._q.unfinished_tasks and time.time() < deadline:
            time.sleep(0.02)

    # --- internals
    def _put(self, event: str, obj) -> None:
        try:
            item = obj.export() if hasattr(obj, "export") else obj
            if item:
                self._q.put_nowait({"event": event, "item": item})
        except Exception:
            pass  # queue full or export failed: drop

    def _run(self) -> None:
        while True:
            msg = self._q.get()
            try:
                body = json.dumps(msg, default=str).encode()
                req = urllib.request.Request(self.url, data=body, method="POST",
                                             headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=self.timeout).close()
            except Exception:
                pass
            finally:
                self._q.task_done()
