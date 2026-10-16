#!/bin/sh
# ---
# relationships:
#   verifies: package-repository-publishing
# ---
set -eu
[ "$1" = remove ]
printf '%s\n' post-remove >> /run/sample-lifecycle
