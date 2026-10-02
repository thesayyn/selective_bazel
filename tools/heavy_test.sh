#!/usr/bin/env bash
# Usage: heavy_test.sh <seconds> <input files...>
# Reads every input (so they are real inputs of the test action), then keeps
# one core busy until <seconds> have elapsed. Always passes.
set -euo pipefail

seconds="$1"
shift

for f in "$@"; do
  echo "input: $f -> $(cat "$f")"
done

# $SECONDS is bash's wall clock since shell start.
SECONDS=0
x=0
while (( SECONDS < seconds )); do
  x=$(( (x * 1103515245 + 12345) % 2147483648 ))
done
echo "burned ${seconds}s of CPU (checksum $x)"
