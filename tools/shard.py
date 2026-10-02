#!/usr/bin/env python3
"""Turn `aspect cache diff --output=json` into a GitHub Actions matrix.

    aspect cache diff --output=json > affected.json
    python3 tools/shard.py affected.json 13 >> "$GITHUB_OUTPUT"

Writes three outputs: `count` (number of affected tests), `total`, and `matrix`
(a JSON object with one `include` entry per shard, each carrying a
space-separated `targets` string and a human-readable `shard` name). When
GITHUB_STEP_SUMMARY is set, also appends a Markdown summary listing each
affected test, the cache miss that caused it, and the shard plan.

Shards are planned by cost, not by count, and they keep related tests together:

  1. ask Bazel for each affected test's `size` and turn it into an expected
     duration (COST). A real repo would use historical durations here.
  2. estimate a shard's wall time on a CORES-core runner as
     max(longest test, total / CORES).
  3. group the affected tests by package (tests in one package share their
     dependencies, so one runner fetches them once) and cut each package into
     chunks whose estimated wall time stays within SHARD_TARGET_SECONDS.
  4. pack the chunks into shards, largest first, as long as a shard stays
     within the target; a chunk is never split across shards.
  5. if that is still more shards than `max_shards`, spread the chunks over
     exactly `max_shards` runners, largest first onto the least-loaded one.

Standard library only; GitHub's ubuntu runners have python3.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict

CORES = 4  # ubuntu-latest; each heavy test burns one core
COST = {"small": 15, "medium": 60, "large": 240, "enormous": 900}  # seconds, see heavy_test.bzl
TARGET = int(os.environ.get("SHARD_TARGET_SECONDS", "240"))
# Checkout, tool install and Bazel analysis before the first test runs; added to
# the wall-time estimates shown in shard names and the summary, not to the plan.
OVERHEAD = int(os.environ.get("SHARD_OVERHEAD_SECONDS", "60"))


def costs(labels: list) -> dict:
    """Expected duration per label from its Bazel `size`; medium when unknown."""
    cost = {label: COST["medium"] for label in labels}
    if not labels or shutil.which("bazel") is None:
        return cost
    universe = "set(" + " ".join(labels) + ")"
    for size, seconds in COST.items():
        out = subprocess.run(
            ["bazel", "query", "--noshow_progress", "--output=label", f'attr(size, "^{size}$", {universe})'],
            capture_output=True, text=True,
        )
        if out.returncode == 0:
            for label in out.stdout.split():
                cost[label] = seconds
    return cost


def makespan(tests: list, cost: dict) -> float:
    d = [cost[t] for t in tests]
    return max(max(d), sum(d) / CORES) if d else 0.0


def flat(shard: list) -> list:
    return [t for chunk in shard for t in chunk]


def package(label: str) -> str:
    return label.split(":", 1)[0]


def describe(chunk: list) -> str:
    """`gamma:00-03,08` — package short name, then the tests' numeric suffixes as ranges."""
    pkg = package(chunk[0]).rsplit("/", 1)[-1]
    names = sorted(t.rsplit(":", 1)[1] for t in chunk)
    nums = [re.search(r"(\d+)$", n) for n in names]
    if not all(nums):
        return f"{pkg}:{','.join(names)}"
    nums = [m.group(1) for m in nums]
    ranges, start, prev = [], nums[0], nums[0]
    for n in nums[1:]:
        if int(n) == int(prev) + 1:
            prev = n
            continue
        ranges.append(start if start == prev else f"{start}-{prev}")
        start = prev = n
    ranges.append(start if start == prev else f"{start}-{prev}")
    return f"{pkg}:{','.join(ranges)}"


def fmt(seconds: float) -> str:
    return f"~{int(seconds) // 60}m{int(seconds) % 60:02d}s"


def plan(labels: list, cost: dict, target: int, max_shards: int) -> list:
    """Return shards as lists of chunks; each chunk is one package's tests."""
    by_pkg = defaultdict(list)
    for label in labels:
        by_pkg[package(label)].append(label)

    chunks = []
    for pkg in sorted(by_pkg):
        chunk = []
        for t in sorted(by_pkg[pkg], key=lambda t: (-cost[t], t)):  # longest first
            if chunk and makespan(chunk + [t], cost) > target:
                chunks.append(chunk)
                chunk = []
            chunk.append(t)
        if chunk:
            chunks.append(chunk)

    shards = []
    for chunk in sorted(chunks, key=lambda c: -makespan(c, cost)):  # first-fit decreasing
        for shard in shards:
            if makespan(flat(shard + [chunk]), cost) <= target:
                shard.append(chunk)
                break
        else:
            shards.append([chunk])

    if len(shards) > max_shards > 0:
        bins = [[] for _ in range(max_shards)]
        for chunk in sorted(chunks, key=lambda c: -makespan(c, cost)):
            min(bins, key=lambda b: makespan(flat(b), cost)).append(chunk)
        shards = [b for b in bins if b]
    return sorted(shards, key=lambda s: -makespan(flat(s), cost))


def summary(doc: dict, labels: list, shards: list, cost: dict) -> str:
    lines = [
        "### `aspect cache diff`",
        "",
        f"Affected **{len(labels)}** of {doc['total_tests']} tests → {len(shards)} shard(s), "
        f"planned at up to {fmt(TARGET)} of test time each on {CORES} cores.",
        "",
    ]
    if labels:
        lines += ["| affected test | size | cache miss in |", "|---|---|---|"]
        size_of = {v: k for k, v in COST.items()}
        for t in doc["affected"]:
            causes = ", ".join(f"`{c['target']}` ({c['mnemonic']})" for c in t["caused_by"])
            lines.append(f"| `{t['label']}` | {size_of.get(cost[t['label']], '?')} | {causes} |")
        lines += ["", "| shard | tests | core-seconds | est. wall (incl. setup) |", "|---|---|---|---|"]
        for shard in shards:
            tests = flat(shard)
            lines.append(
                f"| {' + '.join(describe(c) for c in shard)} | {len(tests)} | "
                f"{sum(cost[t] for t in tests)} | {fmt(OVERHEAD + makespan(tests, cost))} |"
            )
    return "\n".join(lines) + "\n"


def main() -> None:
    path, max_shards = sys.argv[1], int(sys.argv[2])
    with open(path) as f:
        doc = json.load(f)
    labels = sorted(t["label"] for t in doc["affected"])
    cost = costs(labels)
    shards = plan(labels, cost, TARGET, max_shards)
    matrix = {
        "include": [
            {
                "shard": " + ".join(describe(c) for c in shard) + " " + fmt(OVERHEAD + makespan(flat(shard), cost)),
                "targets": " ".join(flat(shard)),
            }
            for shard in shards
        ]
    }
    print(f"count={len(labels)}")
    print(f"total={doc['total_tests']}")
    print(f"matrix={json.dumps(matrix)}")
    print(
        f"affected {len(labels)} of {doc['total_tests']} tests -> {len(shards)} shard(s): "
        + ", ".join(e["shard"] for e in matrix["include"]),
        file=sys.stderr,
    )
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a") as f:
            f.write(summary(doc, labels, shards, cost))


if __name__ == "__main__":
    main()
