#!/usr/bin/env python3
"""Where did a CI job's time go? Setup vs Bazel analysis vs test execution.

    bazel test ... --build_event_json_file=bep.json
    python3 tools/timing.py bep.json                 # a test job
    python3 tools/timing.py --cache-diff cache-diff.log   # the select job

Reads Bazel's build event JSON and writes a breakdown to GITHUB_STEP_SUMMARY
(when set) and to stdout, plus a machine-readable timing.json:

    job start → Bazel start      checkout, tool install (needs JOB_START, epoch seconds)
    loading + analysis           timingMetrics.analysisPhaseTimeInMs
    execution phase              timingMetrics.executionPhaseTimeInMs (wall, tests in parallel)
    pure test time               sum of executed tests' durations (what a 1-core serial run costs)
    cached tests                 results served from the remote cache, with their original durations

For the select job, --cache-diff extracts `aspect cache diff`'s own phase line
(invalidate · probe · enumerate · attribute) from its captured stderr.
Standard library only.
"""

import json
import os
import re
import sys
import time

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def fmt(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 60}m{seconds % 60:02d}s"


def read_bep(path: str) -> dict:
    out = {"tests": {}}
    with open(path) as f:
        for line in f:
            if not line.strip():
                continue
            e = json.loads(line)
            if "started" in e:
                out["bazel_start"] = int(e["started"]["startTimeMillis"]) / 1000
            if "finished" in e:
                out["bazel_finish"] = int(e["finished"]["finishTimeMillis"]) / 1000
            if "buildMetrics" in e:
                tm = e["buildMetrics"].get("timingMetrics", {})
                out["analysis_s"] = int(tm.get("analysisPhaseTimeInMs", 0)) / 1000
                out["execution_s"] = int(tm.get("executionPhaseTimeInMs", 0)) / 1000
                out["bazel_wall_s"] = int(tm.get("wallTimeInMs", 0)) / 1000
            if "testResult" in e:
                label = e["id"]["testResult"]["label"]
                r = e["testResult"]
                info = r.get("executionInfo", {})
                out["tests"][label] = {
                    "duration_s": int(r.get("testAttemptDurationMillis", 0)) / 1000,
                    "cached": bool(r.get("cachedLocally") or info.get("cachedRemotely")),
                    "status": r.get("status"),
                }
    return out


def bep_report(path: str) -> tuple:
    b = read_bep(path)
    now = time.time()
    job_start = float(os.environ["JOB_START"]) if os.environ.get("JOB_START") else None
    executed = {l: t for l, t in b["tests"].items() if not t["cached"]}
    cached = {l: t for l, t in b["tests"].items() if t["cached"]}
    pure = sum(t["duration_s"] for t in executed.values())
    rows = []
    if job_start and "bazel_start" in b:
        rows.append(("checkout + install (job start → Bazel start)", fmt(b["bazel_start"] - job_start)))
    rows.append(("Bazel: loading + analysis", fmt(b.get("analysis_s", 0))))
    rows.append(("Bazel: execution phase (tests run 4 at a time)", fmt(b.get("execution_s", 0))))
    rows.append((f"pure test time ({len(executed)} tests executed, summed)", fmt(pure)))
    if cached:
        rows.append((f"tests served from the remote cache", f"{len(cached)} (would have taken {fmt(sum(t['duration_s'] for t in cached.values()))})"))
    rows.append(("Bazel wall time", fmt(b.get("bazel_wall_s", 0))))
    if job_start:
        rows.append(("job so far (job start → now)", fmt(now - job_start)))
    data = {
        "setup_s": (b["bazel_start"] - job_start) if job_start and "bazel_start" in b else None,
        "analysis_s": b.get("analysis_s"),
        "execution_s": b.get("execution_s"),
        "bazel_wall_s": b.get("bazel_wall_s"),
        "pure_test_s": pure,
        "tests_executed": len(executed),
        "tests_cached": len(cached),
        "job_s": (now - job_start) if job_start else None,
    }
    return rows, data


def cache_diff_report(path: str) -> tuple:
    text = ANSI.sub("", open(path).read())
    rows, data = [], {}
    job_start = float(os.environ["JOB_START"]) if os.environ.get("JOB_START") else None
    m = re.search(r"Affected (\d+) of (\d+) test target", text)
    if m:
        data["affected"], data["total"] = int(m.group(1)), int(m.group(2))
    for m in re.finditer(r"^\s*(bazel|analysis) ([0-9.]+)s: (.*)$", text, re.M):
        rows.append((f"cache diff {m.group(1)} phases", f"{fmt(float(m.group(2)))}: {m.group(3).strip()}"))
        data[f"{m.group(1)}_s"] = float(m.group(2))
        for part in m.group(3).split("·"):
            pm = re.match(r"\s*(\w+) ([0-9.]+)s", part)
            if pm:
                data[f"{m.group(1)}_{pm.group(1)}_s"] = float(pm.group(2))
    m = re.search(r"(Passed|Failed).*?in ([0-9.]+)s", text)
    if m:
        rows.append(("cache diff total", fmt(float(m.group(2)))))
        data["cache_diff_s"] = float(m.group(2))
    if job_start:
        rows.append(("job so far (job start → now)", fmt(time.time() - job_start)))
        data["job_s"] = time.time() - job_start
    return rows, data


def main() -> None:
    if sys.argv[1] == "--cache-diff":
        title, (rows, data) = "Where the time went: `select`", cache_diff_report(sys.argv[2])
    else:
        title, (rows, data) = "Where the time went", bep_report(sys.argv[1])
    md = [f"### {title}", "", "| phase | time |", "|---|---|"] + [f"| {k} | {v} |" for k, v in rows] + [""]
    text = "\n".join(md)
    print(text)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a") as f:
            f.write(text + "\n")
    with open("timing.json", "w") as f:
        json.dump(data, f, indent=2)


if __name__ == "__main__":
    main()
