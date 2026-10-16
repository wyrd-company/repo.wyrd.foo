#!/bin/sh
# ---
# relationships:
#   verifies: package-repository-publishing
# ---
set -eu
printf '%s\n' post-install >> /run/sample-lifecycle
