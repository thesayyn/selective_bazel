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
- uses: aspect-build/setup-aspect@…     # installs bazelisk + the Aspect CLI
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
| no cache configured at all | 50 | [7m 31s](../../actions/runs/37048867957) | — (`cache diff` needs a cache) |
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
aspect cache diff --output=json | python3 tools/shard.py /dev/stdin 10
```

Any other REv2 cache works too, e.g. [bazel-remote](https://github.com/buchgr/bazel-remote): put `build --remote_cache=grpc://127.0.0.1:9092` in `user.bazelrc` instead (it overrides the Aspect Cloud line).
