"""Server / CLI / hooks tests. Never touches the real ~/.claude, ~/.codex or launchd."""
from __future__ import annotations

import http.client
import json
import os
import plistlib
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("NEKO_HOME", str(tmp_path / "neko-home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("NEKO_LAUNCH_AGENTS_DIR", str(tmp_path / "LaunchAgents"))
    monkeypatch.setenv("NEKO_LOG_FILE", str(tmp_path / "logs" / "neko.log"))
    monkeypatch.setenv("NEKO_NO_LAUNCHCTL", "1")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def server(tmp_path):
    from neko.server import make_server
    web = tmp_path / "web"
    (web / "js").mkdir(parents=True)
    (web / "index.html").write_text("<!doctype html><title>neko</title>")
    (web / "js" / "app.js").write_text("console.log('meow')")
    (web / "cat.glb").write_bytes(b"glTF\x02\x00\x00\x00")
    (tmp_path / "secret.txt").write_text("top secret")
    srv, hub = make_server(port=_free_port(), demo=True, poll=0.2, web_root=web)
    hub.start()
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    t.start()
    deadline = time.time() + 10
    while hub.version == 0 and time.time() < deadline:
        time.sleep(0.02)
    yield srv.server_address[1], hub
    hub.shutdown()
    srv.shutdown()
    srv.server_close()


def _req(port, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    h = {"Host": f"127.0.0.1:{port}"}
    h.update(headers or {})
    data = json.dumps(body).encode() if body is not None and not isinstance(body, bytes) else body
    if data is not None:
        h.setdefault("Content-Type", "application/json")
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    out = r.status, dict(r.getheaders()), r.read()
    c.close()
    return out


# ---------------------------------------------------------------- API

def test_agents_schema(server):
    port, _hub = server
    st, hdr, body = _req(port, "GET", "/api/agents")
    assert st == 200 and hdr["Content-Type"].startswith("application/json")
    d = json.loads(body)
    assert {"generated_at", "agents", "totals"} <= d.keys()
    t = d["totals"]
    for k in ("agents", "working", "waiting", "idle", "sleeping", "error", "subagents",
              "cost_10m", "cost_total"):
        assert k in t
    assert t["agents"] == len(d["agents"]) >= 5
    assert t["subagents"] >= 1
    from neko.model import SOURCES, STATES
    ids = {a["id"] for a in d["agents"]}
    for a in d["agents"]:
        assert a["state"] in STATES and a["source"] in SOURCES
        assert 0 <= a["context_pct"] <= 1
        p = a["persona"]
        for k in ("name", "species", "hat", "prop", "fur", "accent", "eyes"):
            assert k in p, k
        if a["kind"] == "subagent":
            assert a["parent_id"] in ids


def test_health(server):
    port, _ = server
    st, _h, body = _req(port, "GET", "/api/health")
    d = json.loads(body)
    assert st == 200 and d["ok"] and d["demo"] is True


def test_sse_yields_event(server):
    port, _ = server
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", "/api/stream", headers={"Host": f"localhost:{port}"})
    r = c.getresponse()
    assert r.status == 200 and r.getheader("Content-Type").startswith("text/event-stream")
    buf = b""
    deadline = time.time() + 5
    while b"data: " not in buf or not buf.rstrip(b"\n").endswith(b"}") and time.time() < deadline:
        chunk = r.fp.readline()
        if not chunk:
            break
        buf += chunk
        if b"data: " in buf and chunk == b"\n":
            break
    c.close()
    line = [ln for ln in buf.split(b"\n") if ln.startswith(b"data: ")][0]
    d = json.loads(line[6:])
    assert "agents" in d and "totals" in d


def test_static_and_mime(server):
    port, _ = server
    st, h, body = _req(port, "GET", "/")
    assert st == 200 and h["Content-Type"].startswith("text/html") and b"neko" in body
    st, h, body = _req(port, "GET", "/js/app.js?v=1")
    assert st == 200 and h["Content-Type"].startswith("text/javascript") and b"meow" in body
    st, h, _ = _req(port, "GET", "/cat.glb")
    assert st == 200 and h["Content-Type"] == "model/gltf-binary"
    st, _h, _ = _req(port, "GET", "/nope.js")
    assert st == 404


@pytest.mark.parametrize("path", ["/../secret.txt", "/%2e%2e/secret.txt", "/js/../../secret.txt",
                                  "/..%2fsecret.txt", "/%2e%2e%2fsecret.txt"])
def test_path_traversal_blocked(server, path):
    port, _ = server
    st, _h, body = _req(port, "GET", path)
    assert st in (400, 403, 404) and b"top secret" not in body


def test_post_event_and_ingest(server):
    port, hub = server
    st, _h, body = _req(port, "POST", "/api/event?source=claude",
                        {"hook_event_name": "PreToolUse", "session_id": "x"})
    assert st == 204 and body == b""  # empty 2xx = "no decision" for Claude http hooks
    st, _h, body = _req(port, "POST", "/api/ingest", {"spans": []})
    assert st == 200 and json.loads(body)["ok"]


def test_foreign_origin_rejected(server):
    port, _ = server
    st, _h, _ = _req(port, "POST", "/api/event", {"a": 1}, {"Origin": "https://evil.example"})
    assert st == 403
    st, _h, _ = _req(port, "POST", "/api/event", {"a": 1}, {"Origin": f"http://localhost:{port}"})
    assert st == 204


def test_foreign_host_rejected(server):
    port, _ = server  # DNS rebinding: attacker.com resolving to 127.0.0.1
    st, _h, _ = _req(port, "GET", "/api/agents", headers={"Host": "attacker.example:8765"})
    assert st == 403
    st, _h, _ = _req(port, "POST", "/api/event", {"a": 1}, {"Host": "attacker.example"})
    assert st == 403


def test_body_limit_and_bad_json(server):
    port, _ = server
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.putrequest("POST", "/api/event", skip_host=True)
    c.putheader("Host", f"127.0.0.1:{port}")
    c.putheader("Content-Type", "application/json")
    c.putheader("Content-Length", str(1024 * 1024 + 10))
    c.endheaders()  # server must refuse from the header alone, without reading 1 MB
    assert c.getresponse().status == 413
    c.close()
    st, _h, _ = _req(port, "POST", "/api/event", b"{not json")
    assert st == 400


def test_totals_counts():
    from neko.server import _totals
    t = _totals([{"state": "working", "kind": "session", "cost_10m": 0.5, "cost_usd": 2},
                 {"state": "waiting", "kind": "subagent", "cost_10m": 0.25, "cost_usd": 1}])
    assert t["agents"] == 2 and t["working"] == 1 and t["waiting"] == 1 and t["subagents"] == 1
    assert t["cost_10m"] == 0.75 and t["cost_total"] == 3


def test_demo_world_evolves():
    from neko.demo import DemoWorld, tiny_persona
    w = DemoWorld(seed=1)
    before = {a.id: a.context_tokens for a in w.collect()}
    w._last_tick -= 5
    after = {a.id: a.context_tokens for a in w.collect()}
    assert before and any(after.get(k, 0) != v for k, v in before.items())
    p = tiny_persona("Research toon shaders")
    assert p["role"] == "research" and p["fur"].startswith("#")


def test_status_table_renders():
    from neko.cli import render_table
    from neko.demo import DemoWorld
    from neko.server import _totals
    ds = [a.to_dict() for a in DemoWorld(seed=2).collect()]
    out = render_table({"agents": ds, "totals": _totals(ds)})
    assert "working" in out and "agents" in out


# ---------------------------------------------------------------- hooks

FOREIGN = {
    "model": "opus",
    "hooks": {
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "~/guard.sh"}]}],
        "Stop": [{"hooks": [{"type": "command", "command": "say done"}]}],
    },
}


def test_hooks_install_uninstall_preserves_and_idempotent(tmp_path):
    from neko import hooks
    settings = tmp_path / "claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps(FOREIGN, indent=2))

    res = hooks.install(port=8765)
    assert res["claude"][1] is True
    assert (tmp_path / "claude" / "settings.json.neko-bak").read_text() == json.dumps(FOREIGN, indent=2)
    d = json.loads(settings.read_text())
    assert d["model"] == "opus"
    assert d["hooks"]["PreToolUse"][0] == FOREIGN["hooks"]["PreToolUse"][0]
    assert d["hooks"]["Stop"][0] == FOREIGN["hooks"]["Stop"][0]
    for ev in hooks.CLAUDE_EVENTS:
        assert any(hooks.is_neko_hook(h) for g in d["hooks"][ev] for h in g["hooks"]), ev
    ss = d["hooks"]["SessionStart"][-1]["hooks"][0]
    assert ss["type"] == "command" and "neko-hook" in ss["command"]  # SessionStart: command only
    pt = d["hooks"]["PreToolUse"][-1]["hooks"][0]
    assert pt["type"] == "http" and pt["url"].startswith("http://127.0.0.1:8765/api/event")

    text1 = settings.read_text()
    assert hooks.install(port=8765)["claude"][1] is False  # idempotent
    assert settings.read_text() == text1
    assert set(hooks.status()["claude"][1]) == set(hooks.CLAUDE_EVENTS)

    hooks.install(port=9999)  # port change replaces, doesn't duplicate
    d = json.loads(settings.read_text())
    n = sum(hooks.is_neko_hook(h) for g in d["hooks"]["PreToolUse"] for h in g["hooks"])
    assert n == 1 and "9999" in d["hooks"]["PreToolUse"][-1]["hooks"][0]["url"]

    assert hooks.uninstall()["claude"][1] is True
    assert json.loads(settings.read_text()) == FOREIGN
    # the backup still holds the user's original file, not the neko-hooked one
    assert json.loads((tmp_path / "claude" / "settings.json.neko-bak").read_text()) == FOREIGN
    assert hooks.uninstall()["claude"][1] is False
    assert hooks.status()["claude"][1] == []


def test_hooks_codex_and_missing_file(tmp_path):
    from neko import hooks
    res = hooks.install(claude=True, codex=True, port=8765)
    assert res["claude"][1] and res["codex"][1]
    d = json.loads((tmp_path / "codex" / "hooks.json").read_text())
    assert all(g["hooks"][0]["type"] == "command" for ev in hooks.CODEX_EVENTS for g in d["hooks"][ev])
    hooks.uninstall()
    assert json.loads((tmp_path / "codex" / "hooks.json").read_text()) == {}
    assert json.loads((tmp_path / "claude" / "settings.json").read_text()) == {}


def test_hooks_refuse_invalid_json(tmp_path):
    from neko import hooks
    s = tmp_path / "claude" / "settings.json"
    s.parent.mkdir(parents=True)
    s.write_text("{ broken")
    with pytest.raises(ValueError):
        hooks.install()
    assert s.read_text() == "{ broken"


def test_hook_script_always_exits_zero_silently():
    script = ROOT / "scripts" / "neko-hook.py"
    r = subprocess.run([sys.executable, str(script), "--source", "codex", "--port", str(_free_port())],
                       input=b'{"hook_event_name":"Stop"}', capture_output=True, timeout=10)
    assert r.returncode == 0 and r.stdout == b"" and r.stderr == b""
    r = subprocess.run([sys.executable, str(script)], input=b"garbage", capture_output=True, timeout=10)
    assert r.returncode == 0 and r.stdout == b""


def test_hook_script_reaches_daemon(server):
    port, hub = server
    got = []
    hub.ingest = lambda kind, payload: got.append((kind, payload))
    script = ROOT / "scripts" / "neko-hook.py"
    r = subprocess.run([sys.executable, str(script), "--source", "codex", "--port", str(port)],
                       input=b'{"hook_event_name":"Stop","session_id":"s1"}', capture_output=True,
                       timeout=10)
    assert r.returncode == 0
    assert got and got[0][0] == "event" and got[0][1]["neko_source"] == "codex"


# ---------------------------------------------------------------- launchd

def test_launchd_install_writes_plist(tmp_path):
    from neko import cli
    assert cli.main(["install", "--port", "8799"]) == 0
    p = tmp_path / "LaunchAgents" / "com.neko-agent-monitor.plist"
    d = plistlib.loads(p.read_bytes())
    assert d["ProgramArguments"][1:] == ["-m", "neko", "serve"]
    assert d["KeepAlive"] and d["RunAtLoad"]
    assert d["EnvironmentVariables"]["NEKO_PORT"] == "8799"
    assert cli.main(["uninstall"]) == 0 and not p.exists()


def test_identical_prompts_get_distinct_names():
    from neko.model import Agent
    from neko.server import Hub
    hub = Hub(demo=True)
    mk = lambda i: Agent(id=f"claude:s{i}", source="claude", creation_prompt="same task",
                         started_at=100 + i)
    agents = [mk(2), mk(1), mk(3)]
    hub.attach_personas(agents)
    names = [a.persona["name"] for a in sorted(agents, key=lambda a: a.id)]
    assert len(set(names)) == 3
    # stable across polls; the oldest keeps the "natural" name
    again = [mk(3), mk(1), mk(2)]
    hub.attach_personas(again)
    assert [a.persona["name"] for a in sorted(again, key=lambda a: a.id)] == names
    solo = Hub(demo=True)
    one = [mk(1)]
    solo.attach_personas(one)
    assert names[0] == one[0].persona["name"]
