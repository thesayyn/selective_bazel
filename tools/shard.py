#!/usr/bin/env python3
"""Turn `aspect cache diff --output=json` into a GitHub Actions matrix.

    aspect cache diff --output=json > affected.json
    python3 tools/shard.py affected.json 13 >> "$GITHUB_OUTPUT"

Writes three outputs: `count` (number of affected tests), `total`, and `matrix`
(a JSON object with one `include` entry per shard, each carrying a
space-separated `targets` string and a human-readable `shard` name). When
GITHUB_STEP_SUMMARY is set, also appends a Markdown summary listing each
affected test, the cache miss that caused it, and the shard plan.

Sharding keeps related tests together. Tests in one package share their
dependencies (here, the service's library), so a runner that gets a whole
package builds or fetches those once instead of every runner doing it:

  1. group the affected tests by package;
  2. cut each package into chunks of at most TESTS_PER_SHARD tests, one per
     core, so a chunk finishes in a single round on the runner;
  3. a full chunk is a shard of its own; the small leftovers of different
     packages are packed together, largest first, so no runner is nearly idle;
  4. if that is still more shards than `max_shards`, the chunks are spread
     over exactly `max_shards` runners, largest first onto the least-loaded
     runner, so shards stay balanced (and take more than one round).

Standard library only; GitHub's ubuntu runners have python3.
"""

import json
import os
import sys
from collections import defaultdict

TESTS_PER_SHARD = 4  # ubuntu-latest has 4 cores; each heavy test burns one


def package(label: str) -> str:
    return label.split(":", 1)[0]


def short(label: str) -> str:
    """`//services/gamma:test_07` -> `gamma:07`, for job names."""
    pkg, name = label.rsplit(":", 1)
    return f"{pkg.rsplit('/', 1)[-1]}:{name.rsplit('_', 1)[-1]}"


def describe(chunk: list) -> str:
    """`gamma:00-03` for a run of one package's tests, `gamma:09` for a single."""
    pkg = short(chunk[0]).split(":")[0]
    first, last = short(chunk[0]).split(":")[1], short(chunk[-1]).split(":")[1]
    return f"{pkg}:{first}" if first == last else f"{pkg}:{first}-{last}"


def plan(labels: list, max_shards: int) -> list:
    """Return shards as lists of chunks; each chunk is one package's tests."""
    by_pkg = defaultdict(list)
    for label in sorted(labels):
        by_pkg[package(label)].append(label)

    full, partial = [], []
    for pkg in sorted(by_pkg):
        tests = by_pkg[pkg]
        for i in range(0, len(tests), TESTS_PER_SHARD):
            chunk = tests[i : i + TESTS_PER_SHARD]
            (full if len(chunk) == TESTS_PER_SHARD else partial).append(chunk)

    shards = [[c] for c in full]
    for chunk in sorted(partial, key=len, reverse=True):  # first-fit decreasing
        for shard in shards:
            if shard in [[c] for c in full]:
                continue  # full chunks have no room
            if sum(map(len, shard)) + len(chunk) <= TESTS_PER_SHARD:
                shard.append(chunk)
                break
        else:
            shards.append([chunk])

    if len(shards) > max_shards > 0:
        # Too many: spread the chunks over exactly max_shards runners, largest
        # chunk first onto the runner with the least work so far (LPT). Each
        # chunk still stays whole, so related tests still run together.
        bins = [[] for _ in range(max_shards)]
        for chunk in sorted(full + partial, key=len, reverse=True):
            min(bins, key=lambda b: sum(map(len, b))).append(chunk)
        shards = [b for b in bins if b]
    return shards


def summary(doc: dict, labels: list, shards: list) -> str:
    lines = [
        "### `aspect cache diff`",
        "",
        f"Affected **{len(labels)}** of {doc['total_tests']} tests → {len(shards)} shard(s).",
        "",
    ]
    if labels:
        lines += ["| affected test | cache miss in |", "|---|---|"]
        for t in doc["affected"]:
            causes = ", ".join(f"`{c['target']}` ({c['mnemonic']})" for c in t["caused_by"])
            lines.append(f"| `{t['label']}` | {causes} |")
        lines += ["", "| shard | tests |", "|---|---|"]
        for shard in shards:
            lines.append(f"| {' + '.join(describe(c) for c in shard)} | {sum(map(len, shard))} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    path, max_shards = sys.argv[1], int(sys.argv[2])
    with open(path) as f:
        doc = json.load(f)
    labels = sorted(t["label"] for t in doc["affected"])
    shards = plan(labels, max_shards)
    matrix = {
        "include": [
            {
                "shard": " + ".join(describe(c) for c in shard),
                "targets": " ".join(label for chunk in shard for label in chunk),
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
            f.write(summary(doc, labels, shards))


if __name__ == "__main__":
    main()
