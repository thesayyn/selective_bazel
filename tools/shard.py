#!/usr/bin/env python3
"""Turn `aspect cache diff --output=json` into a GitHub Actions matrix.

    aspect cache diff --output=json > affected.json
    python3 tools/shard.py affected.json 10 >> "$GITHUB_OUTPUT"

Writes three outputs: `count` (number of affected tests), `total`, and `matrix`
(a JSON object with one `include` entry per shard, each carrying a
space-separated `targets` string). Shards are filled round-robin, so they stay
the same size. When GITHUB_STEP_SUMMARY is set, also appends a Markdown summary
listing each affected test and the cache miss that caused it.
Standard library only; GitHub's ubuntu runners have python3.
"""

import json
import os
import sys

TESTS_PER_SHARD = 4  # ubuntu-latest has 4 cores; each heavy test burns one


def summary(doc: dict, labels: list, n: int) -> str:
    lines = [
        "### `aspect cache diff`",
        "",
        f"Affected **{len(labels)}** of {doc['total_tests']} tests → {n} shard(s).",
        "",
    ]
    if labels:
        lines += ["| affected test | cache miss in |", "|---|---|"]
        for t in doc["affected"]:
            causes = ", ".join(f"`{c['target']}` ({c['mnemonic']})" for c in t["caused_by"])
            lines.append(f"| `{t['label']}` | {causes} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    path, max_shards = sys.argv[1], int(sys.argv[2])
    with open(path) as f:
        doc = json.load(f)
    labels = sorted(t["label"] for t in doc["affected"])
    # One test per core: a shard of <= TESTS_PER_SHARD tests finishes in a
    # single round on a 4-core runner. More shards than that buys nothing.
    n = min(max_shards, -(-len(labels) // TESTS_PER_SHARD))
    shards = [labels[i::n] for i in range(n)] if n else []
    matrix = {
        "include": [
            {"shard": f"{i + 1}/{n}", "targets": " ".join(s)} for i, s in enumerate(shards)
        ]
    }
    print(f"count={len(labels)}")
    print(f"total={doc['total_tests']}")
    print(f"matrix={json.dumps(matrix)}")
    print(
        f"affected {len(labels)} of {doc['total_tests']} tests -> {n} shard(s)",
        file=sys.stderr,
    )
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a") as f:
            f.write(summary(doc, labels, n))


if __name__ == "__main__":
    main()
