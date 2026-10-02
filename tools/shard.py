#!/usr/bin/env python3
"""Turn `aspect cache diff --output=json` into a GitHub Actions matrix.

    aspect cache diff --output=json > affected.json
    python3 tools/shard.py affected.json 10 >> "$GITHUB_OUTPUT"

Writes two outputs: `count` (number of affected tests) and `matrix` (a JSON
object with one `include` entry per shard, each carrying a space-separated
`targets` string). Shards are filled round-robin, so they stay the same size.
Standard library only; GitHub's ubuntu runners have python3.
"""

import json
import sys


def main() -> None:
    path, max_shards = sys.argv[1], int(sys.argv[2])
    with open(path) as f:
        doc = json.load(f)
    labels = sorted(t["label"] for t in doc["affected"])
    n = min(max_shards, len(labels))
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


if __name__ == "__main__":
    main()
