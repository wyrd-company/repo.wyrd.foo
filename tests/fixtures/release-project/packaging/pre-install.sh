#!/bin/sh
# ---
# relationships:
#   verifies: package-repository-publishing
# ---
set -eu
printf '%s\n' pre-install >> /run/sample-lifecycle
[ ! -e /run/sample-stop-fails ]
