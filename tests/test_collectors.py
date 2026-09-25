"""Offline tests for neko.collectors against synthetic fixture trees.

tests/fixtures/claude_home — sessions registry + transcripts (+ subagents)
tests/fixtures/codex_home  — rollouts (copied into sessions/<today>/ at test time)
Processes are faked by monkeypatching ``neko.procs``.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime, timezone

import pytest

from neko import procs
from neko import collectors
from neko.collectors import claude, codex, common, events, sdk
from neko.model import DONE, ERROR, IDLE, SLEEPING, WAITING, WORKING

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
START = "Sun Sep 20 10:00:00 2026"
SA = "11111111-aaaa-4aaa-8aaa-000000000001"
SB = "22222222-bbbb-4bbb-8bbb-000000000002"
SC = "33333333-cccc-4ccc-8ccc-000000000003"
SD = "44444444-dddd-4ddd-8ddd-000000000004"
X1 = "019a0000-0000-7000-8000-000000000001"
X2 = "019a0000-0000-7000-8000-000000000002"
X3 = "019a0000-0000-7000-8000-000000000003"
X4 = "019a0000-0000-7000-8000-000000000004"


def _proc(pid, cmd="claude", start=START, ppid=1):
    return procs.Proc(pid, ppid, procs.parse_lstart(start), cmd)


@pytest.fixture
def env(tmp_path, monkeypatch):
    ch = tmp_path / "claude"
    shutil.copytree(os.path.join(FIX, "claude_home"), ch)
    xh = tmp_path / "codex"
    os.makedirs(xh)
    shutil.copy(os.path.join(FIX, "codex_home", "session_index.jsonl"), xh)
    d = datetime.now()
    day = xh / "sessions" / f"{d.year:04d}" / f"{d.month:02d}" / f"{d.day:02d}"
    os.makedirs(day)
    now = time.time()
    for f in os.listdir(os.path.join(FIX, "codex_home", "rollouts")):
        dst = day / f
        shutil.copy(os.path.join(FIX, "codex_home", "rollouts", f), dst)
        old = X3 in f
        os.utime(dst, (now - 300, now - 300) if old else (now - 5, now - 5))
    sub = ch / "projects" / "-work-alpha" / SA / "subagents"
    for name, age in (("aactive", 10), ("bdone", 30), ("cold", 400)):
        os.utime(sub / f"agent-{name}.jsonl", (now - age, now - age))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(ch))
    monkeypatch.setenv("CODEX_HOME", str(xh))
    table = {p: _proc(p) for p in (90001, 90002, 90003, 90004)}
    table[90006] = _proc(90006)  # alive, but registry procStart differs → pid reuse
    state = {"table": table, "files": {}, "cwd": {}}
    monkeypatch.setattr(procs, "processes", lambda max_age=2.0: state["table"])
    monkeypatch.setattr(procs, "open_files", lambda pid, ttl=15.0: state["files"].get(pid, []))
    monkeypatch.setattr(procs, "cwd_of", lambda pid: state["cwd"].get(pid, ""))
    collectors.reset()
    codex._db_cache.update(ts=0.0, home=None)
    codex._index_cache.update(key=None)
    yield {"claude": ch, "codex": xh, "day": day, "state": state, "now": now}
    collectors.reset()


def by_id(agents):
    return {a.id: a for a in agents}


def _set_reg(ch, pid, **kw):
    p = ch / "sessions" / f"{pid}.json"
    d = json.loads(p.read_text())
    d.update(kw)
    p.write_text(json.dumps(d))


def _append(path, *recs):
    with open(path, "a") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ------------------------------------------------------------------ procs

def test_parse_lstart_and_is_alive():
    t = procs.parse_lstart("Thu Sep 24 23:21:29 2026")
    assert t == procs.parse_lstart("Thu  Sep 24  23:21:29   2026")
    assert datetime.fromtimestamp(t, timezone.utc).strftime("%H:%M") == "23:21"
    table = {5: procs.Proc(5, 1, t, "claude", ticks="12345")}
    assert procs.is_alive(5, "Thu Sep 24 23:21:29 2026", table)
    assert procs.is_alive(5, "Thu Sep 24 23:21:30 2026", table)   # 1 s tolerance
    assert not procs.is_alive(5, "Thu Sep 24 20:00:00 2026", table)
    assert procs.is_alive(5, "12345", table) and not procs.is_alive(5, "999", table)
    assert not procs.is_alive(6, None, table)
    assert procs.is_alive(5, None, table)


def test_real_process_table_contains_self():
    table = procs.processes(max_age=0)
    me = table.get(os.getpid())
    assert me is not None and me.start and abs(me.start - time.time()) < 3 * 86400
    assert "python" in me.command.lower() or "pytest" in me.command.lower()


# ------------------------------------------------------------------ claude

def test_claude_registry_liveness(env):
    ids = by_id(claude.collect())
    assert f"claude:{SA}" in ids and f"claude:{SB}" in ids and f"claude:{SD}" in ids
    assert not any("55555555" in i for i in ids)   # dead pid
    assert not any("66666666" in i for i in ids)   # pid reused (procStart mismatch)


def test_claude_states_and_details(env):
    ids = by_id(claude.collect())
    a = ids[f"claude:{SA}"]
    assert a.state == WORKING and a.state_detail == "Bash: Run tests"
    assert a.kind == "session" and a.pid == 90001 and a.project == "alpha"
    assert a.title == "Todo app build"
    assert (a.progress_done, a.progress_total) == (1, 3)
    assert a.tool_count == 2 and a.last_tool == "Bash: Run tests"
    b = ids[f"claude:{SB}"]
    assert b.state == WAITING and b.state_detail == "permission prompt"
    c = ids[f"claude:{SC}"]
    assert c.state == SLEEPING   # idle, nothing for days
    _set_reg(env["claude"], 90003, statusUpdatedAt=int(time.time() * 1000))
    assert by_id(claude.collect())[f"claude:{SC}"].state == IDLE


def test_claude_api_error_is_error_state(env):
    path = env["claude"] / "projects" / "-work-gamma" / f"{SC}.jsonl"
    _append(path, {"type": "system", "subtype": "api_error", "timestamp": _iso(time.time() - 5),
                   "error": {"formatted": "API overloaded"}, "retryAttempt": 10, "maxRetries": 10})
    c = by_id(claude.collect())[f"claude:{SC}"]
    assert c.state == ERROR and "overloaded" in c.state_detail


def test_creation_prompt_rules(env):
    ids = by_id(claude.collect())
    assert ids[f"claude:{SA}"].creation_prompt == "Build a tiny todo app with tests"
    assert ids[f"claude:{SB}"].creation_prompt == "Deploy the staging server"
    assert ids[f"claude:{SD}"].creation_prompt.startswith("You are the nightly report generator")
    assert ids[f"claude:{SA}"].last_prompt == "Build a tiny todo app with tests"


def test_context_tokens_limits_and_cost(env):
    ids = by_id(claude.collect())
    a = ids[f"claude:{SA}"]
    assert a.context_tokens == 5 + 300000 + 10000       # last non-sidechain usage
    assert a.context_limit == 1_000_000                 # sonnet-5
    assert a.tokens_in == 120010 + 310005 and a.tokens_out == 700   # duplicate msg id counted once
    assert a.cost_usd > 0
    assert ids[f"claude:{SB}"].context_limit == 200_000  # opus-4-6
    assert ids[f"claude:{SD}"].context_limit == 1_000_000  # opus-5-5
    c = ids[f"claude:{SC}"]
    assert c.context_limit == 200_000 and c.cost_usd == pytest.approx(0.5)  # cost-state wins
    assert common.claude_context_limit("claude-opus-4-6[1m]") == 1_000_000
    assert common.claude_context_limit("claude-fable-5-1") == 1_000_000
    assert common.claude_context_limit("claude-haiku-4-5") == 200_000


def test_claude_subagents(env):
    ids = by_id(claude.collect())
    s = ids[f"claude:{SA}:aactive"]
    assert s.kind == "subagent" and s.parent_id == f"claude:{SA}"
    assert s.state == WORKING and s.title == "Find flaky test"
    assert s.creation_prompt == "Investigate the flaky test in test_api.py"
    assert s.state_detail == "Grep: flaky"
    d = ids[f"claude:{SA}:bdone"]
    assert d.state == DONE and d.last_text.startswith("Summary")
    assert f"claude:{SA}:cold" not in ids   # handed back long ago


def test_ingest_event_overrides_while_fresher(env):
    collectors.ingest_event({"session_id": SA, "hook_event_name": "PermissionRequest",
                             "tool_name": "Edit", "tool_input": {"file_path": "/work/alpha/app.py"},
                             "transcript_path": "/x.jsonl", "cwd": "/work/alpha"})
    a = by_id(collectors.collect_all())[f"claude:{SA}"]
    assert a.state == WAITING and a.state_detail == "permission: Edit: /work/alpha/app.py"
    collectors.ingest_event({"session_id": SA, "hook_event_name": "Notification",
                             "notification_type": "idle_prompt"})
    assert by_id(collectors.collect_all())[f"claude:{SA}"].state == IDLE
    collectors.ingest_event({"session_id": SA, "hook_event_name": "PreToolUse", "tool_name": "Bash",
                             "tool_input": {"command": "ls -la"}})
    a = by_id(collectors.collect_all())[f"claude:{SA}"]
    assert a.state == WORKING and a.state_detail == "Bash: ls -la"
    collectors.ingest_event({"session_id": SA, "hook_event_name": "StopFailure", "error": "rate_limit"})
    a = by_id(collectors.collect_all())[f"claude:{SA}"]
    assert a.state == ERROR and "rate_limit" in a.state_detail
    # newer file data (registry update) beats the older hook
    _set_reg(env["claude"], 90001, status="busy", statusUpdatedAt=int((time.time() + 5) * 1000))
    assert by_id(collectors.collect_all())[f"claude:{SA}"].state == WORKING
    # bad payloads never raise
    collectors.ingest_event(None)
    collectors.ingest_event({"hook_event_name": "Stop"})
    collectors.ingest_event({"session_id": 1, "hook_event_name": 2, "tool_input": "x"})


def test_hook_subagent_start_before_file(env):
    collectors.ingest_event({"session_id": SA, "hook_event_name": "SubagentStart",
                             "agent_id": "zz9", "agent_type": "Explore"})
    ids = by_id(collectors.collect_all())
    s = ids[f"claude:{SA}:zz9"]
    assert s.state == WORKING and s.parent_id == f"claude:{SA}"
    collectors.ingest_event({"session_id": SA, "hook_event_name": "SubagentStop", "agent_id": "zz9"})
    ids = by_id(collectors.collect_all())
    assert ids.get(f"claude:{SA}:zz9") is None or ids[f"claude:{SA}:zz9"].state == DONE


def test_exited_session_says_goodbye(env, monkeypatch):
    assert by_id(collectors.collect_all())[f"claude:{SB}"].state == WAITING
    os.remove(env["claude"] / "sessions" / "90002.json")
    b = by_id(collectors.collect_all())[f"claude:{SB}"]
    assert b.state == DONE
    monkeypatch.setattr(collectors, "GONE_KEEP", -1)
    assert f"claude:{SB}" not in by_id(collectors.collect_all())


def test_repeat_flag_makes_working_session_dizzy(env):
    path = env["claude"] / "projects" / "-work-alpha" / f"{SA}.jsonl"
    t = time.time()
    recs = []
    for i in range(9):
        recs.append({"type": "assistant", "timestamp": _iso(t - 60 + i), "message": {
            "id": f"r{i}", "model": "claude-sonnet-5", "content": [
                {"type": "tool_use", "id": f"rt{i}", "name": "Bash", "input": {"command": "sleep 5; cat log"}}],
            "usage": {"input_tokens": 1, "output_tokens": 1}}})
        recs.append({"type": "user", "timestamp": _iso(t - 60 + i), "message": {"content": [
            {"type": "tool_result", "tool_use_id": f"rt{i}"}]}})
    _append(path, *recs)
    a = by_id(claude.collect())[f"claude:{SA}"]
    fids = {f["id"] for f in a.flags}
    assert {"repeat", "poll"} <= fids
    assert a.state == ERROR and "repeated" in a.state_detail


def test_huge_transcript_head_scan_and_tail(env, monkeypatch):
    monkeypatch.setattr(claude, "BIG_FILE", 5000)
    monkeypatch.setattr(claude, "TAIL_BYTES", 2000)
    path = env["claude"] / "projects" / "-work-alpha" / f"{SA}.jsonl"
    filler = [{"type": "attachment", "attachment": {"type": "x", "text": "y" * 200}} for _ in range(60)]
    _append(path, *filler)
    _append(path, {"type": "ai-title", "aiTitle": "Late title"})
    a = by_id(claude.collect())[f"claude:{SA}"]
    assert a.creation_prompt == "Build a tiny todo app with tests"
    assert a.title == "Late title"
    assert any(f["id"] == "partial" for f in a.flags)


def test_collect_all_survives_collector_failure(env, monkeypatch):
    def boom(now=None):
        raise RuntimeError("x")
    monkeypatch.setattr(claude, "collect", boom)
    out = collectors.collect_all()
    assert out and all(a.source != "claude" for a in out)


def test_incremental_parse_reads_only_new_bytes(env):
    path = str(env["claude"] / "projects" / "-work-alpha" / f"{SA}.jsonl")
    claude.collect()
    tr = claude._CACHE[path]
    off = tr.offset
    assert off == os.path.getsize(path)
    _append(path, {"type": "ai-title", "aiTitle": "Renamed"})
    with open(path, "a") as f:
        f.write('{"type": "ai-title", "aiTi')   # partial line: not consumed yet
    a = by_id(claude.collect())[f"claude:{SA}"]
    assert a.title == "Renamed" and tr.offset > off and tr.offset < os.path.getsize(path)


# ------------------------------------------------------------------ codex

def test_codex_recent_rollouts(env):
    ids = by_id(codex.collect())
    x1 = ids[f"codex:{X1}"]
    assert x1.source == "codex" and x1.kind == "session"
    assert x1.state == WORKING and x1.state_detail == "shell: make build"
    assert x1.title == "Parser refactor"
    assert x1.creation_prompt == "Refactor the parser module"   # <environment_context> skipped
    assert x1.model == "gpt-5-codex" and x1.project == "cx1"
    assert x1.context_tokens == 60000 and x1.context_limit == 258400
    assert x1.tokens_in == 110000 and x1.tokens_out == 2000
    expected = common.openai_cost("gpt-5-codex", 50000, 25000, 1000) + \
        common.openai_cost("gpt-5-codex", 60000, 30000, 1000)
    assert x1.cost_usd == pytest.approx(expected, abs=1e-4)          # duplicate token_count ignored
    assert x1.tool_count == 2
    x2 = ids[f"codex:{X2}"]
    assert x2.state == WAITING and x2.state_detail == "permission: rm -rf build"
    x4 = ids[f"codex:{X4}"]
    assert x4.kind == "subagent" and x4.parent_id == f"codex:{X1}"
    assert x4.creation_prompt == "Write unit tests for parser.py"
    assert f"codex:{X3}" not in ids   # stale, no process


def _x3(env):
    return str(next(p for p in env["day"].iterdir() if X3 in p.name))


def test_codex_process_open_rollout(env):
    st = env["state"]
    st["table"][91000] = _proc(91000, "node /opt/homebrew/bin/codex --yolo", ppid=500)
    st["table"][91001] = _proc(91001, "/opt/homebrew/lib/node_modules/@openai/codex/vendor/"
                                      "aarch64-apple-darwin/codex/codex --yolo", ppid=91000)
    st["table"][91002] = _proc(91002, "/usr/local/bin/codex mcp-server", ppid=500)
    st["files"][91001] = ["/dev/null", _x3(env)]
    ids = by_id(codex.collect())
    x3 = ids[f"codex:{X3}"]
    assert x3.pid == 91001 and x3.state == IDLE and x3.last_text == "All done."
    assert not any(a.pid in (91000, 91002) for a in ids.values())


def test_codex_process_matched_by_cwd(env):
    st = env["state"]
    st["table"][92000] = _proc(92000, "codex", start="Sun Sep 20 09:00:00 2026")
    st["cwd"][92000] = "/work/cx3"
    ids = by_id(codex.collect())
    assert ids[f"codex:{X3}"].pid == 92000


def test_codex_process_without_rollout_shows_idle(env):
    st = env["state"]
    st["table"][93000] = _proc(93000, "codex")
    st["cwd"][93000] = "/work/nothing-here"
    a = by_id(codex.collect())["codex:pid-93000"]
    assert a.state == IDLE and a.project == "nothing-here"


def test_codex_hook_override(env):
    collectors.ingest_event({"session_id": X1, "hook_event_name": "PermissionRequest",
                             "tool_name": "shell", "tool_input": {"command": "git push"},
                             "transcript_path": "/Users/x/.codex/sessions/rollout-x.jsonl"})
    a = by_id(collectors.collect_all())[f"codex:{X1}"]
    assert a.state == WAITING and "git push" in a.state_detail
    assert events.source_of({"transcript_path": "/h/.codex/sessions/2026/rollout-1.jsonl"}) == "codex"


# ------------------------------------------------------------------ openai agents sdk

def _span(sid, typ, parent=None, ended=False, **data):
    d = {"object": "trace.span", "id": sid, "trace_id": "trace_abc", "parent_id": parent,
         "started_at": "2026-09-24T10:00:00+00:00",
         "ended_at": "2026-09-24T10:00:05+00:00" if ended else None,
         "span_data": dict(type=typ, **data), "error": None}
    return d


def test_ingest_sdk_run_lifecycle(env):
    collectors.ingest_sdk({"event": "trace_start", "item": {
        "object": "trace", "id": "trace_abc", "workflow_name": "Travel planner"}})
    collectors.ingest_sdk({"event": "span_start", "item": _span("span_a", "agent", name="Planner")})
    collectors.ingest_sdk({"data": [
        _span("span_g", "generation", parent="span_a", ended=True, model="gpt-4.1",
              input=[{"role": "system", "content": "sys"},
                     {"role": "user", "content": "Plan a 3 day trip to Kyoto"}],
              usage={"input_tokens": 1200, "output_tokens": 300}),
        _span("span_f", "function", parent="span_a", name="search_hotels"),
        _span("span_sub", "agent", parent="span_f", name="HotelScout"),
        _span("span_g2", "generation", parent="span_sub", ended=True, model="gpt-4.1-mini",
              input=[{"role": "user", "content": "Find ryokans near Gion"}],
              usage={"input_tokens": 400, "output_tokens": 50}),
    ]})
    ids = by_id(collectors.collect_all())
    run = ids["openai-sdk:trace_abc"]
    assert run.kind == "run" and run.source == "openai-sdk"
    assert run.title == "Travel planner"
    assert run.creation_prompt == "Plan a 3 day trip to Kyoto"
    assert run.state == WORKING and run.state_detail == "search_hotels"
    assert run.tokens_in == 1600 and run.tokens_out == 350 and run.tool_count == 1
    assert run.context_limit > 1_000_000 - 1
    sub = ids["openai-sdk:span_sub"]
    assert sub.kind == "subagent" and sub.parent_id == run.id and sub.state == WORKING
    assert sub.creation_prompt == "Find ryokans near Gion" and sub.tokens_in == 400
    # finish
    for sid, typ in (("span_sub", "agent"), ("span_f", "function"), ("span_a", "agent")):
        collectors.ingest_sdk({"event": "span_end", "item": _span(sid, typ, ended=True)})
    assert by_id(collectors.collect_all())["openai-sdk:span_sub"].state == DONE
    collectors.ingest_sdk({"event": "trace_end", "item": {"object": "trace", "id": "trace_abc"}})
    ids = by_id(collectors.collect_all())
    assert ids["openai-sdk:trace_abc"].state == DONE
    # expires after ~60 s, no goodbye ghost needed (already done)
    assert not [a for a in sdk.collect(time.time() + 61) if a.source == "openai-sdk"]
    collectors.ingest_sdk("garbage")
    collectors.ingest_sdk([{"object": "trace.span"}])


def test_sdk_error_span(env):
    collectors.ingest_sdk([{"object": "trace", "id": "trace_err", "workflow_name": "W"},
                           {"object": "trace.span", "id": "s1", "trace_id": "trace_err",
                            "span_data": {"type": "function", "name": "f"},
                            "error": {"message": "tool exploded"}}])
    run = by_id(collectors.collect_all())["openai-sdk:trace_err"]
    assert run.state == ERROR and "exploded" in run.state_detail


def test_sdk_client_never_raises():
    from neko.collectors.sdk_client import NekoTracingProcessor
    p = NekoTracingProcessor(url="http://127.0.0.1:9/api/ingest", timeout=0.1)

    class T:
        def export(self):
            return {"object": "trace", "id": "trace_x"}

    class Bad:
        def export(self):
            raise ValueError

    p.on_trace_start(T())
    p.on_span_end(Bad())
    p.force_flush(timeout=2)
    p.shutdown()


def test_to_dict_is_json_serialisable(env):
    for a in collectors.collect_all():
        json.dumps(a.to_dict())
