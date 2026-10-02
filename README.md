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

Each test burns one CPU core for 120 seconds and reads its library files, so a change to any of them changes the test action's inputs (its cache key). 50 tests × 120 s = 100 CPU-minutes per full run, which is about 25 minutes of wall clock on one 4-core runner.

## The two pipelines

Both run on every push and pull request, so you can compare them side by side in the [Actions tab](../../actions).

**[`single-runner.yml`](.github/workflows/single-runner.yml)** — the baseline everyone starts with:

```yaml
- uses: aspect-build/setup-aspect@…     # installs bazelisk + the Aspect CLI
- run: bazel test //...
```

**[`sharded.yml`](.github/workflows/sharded.yml)** — ask the cache first, then fan out:

```yaml
select:
  - run: aspect cache diff --output=json > affected.json     # which tests are NOT in the cache?
  - run: python3 tools/shard.py affected.json 13 >> "$GITHUB_OUTPUT"   # ≤ 4 tests per shard, one per core
test:
  strategy: { matrix: "${{ fromJSON(needs.select.outputs.matrix) }}" }
  - run: bazel test ${{ matrix.targets }}
```

`aspect cache diff` runs one `bazel test` with `--experimental_remote_require_cached` and `--remote_grpc_log`: every action asks the remote cache whether it is already built, a miss is denied instead of run, and the gRPC log says which tests had a miss somewhere in their closure. Nothing executes. The result is the affected set as data, which is what you need before you can shard.

[`tools/shard.py`](tools/shard.py) then plans the shards, and it keeps related tests together rather than dealing them out round-robin. Tests in one package share their dependencies (here, the service's library), so a runner that gets a whole package builds or fetches those once instead of every runner doing it. The rules:

1. group the affected tests by package;
2. cut each package into chunks of at most 4 tests, one per core on `ubuntu-latest`, so a chunk finishes in a single two-minute round;
3. a full chunk is a shard of its own; the small leftovers of different packages are packed together so no runner is nearly idle;
4. never ask for more than 13 shards (a personal GitHub account runs 20 jobs at once); past that, chunks are spread evenly over 13 runners.

So 50 affected tests become 13 shards named like `gamma:00-03` and `alpha:08-09 + beta:08-09`; 10 affected tests in one service become 3 shards; 1 becomes 1; 0 skips the test job entirely. The shard names are the job names in the Actions UI, so a red job tells you which service broke without opening it.

## The remote cache

`aspect cache diff` is strictly a remote-cache operation, so the one thing this repo needs is a cache that CI can read and write. It uses the [**Aspect Cloud remote cache**](https://aspect.build): a hosted REv2 cache you can point any Bazel build at with two flags and no infrastructure of your own, and it is on the [free tier](https://aspect.build/pricing).

The whole configuration is in [`.bazelrc`](.bazelrc):

```
build --remote_cache=grpcs://cache.aspect.build
build --remote_cache_header=X-Aspect=<your token>      # lives in user.bazelrc, gitignored
```

CI writes the header line into `user.bazelrc` from the `ASPECT_API_TOKEN` secret; `.bazelrc` `try-import`s that file. Nothing else is needed: no credential helper, no cache servers to run, and the same cache is shared by every job, branch and laptop that has the token.

### How the cache gets its baseline

A cache hit means "unaffected", so the cache must hold the mainline's results. That happens as a by-product of the pipelines themselves: every `bazel test` on `main` uploads its results. There is no seed step.

The one rule: the baseline and the probe must resolve the same Bazel flags, so they compute the same action keys. Both pipelines read the same `.bazelrc`, so they match.

## Results

Measured on GitHub-hosted `ubuntu-latest` runners (4 cores). Durations are the whole workflow run as reported by GitHub Actions, including checkout and tool install.

| change | tests affected | single runner | sharded (`cache diff` + N runners) |
|---|---|---|---|
| _first run, empty cache_ | 50 | _pending_ | _pending_ |
| no change | 0 | _pending_ | _pending_ |
| one service's `lib.txt` | 10 | _pending_ | _pending_ |
| `core/core.txt` | 50 | _pending_ | _pending_ |

(Filled in from real runs; each cell links to its workflow run.)

## Reproduce

Fork, add an `ASPECT_API_TOKEN` repository secret (an Aspect Cloud token), push. Then open a pull request that edits `core/core.txt` and another that edits `services/gamma/lib.txt`, and compare the two workflows on each.

Locally, with the same token:

```sh
echo 'build --remote_cache_header=X-Aspect=<your token>' > user.bazelrc   # gitignored
bazel test //...                 # seed the baseline (or let CI do it)
echo 'core v2' > core/core.txt
aspect cache diff                # → 50 labels on stdout, reasons on stderr, nothing executed
aspect cache diff --output=json | python3 tools/shard.py /dev/stdin 13
```

Any other REv2 cache works too, e.g. [bazel-remote](https://github.com/buchgr/bazel-remote): put `build --remote_cache=grpc://127.0.0.1:9092` in `user.bazelrc` instead (it overrides the Aspect Cloud line).
