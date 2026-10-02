# selective_bazel

Companion repo for the BazelCon 2026 talk **"Stop delivering your whole fleet for a single change."**

It answers one question with real CI runs: for a repo with 50 heavy tests, what does it cost to run them on one runner, versus asking the remote cache which ones are affected and sharding only those across runners?

The only Aspect CLI command used here is [`aspect cache diff`](https://aspect.build/docs/cli). Everything else is stock Bazel and GitHub Actions.

## The repo

```
core/         one shared library                       edit core.txt   → all 50 tests affected
services/
  alpha/      lib + 10 heavy tests (4 small, 4 medium, 2 large)   edit lib.txt → 10 tests affected
  beta/       lib + 10 heavy tests
  gamma/      lib + 10 heavy tests
  delta/      lib + 10 heavy tests
  epsilon/    lib + 10 heavy tests
```

Each test burns one CPU core for a fixed time set by its Bazel `size` — small 15 s, medium 60 s, large 240 s — and reads its library files, so a change to any of them changes the test action's inputs (its cache key). A full run is 65 CPU-minutes, about 17 minutes of wall clock on one 4-core runner.

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
  - run: python3 tools/shard.py affected.json 13 >> "$GITHUB_OUTPUT"   # plan shards by cost
test:
  strategy: { matrix: "${{ fromJSON(needs.select.outputs.matrix) }}" }
  - run: bazel test ${{ matrix.targets }}
```

`aspect cache diff` runs one `bazel test` with `--experimental_remote_require_cached` and `--remote_grpc_log`: every action asks the remote cache whether it is already built, a miss is denied instead of run, and the gRPC log says which tests had a miss somewhere in their closure. Nothing executes. The result is the affected set as data, which is what you need before you can shard.

[`tools/shard.py`](tools/shard.py) then plans the shards. Every shard is a job that pays for checkout, tool install and Bazel analysis before it runs a single test (about a minute here), so dealing tests out one per runner would spend a large share of every job on overhead. The planner works by cost instead of count:

1. ask Bazel for each affected test's `size` and turn it into an expected duration (a real repo would use historical durations);
2. estimate a shard's wall time on a 4-core runner as `max(longest test, total / 4)`;
3. keep related tests together: group by package (tests in one package share their dependencies, so one runner fetches them once) and cut each package into chunks that fit a 4-minute target;
4. pack chunks into shards, largest first, while a shard stays within the target; never more than 13 shards (a personal GitHub account runs 20 jobs at once).

So all 50 tests become 5 shards, one whole service each, planned at ~4 minutes; the 10 tests of one service become 1 shard; a single test becomes 1 shard; 0 skips the test job. Shard names like `gamma:00-09 ~4m00s` are the job names in the Actions UI, and the job summary shows the plan next to the actual numbers.

## The remote cache

`aspect cache diff` is strictly a remote-cache operation, so the one thing this repo needs is a cache that CI can read and write. It uses the [**Aspect Cloud remote cache**](https://aspect.build): a hosted REv2 cache you can point any Bazel build at with two flags and no infrastructure of your own, and it is on the [free tier](https://aspect.build/pricing).

The whole configuration is in [`.bazelrc`](.bazelrc):

```
build --remote_cache=grpcs://cache.aspect.build
build --remote_cache_header=X-Aspect=<your token>      # lives in user.bazelrc, gitignored
build --bes_backend=grpcs://bes.aspect.build
build --bes_results_url=https://app.aspect.build/i/
build --credential_helper=bes.aspect.build=aspect
```

The cache takes the API token as a header; CI writes that line into `user.bazelrc` from the `ASPECT_API_TOKEN` secret, and `.bazelrc` `try-import`s the file. No cache servers to run, and the same cache is shared by every job, branch and laptop that has the token.

The build event stream goes to Aspect Cloud too, so every `bazel test` in the logs starts with a link to its page in the UI. BES wants the short-lived JWT that `aspect auth login` creates rather than the token itself, so Bazel asks the Aspect CLI for it through the credential helper line; on CI, `setup-aspect` does the login from the same secret.

### How the cache gets its baseline

A cache hit means "unaffected", so the cache must hold the mainline's results. That happens as a by-product of the pipelines themselves: every `bazel test` on `main` uploads its results. There is no seed step.

The one rule: the baseline and the probe must resolve the same Bazel flags, so they compute the same action keys. Both pipelines read the same `.bazelrc`, so they match.

### Keeping the comparison honest

Both pipelines run on every push, at the same time, against the same cache. Left alone, the shards' uploads land while the single runner is still working through its queue, and it takes them as cache hits; its first measured full run came out at under five minutes that way. So `single-runner.yml` adds `--action_env=PIPELINE=single-runner`, which enters every action key and gives that pipeline its own cache namespace. Each pipeline now only ever sees its own uploads, which is what a team running one or the other would see.

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
aspect auth login                # once; the BES credential helper reads the stored JWT
bazel test //...                 # seed the baseline (or let CI do it)
echo 'core v2' > core/core.txt
aspect cache diff                # → 50 labels on stdout, reasons on stderr, nothing executed
aspect cache diff --output=json | python3 tools/shard.py /dev/stdin 13
```

Any other REv2 cache works too, e.g. [bazel-remote](https://github.com/buchgr/bazel-remote): put `build --remote_cache=grpc://127.0.0.1:9092` in `user.bazelrc` instead (it overrides the Aspect Cloud line).
