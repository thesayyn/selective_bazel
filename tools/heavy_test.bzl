"""A test that burns one CPU core for a fixed number of seconds.

The point of this repo is wall-clock time, so every test costs the same
whatever runner it lands on. Each test reads its `deps` files so that a change
to any of them changes the test action's inputs, and therefore its cache key.
"""

load("@rules_shell//shell:sh_test.bzl", "sh_test")

def heavy_test(name, deps, seconds = 30, **kwargs):
    sh_test(
        name = name,
        srcs = ["//tools:heavy_test.sh"],
        args = [str(seconds)] + ["$(rootpath %s)" % d for d in deps],
        data = deps,
        size = "medium",
        **kwargs
    )

def heavy_tests(prefix, count, deps, seconds = 30):
    """`count` identical heavy tests named `<prefix>_00`, `<prefix>_01`, ..."""
    for i in range(count):
        heavy_test(
            name = "%s_%s%d" % (prefix, "0" if i < 10 else "", i),
            deps = deps,
            seconds = seconds,
        )
