#!/usr/bin/env python3
"""Performance and effectiveness test harness for the IT Helpdesk RAG agent.

Scenarios
  A  Real Claude, sequential (1 user), labelled workload           latency, tokens, cost, effectiveness
  B  Real Claude, 4 concurrent users                              throughput under contention
  C  Retrieval only (Chroma + local embeddings), 1/4/8 threads    retrieval latency and lock contention
  D  Stubbed Claude with the measured latency, 1..12 concurrent   capacity, queueing and busy errors
  E  Webhook receiver + incident system, 1/8/32 concurrent        ticket-path throughput and idempotency

Usage
  python perf/run_perf.py                  # everything (scenarios A and B make real Claude calls)
  python perf/run_perf.py --skip-real      # no Claude calls at all; A and B are skipped
  python perf/run_perf.py --only C,D,E     # selected scenarios

Output: perf/results/run-<timestamp>.json. Render the report with: python perf/report.py
Real calls cost about USD 0.02 each (measured 2026-10-31); scenario A makes about 17 calls and B about 8.
"""

import argparse
import contextlib
import datetime as dt
import json
import math
import os
import sqlite3
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from config import settings  # noqa: E402  (also loads .env)
from services import llm_service, rag_service, vector_service  # noqa: E402
from webhook_receiver import security  # noqa: E402

REPORTER = {"name": "Perf Tester", "email": "perf.tester@company.test"}
EXPECTED = {"kb_answerable": "answered", "out_of_scope": "no_info", "it_issue": "ticket_proposed"}
WEBHOOK_SECRET = "perf-webhook-secret-0123456789"
ITSM_KEY = "perf-itsm-key-0123456789"

LOCAL = threading.local()
REAL_SUBPROCESS_RUN = subprocess.run
_ORIGINAL_RETRIEVE = vector_service.retrieve


# ---- instrumentation -----------------------------------------------------------------------------

def _bump(name, value):
    setattr(LOCAL, name, getattr(LOCAL, name, 0.0) + value)


def _timed_retrieve(question, *args, **kwargs):
    started = time.perf_counter()
    try:
        return _ORIGINAL_RETRIEVE(question, *args, **kwargs)
    finally:
        _bump("retrieval_ms", (time.perf_counter() - started) * 1000)


vector_service.retrieve = _timed_retrieve


class _SubprocessShim:
    """Stands in for the subprocess module inside llm_service only; everything else is untouched."""

    TimeoutExpired = subprocess.TimeoutExpired

    def __init__(self, run):
        self.run = run


def _record_call(kind, wall_ms, data=None, stub=False):
    usage = (data or {}).get("usage") or {}
    LOCAL.calls.append({
        "kind": kind,
        "wall_ms": wall_ms,
        "api_ms": (data or {}).get("duration_api_ms"),
        "input_uncached": usage.get("input_tokens", 0),
        "cache_creation": usage.get("cache_creation_input_tokens", 0),
        "cache_read": usage.get("cache_read_input_tokens", 0),
        "output": usage.get("output_tokens", 0),
        "cost_usd": (data or {}).get("total_cost_usd") or 0.0,
        "stub": stub,
    })
    _bump("llm_ms", wall_ms)


def _real_run(command, **kwargs):
    """Runs the production command with --output-format json added, so token usage can be read."""
    started = time.perf_counter()
    result = REAL_SUBPROCESS_RUN(command + ["--output-format", "json"], **kwargs)
    wall_ms = (time.perf_counter() - started) * 1000
    try:
        data = json.loads(result.stdout)
    except ValueError:
        data = {}
    kind = "triage" if "is_support_issue" in kwargs.get("input", "") else "answer"
    _record_call(kind, wall_ms, data)
    if result.returncode != 0 or not data.get("result"):
        return subprocess.CompletedProcess(command, result.returncode or 1, "", result.stderr)
    return subprocess.CompletedProcess(command, 0, data["result"], result.stderr)


def _stub_run_factory(latency_s):
    def _stub(command, **kwargs):
        started = time.perf_counter()
        time.sleep(latency_s)
        _record_call("answer", (time.perf_counter() - started) * 1000, stub=True)
        return subprocess.CompletedProcess(command, 0, "Stub answer based on the excerpts.", "")
    return _stub


@contextlib.contextmanager
def llm_mode(run):
    original = llm_service.subprocess
    llm_service.subprocess = _SubprocessShim(run)
    try:
        yield
    finally:
        llm_service.subprocess = original


@contextlib.contextmanager
def setting(name, value):
    original = getattr(settings, name)
    setattr(settings, name, value)
    try:
        yield
    finally:
        setattr(settings, name, original)


# ---- request execution and classification --------------------------------------------------------

def classify(answer):
    if answer.answered:
        return "answered"
    if answer.ticket_status == "pending":
        return "ticket_proposed"
    if answer.ticket_status in ("declined", "created", "duplicate", "failed"):
        return answer.ticket_status
    return "no_info"


def is_correct(item, outcome, sources):
    if item["category"] == "kb_answerable":
        return outcome == "answered" and item["expected_source"] in sources
    return outcome == EXPECTED[item["category"]]


def run_question(item, reporter=REPORTER):
    LOCAL.calls, LOCAL.retrieval_ms, LOCAL.llm_ms = [], 0.0, 0.0
    outcome, sources, error, triage_failed = "error", [], None, False
    started = time.perf_counter()
    try:
        answer = rag_service.answer_question(item["question"], reporter)
        outcome = classify(answer)
        sources = list(answer.sources)
        triage_failed = any(s.status == "failed" and s.text.startswith("Assessed whether") for s in answer.steps)
    except Exception as exc:  # the harness records every failure; it never stops a run
        error = type(exc).__name__
    wall_ms = (time.perf_counter() - started) * 1000
    calls = LOCAL.calls
    usage = {k: sum(c[k] for c in calls) for k in ("input_uncached", "cache_creation", "cache_read", "output")}
    return {
        "id": item["id"],
        "category": item["category"],
        "outcome": outcome,
        "correct": error is None and is_correct(item, outcome, sources),
        "error": error,
        "sources": sources,
        "wall_ms": round(wall_ms, 1),
        "retrieval_ms": round(LOCAL.retrieval_ms, 1),
        "llm_ms": round(LOCAL.llm_ms, 1),
        "other_ms": round(max(0.0, wall_ms - LOCAL.retrieval_ms - LOCAL.llm_ms), 1),
        "llm_calls": len(calls),
        "triage_called": any(c["kind"] == "triage" for c in calls),
        "triage_ok": any(c["kind"] == "triage" for c in calls) and not triage_failed,
        "answer_call_ms": round(sum(c["wall_ms"] for c in calls if c["kind"] == "answer"), 1) or None,
        "triage_failed": triage_failed,
        "input_total": usage["input_uncached"] + usage["cache_creation"] + usage["cache_read"],
        **usage,
        "cost_usd": round(sum(c["cost_usd"] for c in calls), 5),
        "stub_calls": sum(1 for c in calls if c["stub"]),
    }


# ---- statistics ----------------------------------------------------------------------------------

def pct(values, p):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(1, math.ceil(p / 100 * len(ordered))) - 1], 1)


def mean(values, digits=1):
    return round(statistics.fmean(values), digits) if values else None


def rate(part, whole):
    return round(100 * part / whole, 1) if whole else None


def latency_block(latencies_ms, errors, total_s):
    count = len(latencies_ms) + errors
    return {
        "requests": count,
        "errors": errors,
        "error_rate_pct": rate(errors, count),
        "throughput_rps": round(count / total_s, 2) if total_s else None,
        "latency_ms": {
            "mean": mean(latencies_ms),
            "p50": pct(latencies_ms, 50),
            "p95": pct(latencies_ms, 95),
            "p99": pct(latencies_ms, 99),
            "max": round(max(latencies_ms), 1) if latencies_ms else None,
        },
        "total_s": round(total_s, 2),
    }


def effectiveness(rows):
    kb = [r for r in rows if r["category"] == "kb_answerable"]
    oos = [r for r in rows if r["category"] == "out_of_scope"]
    it = [r for r in rows if r["category"] == "it_issue"]
    unresolved = [r for r in rows if not r["error"] and r["outcome"] != "answered"]
    triaged = [r for r in rows if r["triage_called"]]
    return {
        "overall_correct_pct": rate(sum(r["correct"] for r in rows), len(rows)),
        "answerable_correct_pct": rate(sum(r["correct"] for r in kb), len(kb)),
        "out_of_scope_refused_pct": rate(sum(r["correct"] for r in oos), len(oos)),
        "it_issue_ticket_proposed_pct": rate(sum(r["correct"] for r in it), len(it)),
        "false_ticket_pct": rate(sum(r["outcome"] == "ticket_proposed" for r in oos), len(oos)),
        "unresolved_engagement_pct": rate(sum(r["triage_called"] for r in unresolved), len(unresolved)),
        "triage_success_pct": rate(sum(r["triage_ok"] for r in triaged), len(triaged)),
        "breakdown": {
            "kb_answerable": len(kb), "out_of_scope": len(oos), "it_issue": len(it),
        },
    }


def summarize_rows(rows, total_s):
    ok = [r for r in rows if not r["error"]]
    errors = len(rows) - len(ok)
    block = latency_block([r["wall_ms"] for r in ok], errors, total_s)

    def avg(key):
        return mean([r[key] for r in ok])

    block.update({
        "time_breakdown_ms": {"retrieval": avg("retrieval_ms"), "llm": avg("llm_ms"), "other": avg("other_ms")},
        "llm_calls_per_request": avg("llm_calls"),
        "tokens_per_request": {
            "input_total": avg("input_total"),
            "input_uncached": avg("input_uncached"),
            "cache_creation": avg("cache_creation"),
            "cache_read": avg("cache_read"),
            "output": avg("output"),
        },
        "cost_usd_per_request": mean([r["cost_usd"] for r in ok], 4),
        "cost_usd_total": round(sum(r["cost_usd"] for r in ok), 4),
        "effectiveness": effectiveness(rows),
    })
    return block


# ---- scenarios -----------------------------------------------------------------------------------

def run_rows(items, workers, reporter=REPORTER):
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(lambda item: run_question(item, reporter), items))
    return rows, time.perf_counter() - started


def scenario_a(workload):
    rows, total = run_rows(workload, workers=1)
    return {"id": "A", "name": "Real Claude, sequential user", "workers": 1, "rows": rows,
            "levels": [{"label": "1 user", "summary": summarize_rows(rows, total)}]}


def scenario_b(workload):
    subset = [i for i in workload if i["id"] in {"KB-01", "KB-02", "KB-03", "KB-04", "KB-05", "IT-01", "IT-02", "OOS-01"}]
    rows, total = run_rows(subset, workers=4)
    return {"id": "B", "name": "Real Claude, 4 concurrent users", "workers": 4, "rows": rows,
            "levels": [{"label": "4 users", "summary": summarize_rows(rows, total)}]}


def scenario_c(workload):
    queries = [item["question"] for item in workload] * 25
    levels = []
    for threads in (1, 4, 8):
        latencies, errors = [], 0

        def one(question):
            started = time.perf_counter()
            try:
                vector_service.retrieve(question)
                return (time.perf_counter() - started) * 1000
            except Exception:
                return None

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=threads) as pool:
            for value in pool.map(one, queries):
                if value is None:
                    errors += 1
                else:
                    latencies.append(value)
        total = time.perf_counter() - started
        levels.append({"label": f"{threads} threads", "summary": latency_block(latencies, errors, total)})

    # warm index check: the fingerprint matches, so no re-embedding should happen
    warm = []
    for _ in range(5):
        started = time.perf_counter()
        rag_service.refresh_index()
        warm.append((time.perf_counter() - started) * 1000)
    return {"id": "C", "name": "Retrieval only (Chroma + local embeddings)", "levels": levels,
            "warm_index_refresh_ms": {"mean": mean(warm), "max": round(max(warm), 1)}, "queries_per_level": len(queries)}


def scenario_d(workload, stub_latency_s):
    kb = [i for i in workload if i["category"] == "kb_answerable"]
    items = [kb[n % len(kb)] for n in range(24)]
    levels = []
    with llm_mode(_stub_run_factory(stub_latency_s)):
        for workers in (1, 3, 6, 12):
            rows, total = run_rows(items, workers=workers, reporter=None)
            levels.append({"label": f"{workers} concurrent", "workers": workers, "summary": summarize_rows(rows, total)})
        with setting("CLAUDE_TIMEOUT", 5):  # short queue wait to show what overload does
            rows, total = run_rows(items, workers=12, reporter=None)
            levels.append({"label": "12 concurrent, queue wait 5 s", "workers": 12, "overload": True,
                           "summary": summarize_rows(rows, total)})
    return {"id": "D", "name": "Stubbed Claude latency, capacity sweep", "stub_latency_s": stub_latency_s,
            "slots": settings.MAX_CONCURRENT_REQUESTS, "levels": levels}


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_server(module, port, env, log_path):
    log = open(log_path, "w")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", f"{module}:create_app", "--factory", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    for _ in range(100):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=1).status_code == 200:
                return proc
        except httpx.TransportError:
            time.sleep(0.1)
    proc.kill()
    raise RuntimeError(f"{module} did not start; see {log_path}")


_thread_client = threading.local()


def post_event(url, event_id):
    base = json.loads((ROOT / "data" / "scenario" / "webhook_payloads.json").read_text(encoding="utf-8"))["valid"]
    payload = {**base, "event_id": event_id, "reported_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    body = json.dumps(payload).encode()
    timestamp = str(int(time.time()))
    headers = {"Content-Type": "application/json", security.TIMESTAMP_HEADER: timestamp,
               security.SIGNATURE_HEADER: security.sign(WEBHOOK_SECRET, timestamp, body)}
    if not hasattr(_thread_client, "client"):
        _thread_client.client = httpx.Client(timeout=30)
    started = time.perf_counter()
    try:
        status = _thread_client.client.post(url, content=body, headers=headers).status_code
    except httpx.TransportError:
        status = None
    return status, (time.perf_counter() - started) * 1000


def scenario_e(levels_to_run=(1, 8, 32), per_level=200):
    tmp = Path(tempfile.mkdtemp(prefix="perf-itsm-"))
    itsm_port, hook_port = free_port(), free_port()
    env = {
        **os.environ,
        "MOCK_ITSM_API_KEY": ITSM_KEY, "MOCK_ITSM_DB": str(tmp / "itsm.db"),
        "WEBHOOK_SECRET": WEBHOOK_SECRET, "TICKET_API_KEY": ITSM_KEY,
        "TICKET_API_URL": f"http://127.0.0.1:{itsm_port}", "TICKET_MAX_ATTEMPTS": "2", "TICKET_TIMEOUT_SECONDS": "5",
    }
    procs = []
    try:
        procs.append(start_server("mock_itsm.app", itsm_port, env, tmp / "itsm.log"))
        procs.append(start_server("webhook_receiver.app", hook_port, env, tmp / "receiver.log"))
        url = f"http://127.0.0.1:{hook_port}/webhooks/issues"
        levels, expected_unique = [], 0
        for level in levels_to_run:
            ids = []
            for i in range(per_level):
                ids.append(ids[i - 1] if i % 5 == 4 else f"perf-L{level}-{i:04d}")  # every fifth event is a re-delivery
            expected_unique += len(set(ids))
            started = time.perf_counter()
            with ThreadPoolExecutor(max_workers=level) as pool:
                results = list(pool.map(lambda event_id: post_event(url, event_id), ids))
            total = time.perf_counter() - started
            successes = [ms for status, ms in results if status in (200, 201)]
            errors = len(results) - len(successes)
            block = latency_block(successes, errors, total)
            block["status_codes"] = dict(Counter(str(status) for status, _ in results))
            block["created"] = sum(1 for status, _ in results if status == 201)
            block["duplicates"] = sum(1 for status, _ in results if status == 200)
            levels.append({"label": f"{level} concurrent", "workers": level, "summary": block})

        db = sqlite3.connect(f"file:{tmp / 'itsm.db'}?mode=ro", uri=True)
        stored = db.execute("select count(*) from incidents").fetchone()[0]
        duplicated = db.execute("select count(*) from (select external_id from incidents group by external_id having count(*) > 1)").fetchone()[0]
        db.close()
        integrity = {"expected_tickets": expected_unique, "stored_tickets": stored, "duplicate_tickets": duplicated,
                     "passed": stored == expected_unique and duplicated == 0}
    finally:
        for proc in procs:
            proc.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=10)
    return {"id": "E", "name": "Webhook to incident system (signed, idempotent)", "per_level": per_level,
            "levels": levels, "integrity": integrity}


def environment():
    def run(cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=ROOT).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return "unavailable"

    collection = vector_service._get_client().get_collection(settings.COLLECTION_NAME)
    return {
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "git_commit": run(["git", "rev-parse", "--short", "HEAD"]),
        "cpus": os.cpu_count(),
        "python": sys.version.split()[0],
        "claude_cli": run(["claude", "--version"]),
        "chunks_indexed": collection.count(),
        "max_concurrent_requests": settings.MAX_CONCURRENT_REQUESTS,
        "claude_timeout_s": settings.CLAUDE_TIMEOUT,
        "top_k": settings.TOP_K,
        "min_score": settings.MIN_SCORE,
        "require_ticket_confirmation": settings.REQUIRE_TICKET_CONFIRMATION,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-real", action="store_true", help="make no Claude calls")
    parser.add_argument("--only", default="", help="comma-separated scenario ids, e.g. C,D,E")
    args = parser.parse_args()
    selected = set(filter(None, args.only.split(","))) or {"A", "B", "C", "D", "E"}
    if args.skip_real:
        selected -= {"A", "B"}
    if not settings.REQUIRE_TICKET_CONFIRMATION:
        sys.exit("Refusing to run: REQUIRE_TICKET_CONFIRMATION must stay true so the harness can never create a ticket.")

    workload = json.loads((HERE / "workload.json").read_text(encoding="utf-8"))["items"]
    rag_service.refresh_index()
    results = {"environment": environment(), "scenarios": []}
    print("Environment:", json.dumps(results["environment"], indent=1))

    def run_real(fn):
        with llm_mode(_real_run):
            return fn()

    if "A" in selected:
        print("Scenario A: real Claude, sequential ...", flush=True)
        results["scenarios"].append(run_real(lambda: scenario_a(workload)))
    if "B" in selected:
        print("Scenario B: real Claude, 4 concurrent ...", flush=True)
        results["scenarios"].append(run_real(lambda: scenario_b(workload)))
    if "C" in selected:
        print("Scenario C: retrieval only ...", flush=True)
        results["scenarios"].append(scenario_c(workload))
    if "D" in selected:
        stub_latency = 3.0
        a = next((s for s in results["scenarios"] if s["id"] == "A"), None)
        if a:  # use the measured median answer-call time, so the stub reflects reality
            answer_ms = [r["answer_call_ms"] for r in a["rows"] if r["answer_call_ms"] and not r["error"]]
            if answer_ms:
                stub_latency = round(statistics.median(answer_ms) / 1000, 2)
        print(f"Scenario D: stubbed LLM at {stub_latency}s, capacity sweep ...", flush=True)
        results["scenarios"].append(scenario_d(workload, stub_latency))
    if "E" in selected:
        print("Scenario E: webhook path ...", flush=True)
        results["scenarios"].append(scenario_e())

    RESULTS_DIR = HERE / "results"
    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"run-{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}.json"
    out.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"Wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
