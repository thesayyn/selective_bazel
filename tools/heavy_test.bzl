"""A test that burns one CPU core for a fixed number of seconds.

The point of this repo is wall-clock time, so every test costs the same
whatever runner it lands on. Each test reads its `deps` files so that a change
to any of them changes the test action's inputs, and therefore its cache key.

How long a test burns follows its Bazel `size`, so the sharder can plan by
cost from the attribute alone (in a real repo you would feed it historical
durations instead). Each stays well inside Bazel's default timeout for the size
(60 s / 300 s / 900 s).
"""

load("@rules_shell//shell:sh_test.bzl", "sh_test")

SIZE_SECONDS = {
    "small": 15,
    "medium": 60,
    "large": 240,
}

def heavy_test(name, deps, size = "medium", **kwargs):
    sh_test(
        name = name,
        srcs = ["//tools:heavy_test.sh"],
        args = [str(SIZE_SECONDS[size])] + ["$(rootpath %s)" % d for d in deps],
        data = deps,
        size = size,
        **kwargs
    )

def heavy_tests(prefix, deps, small = 4, medium = 4, large = 2):
    """A mixed bag of heavy tests named `<prefix>_00`, `<prefix>_01`, ...:
    the small ones first, then the medium, then the large."""
    sizes = ["small"] * small + ["medium"] * medium + ["large"] * large
    for i, size in enumerate(sizes):
        heavy_test(
            name = "%s_%s%d" % (prefix, "0" if i < 10 else "", i),
            deps = deps,
            size = size,
        )
