# selective_bazel

Companion repo for the BazelCon 2026 talk **"Stop delivering your whole fleet for a single change."**

It answers one question with real CI runs: for a repo with 50 heavy tests, what does it cost to run them on one runner, versus asking the remote cache which ones are affected and sharding only those across runners?

The only Aspect CLI command used here is [`aspect cache diff`](https://aspect.build/docs/cli). Everything else is stock Bazel and GitHub Actions.

## The repo

```
core/         one shared library       edit core.txt   → all 50 tests affected
services/
  alpha/      lib + 10 heavy tests     edit lib.txt    → 10 tests affected
  beta/       lib + 10 heavy tests
  gamma/      lib + 10 heavy tests
  delta/      lib + 10 heavy tests
  epsilon/    lib + 10 heavy tests
```

Each test burns one CPU core for 30 seconds and reads its library files, so a change to any of them changes the test action's inputs (its cache key). 50 tests × 30 s = 25 CPU-minutes per full run.

## The two pipelines

Both run on every push and pull request, so you can compare them side by side in the [Actions tab](../../actions).

**[`single-runner.yml`](.github/workflows/single-runner.yml)** — the baseline everyone starts with:

```yaml
- uses: aspect-build/setup-aspect@…     # installs bazelisk + aspect, points Bazel at the remote cache
- run: bazel test //...
```

**[`sharded.yml`](.github/workflows/sharded.yml)** — ask the cache first, then fan out:

```yaml
select:
  - run: aspect cache diff --output=json > affected.json     # which tests are NOT in the cache?
  - run: python3 tools/shard.py affected.json 10 >> "$GITHUB_OUTPUT"
test:
  strategy: { matrix: "${{ fromJSON(needs.select.outputs.matrix) }}" }
  - run: bazel test ${{ matrix.targets }}
```

`aspect cache diff` runs one `bazel test` with `--experimental_remote_require_cached` and `--remote_grpc_log`: every action asks the remote cache whether it is already built, a miss is denied instead of run, and the gRPC log says which tests had a miss somewhere in their closure. Nothing executes. The result is the affected set as data, which is what you need before you can shard.

## How the cache gets its baseline

A cache hit means "unaffected", so the cache must hold the mainline's results. That happens as a by-product of the pipelines themselves: every `bazel test` on `main` uploads its results. There is no seed step.

The one rule: the baseline and the probe must resolve the same Bazel flags, so they compute the same action keys. Both pipelines get their cache configuration from `setup-aspect` and their other flags from the same `.bazelrc`, so they match.

## Results

Measured on GitHub-hosted `ubuntu-latest` runners (4 cores). Durations are the whole workflow run as reported by GitHub Actions, including checkout and tool install.

| change | tests affected | single runner | sharded (`cache diff` + N runners) |
|---|---|---|---|
| _first run, empty cache_ | 50 | _pending_ | _pending_ |
| no change | 0 | _pending_ | _pending_ |
| one service's `lib.txt` | 10 | _pending_ | _pending_ |
| `core/core.txt` | 50 | _pending_ | _pending_ |

(Filled in from real runs; see the linked workflow runs in each cell.)

## Reproduce

Fork, add an `ASPECT_API_TOKEN` repository secret (an [Aspect Cloud](https://aspect.build) free-tier token; `setup-aspect` uses it to reach the remote cache), push. Then open a pull request that edits `core/core.txt` and another that edits `services/gamma/lib.txt`, and compare the two workflows on each.

To run `cache diff` locally you need any REv2 cache you can write to, e.g. [bazel-remote](https://github.com/buchgr/bazel-remote):

```sh
bazel-remote --dir /tmp/cache --max_size 5 --grpc_address 127.0.0.1:9092 &
echo 'common --remote_cache=grpc://127.0.0.1:9092' > user.bazelrc   # gitignored, try-imported by .bazelrc
bazel test //...                 # seed the baseline
echo 'core v2' > core/core.txt
aspect cache diff                # → 50 labels on stdout, reasons on stderr
aspect cache diff --output=json | python3 tools/shard.py /dev/stdin 10
```
