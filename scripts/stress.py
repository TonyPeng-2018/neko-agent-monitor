"""Usage: python scripts/stress.py   (N_CLAUDE=40 by default; never touches your real ~/.claude)

Stress test for the neko daemon on an isolated instance (own port, temp NEKO_HOME / ~/.claude / ~/.codex).

Phases
  A  Claude-style load: N real `sleep` processes registered as live Claude sessions with
     synthetic transcripts (+ subagents, + one 64 MB transcript) → collector cost.
  B  SDK ramp: add agents via /api/ingest up to 50 / 150 / 300 → persona + payload cost.
  C  Hook flood: many threads POST /api/event as fast as possible → throughput, errors.
  D  SSE fan-out: 50 concurrent stream clients must all receive a pushed change.
Resources (CPU %, RSS of daemon + embed child) are sampled throughout.
"""
import json, os, random, shutil, signal, statistics, subprocess, sys, tempfile, threading, time
import urllib.request, uuid
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.environ.get("NEKO_PY") or (f"{REPO}/.venv/bin/python" if os.path.exists(f"{REPO}/.venv/bin/python") else sys.executable)
PORT = int(os.environ.get("STRESS_PORT", 8790))
BASE = f"http://127.0.0.1:{PORT}"
N_CLAUDE = int(os.environ.get("N_CLAUDE", 40))
TMP = tempfile.mkdtemp(prefix="neko-stress-")
CL, CX, NH = f"{TMP}/claude", f"{TMP}/codex", f"{TMP}/nekohome"
for d in (f"{CL}/sessions", f"{CL}/projects", f"{CX}/sessions", NH):
    os.makedirs(d, exist_ok=True)
report = {}


def get(path, timeout=10):
    t = time.perf_counter()
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        body = r.read()
    return body, (time.perf_counter() - t) * 1000


def post(path, obj, timeout=5):
    req = urllib.request.Request(BASE + path, json.dumps(obj).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"


# ------------------------------------------------------------------ resources
samples = []
stop_sampling = threading.Event()


def proc_tree(pid):
    out = subprocess.run(["ps", "-axo", "pid=,ppid=,%cpu=,rss="], capture_output=True, text=True).stdout
    rows = [list(map(float, l.split())) for l in out.splitlines() if l.strip()]
    kids, tree = {}, [pid]
    for p, pp, c, r in rows:
        kids.setdefault(int(pp), []).append(int(p))
    i = 0
    while i < len(tree):
        tree += kids.get(tree[i], [])
        i += 1
    tot_cpu = sum(c for p, pp, c, r in rows if int(p) in tree)
    tot_rss = sum(r for p, pp, c, r in rows if int(p) in tree) / 1024
    return tot_cpu, tot_rss


def sampler(pid, phase):
    while not stop_sampling.is_set():
        try:
            c, r = proc_tree(pid)
            samples.append((phase[0], c, r))
        except Exception:
            pass
        time.sleep(0.5)


def res(phase):
    s = [x for x in samples if x[0] == phase]
    if not s:
        return {}
    return {"cpu_avg%": round(statistics.mean(x[1] for x in s), 1), "cpu_max%": round(max(x[1] for x in s), 1),
            "rss_max_MB": round(max(x[2] for x in s))}


# ------------------------------------------------------------------ phase A fixtures
def ps_lstart(pid):
    out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True,
                         env={"TZ": "UTC", "LC_ALL": "C"}).stdout
    return " ".join(out.split())


def transcript_lines(sid, cwd, n, prompt):
    now = time.time() - n
    yield {"type": "user", "sessionId": sid, "cwd": cwd, "timestamp": iso(now), "uuid": str(uuid.uuid4()),
           "message": {"role": "user", "content": prompt}}
    yield {"type": "ai-title", "aiTitle": prompt[:40]}
    for i in range(n):
        mid = f"msg_{sid[:8]}_{i}"
        yield {"type": "assistant", "sessionId": sid, "timestamp": iso(now + i), "uuid": str(uuid.uuid4()),
               "message": {"id": mid, "model": "claude-opus-5-5", "role": "assistant",
                           "usage": {"input_tokens": 3, "cache_read_input_tokens": 20000 + i * 40,
                                     "cache_creation_input_tokens": 200, "output_tokens": 150},
                           "content": [{"type": "text", "text": "working on it " * 8},
                                       {"type": "tool_use", "id": f"tu_{mid}", "name": "Bash",
                                        "input": {"command": f"echo step {i}", "description": f"step {i}"}}]}}
        yield {"type": "user", "sessionId": sid, "timestamp": iso(now + i + 0.5), "uuid": str(uuid.uuid4()),
               "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"tu_{mid}",
                                                         "content": "ok " * 20}]}}


PROMPTS = ["调研类似产品", "fix the flaky integration tests", "写一份部署文档", "design a pastel landing page",
           "analyze last month's sales csv", "refactor the auth module", "寻找可爱的3D模型", "plan the sprint",
           "translate the README into Japanese", "benchmark the embedding model"]
sleepers = []


def build_claude(n):
    t0 = time.time()
    total = 0
    for i in range(n):
        sp = subprocess.Popen(["sleep", "3600"])
        sleepers.append(sp)
        sid = str(uuid.uuid4())
        cwd = f"/work/proj{i % 7}"
        slug = cwd.replace("/", "-")
        pdir = f"{CL}/projects/{slug}"
        os.makedirs(pdir, exist_ok=True)
        lines = 3000 if i == 0 else random.randint(200, 1500)
        with open(f"{pdir}/{sid}.jsonl", "w") as f:
            for rec in transcript_lines(sid, cwd, lines, PROMPTS[i % len(PROMPTS)] + f" #{i}"):
                f.write(json.dumps(rec) + "\n")
        if i == 0:  # one monster transcript → partial (head + tail) parsing path
            with open(f"{pdir}/{sid}.jsonl", "a") as f:
                pad = json.dumps({"type": "system", "subtype": "informational", "content": "x" * 4000}) + "\n"
                while f.tell() < 64 * 1024 * 1024:
                    f.write(pad)
        total += os.path.getsize(f"{pdir}/{sid}.jsonl")
        # two subagents each
        sdir = f"{pdir}/{sid}/subagents"
        os.makedirs(sdir, exist_ok=True)
        for k in range(2):
            aid = uuid.uuid4().hex[:17]
            with open(f"{sdir}/agent-{aid}.jsonl", "w") as f:
                for rec in transcript_lines(sid, cwd, 100, f"subtask {k} of session {i}"):
                    rec["isSidechain"] = True
                    f.write(json.dumps(rec) + "\n")
            json.dump({"agentType": "general-purpose", "description": f"helper {k}"}, open(f"{sdir}/agent-{aid}.meta.json", "w"))
        status = random.choice(["busy", "busy", "idle", "waiting"])
        reg = {"pid": sp.pid, "sessionId": sid, "cwd": cwd, "startedAt": int(time.time() * 1000),
               "procStart": ps_lstart(sp.pid), "kind": "interactive", "entrypoint": "cli",
               "status": status, "statusUpdatedAt": int(time.time() * 1000)}
        if status == "waiting":
            reg["waitingFor"] = "permission prompt"
        json.dump(reg, open(f"{CL}/sessions/{sp.pid}.json", "w"))
    return round(time.time() - t0, 1), round(total / 1e6)


# ------------------------------------------------------------------ run
phase = ["setup"]
secs, mb = build_claude(N_CLAUDE)
report["fixtures"] = {"claude_sessions": N_CLAUDE, "subagents": 2 * N_CLAUDE, "transcripts_MB": mb, "build_s": secs}

env = dict(os.environ, NEKO_PORT=str(PORT), NEKO_HOME=NH, CLAUDE_CONFIG_DIR=CL, CODEX_HOME=CX, HF_HUB_OFFLINE="1")
log = open(f"{TMP}/daemon.log", "w")
daemon = subprocess.Popen([PY, "-m", "neko", "serve", "--port", str(PORT)], cwd=REPO, env=env, stdout=log, stderr=log)
threading.Thread(target=sampler, args=(daemon.pid, phase), daemon=True).start()


def wait_agents(pred, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            d = json.loads(get("/api/agents")[0])
            if pred(d):
                return time.time() - t0, d
        except Exception:
            pass
        time.sleep(0.25)
    return None, None


def latency(n=40):
    lat, size = [], 0
    for _ in range(n):
        b, ms = get("/api/agents")
        lat.append(ms)
        size = len(b)
    lat.sort()
    return {"p50_ms": round(lat[len(lat) // 2], 1), "p95_ms": round(lat[int(len(lat) * .95)], 1), "payload_KB": round(size / 1024)}


try:
    # ---- A
    phase[0] = "A"
    want = N_CLAUDE * 3
    t, d = wait_agents(lambda d: len(d["agents"]) >= want)
    time.sleep(6)
    h = json.loads(get("/api/health")[0])
    d = json.loads(get("/api/agents")[0])
    st = {}
    for a in d["agents"]:
        st[a["state"]] = st.get(a["state"], 0) + 1
    partial = sum(1 for a in d["agents"] if any(f["id"] == "partial" for f in a["flags"]))
    names = [a["persona"].get("name") for a in d["agents"] if a["state"] != "done"]
    report["A_claude"] = {"agents": len(d["agents"]), "first_full_snapshot_s": round(t, 1) if t else "TIMEOUT",
                          "states": st, "partial_flagged": partial, "dup_names": len(names) - len(set(names)),
                          "collect_ms": h["collect_ms"], **latency(), **res("A")}

    # ---- B
    traces = []
    for target in (50, 150, 300):
        phase[0] = f"B{target}"
        t0 = time.time()
        while len(traces) < target:
            tid = "trace_" + uuid.uuid4().hex
            post("/api/ingest", {"event": "trace_start", "item": {"object": "trace", "id": tid,
                                 "workflow_name": f"{random.choice(PROMPTS)} (sdk {len(traces)})"}})
            post("/api/ingest", {"event": "span_start", "item": {"object": "trace.span", "id": "span_" + uuid.uuid4().hex[:12],
                                 "trace_id": tid, "started_at": iso(time.time()), "span_data": {"type": "function", "name": "read_book"}}})
            traces.append(tid)
        ingest_s = time.time() - t0
        t, d = wait_agents(lambda d: sum(a["source"] == "openai-sdk" for a in d["agents"]) >= target)
        time.sleep(4)
        h = json.loads(get("/api/health")[0])
        report[f"B_sdk_{target}"] = {"total_agents": len(d["agents"]) if d else "?", "ingest_s": round(ingest_s, 2),
                                     "visible_after_s": round(t, 1) if t else "TIMEOUT", "collect_ms": h["collect_ms"],
                                     **latency(20), **res(f"B{target}")}

    # ---- C
    phase[0] = "C"
    sids = [json.load(open(f"{CL}/sessions/{f}"))["sessionId"] for f in os.listdir(f"{CL}/sessions")]
    evs = ["PreToolUse", "PostToolUse", "Notification", "UserPromptSubmit", "Stop"]
    errs, lat = [0], []
    lock = threading.Lock()

    def fire(i):
        ev = evs[i % len(evs)]
        p = {"session_id": random.choice(sids), "hook_event_name": ev, "tool_name": "Bash", "cwd": "/work/x",
             "notification_type": "permission_prompt" if ev == "Notification" else None}
        t = time.perf_counter()
        try:
            req = urllib.request.Request(BASE + "/api/event?source=claude", json.dumps(p).encode(),
                                         {"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5).read()
            ms = (time.perf_counter() - t) * 1000
            with lock:
                lat.append(ms)
        except Exception:
            with lock:
                errs[0] += 1

    N_EV = 5000
    t0 = time.time()
    with ThreadPoolExecutor(32) as ex:
        list(ex.map(fire, range(N_EV)))
    dur = time.time() - t0
    lat.sort()
    time.sleep(3)
    ok_after = get("/api/health")[1]
    report["C_hook_flood"] = {"events": N_EV, "threads": 32, "seconds": round(dur, 2),
                              "events_per_s": round(N_EV / dur), "errors": errs[0],
                              "p50_ms": round(lat[len(lat) // 2], 1) if lat else None,
                              "p99_ms": round(lat[int(len(lat) * .99)], 1) if lat else None,
                              "health_after_ms": round(ok_after, 1), **res("C")}

    # ---- D
    phase[0] = "D"
    got = [0]
    ready = threading.Barrier(51)

    def sse_client():
        try:
            r = urllib.request.urlopen(BASE + "/api/stream", timeout=30)
            ready.wait(timeout=20)
            n = 0
            for line in r:
                if line.startswith(b"data:"):
                    n += 1
                    if n >= 2:  # initial snapshot + one pushed change
                        with lock:
                            got[0] += 1
                        break
            r.close()
        except Exception:
            try:
                ready.abort()
            except Exception:
                pass

    ths = [threading.Thread(target=sse_client, daemon=True) for _ in range(50)]
    for th in ths:
        th.start()
    try:
        ready.wait(timeout=20)
    except threading.BrokenBarrierError:
        pass
    t0 = time.time()
    post("/api/ingest", {"event": "trace_start", "item": {"object": "trace", "id": "trace_sse_probe", "workflow_name": "sse probe"}})
    for th in ths:
        th.join(timeout=15)
    report["D_sse_fanout"] = {"clients": 50, "received_push": got[0], "within_s": round(time.time() - t0, 1), **res("D")}

    # ---- E cool-down: finish every SDK run, daemon should settle back
    phase[0] = "E"
    for tid in traces:
        post("/api/ingest", {"event": "trace_end", "item": {"object": "trace", "id": tid, "ended_at": iso(time.time())}})
    time.sleep(12)
    report["E_idle_after"] = res("E")
finally:
    stop_sampling.set()
    daemon.send_signal(signal.SIGTERM)
    try:
        daemon.wait(10)
    except Exception:
        daemon.kill()
    for sp in sleepers:
        sp.kill()
    errlines = [l for l in open(f"{TMP}/daemon.log") if "Traceback" in l or "Error" in l]
    report["daemon_log_errors"] = len(errlines)
    print(json.dumps(report, indent=1, ensure_ascii=False))
    if errlines:
        print("".join(errlines[:10]))
    shutil.rmtree(TMP, ignore_errors=True)
