#!/usr/bin/env bash
#
# Wrapper that makes the daily job safe to run from cron.
#
# cron is not a login shell: it starts with a near-empty PATH, no profile, and no
# working directory to speak of. Everything this script does is about putting that
# back, so that the job behaves the same whether you ran it or cron did.
#
# Install it with `crontab -e`, e.g. to run at 07:15 every morning:
#
#   MAILTO=you@example.com
#   15 7 * * * /path/to/CMIP-Output-Tracking/scripts/run-daily.sh
#
# MAILTO is worth setting: cron mails whatever the job prints to stderr, which is
# the only notification left if the job dies before it can send its own.

set -euo pipefail

# Resolve the repository from this script's own location, so the crontab entry can
# use any path and the job still runs in the right place.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# uv is usually installed per-user, which is exactly the sort of thing cron's PATH
# leaves out. Add the usual homes for it, keeping anything already there.
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:/opt/homebrew/bin:$PATH"

if ! command -v uv > /dev/null; then
    echo "uv is not on PATH. Either install it or add its directory to the PATH line in $0" >&2
    exit 1
fi

LOG_DIR="$REPO_ROOT/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/search-and-notifications-$(date +%Y-%m).log"

{
    echo "=== $(date --iso-8601=seconds 2> /dev/null || date +%Y-%m-%dT%H:%M:%S%z) starting ==="
    # The job's own output goes to the log; a non-zero exit is re-raised below so
    # that cron (and MAILTO) still hear about a failure.
    make run-search-and-notifications
    echo "=== finished ok ==="
} >> "$LOG_FILE" 2>&1 || {
    status=$?
    echo "Daily CMIP search job FAILED (exit $status). Last 40 lines of $LOG_FILE:" >&2
    tail -40 "$LOG_FILE" >&2
    exit "$status"
}
