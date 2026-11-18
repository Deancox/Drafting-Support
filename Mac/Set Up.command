#!/bin/sh
# Set Up (Mac): installs everything the Drawing QA tool needs, then a box says it worked or what went wrong.
# Double-click it once on a new Mac, and again whenever the tool is updated. Installs Python 3.12 with Homebrew
# if no Python 3.11+ is found, then the requirements into .venv in the project folder.
cd "$(dirname "$0")/.." || exit 1

box() {  # box "message": an error dialog
    osascript -e "display dialog \"$1\" with title \"Set Up: error\" buttons {\"OK\"} with icon stop" >/dev/null 2>&1
    echo "$1"
}

PY=""
for p in python3.13 python3.12 python3.11 python3 /usr/local/bin/python3 /opt/homebrew/bin/python3; do
    if command -v "$p" >/dev/null 2>&1 && "$p" -c "import sys; sys.exit(sys.version_info < (3, 11))" 2>/dev/null; then
        PY="$p"; break
    fi
done

if [ -z "$PY" ]; then
    BREW="$(command -v brew || ls /opt/homebrew/bin/brew /usr/local/bin/brew 2>/dev/null | head -1)"
    if [ -n "$BREW" ]; then
        echo "Python 3.11 or newer is not installed. Installing Python 3.12 with Homebrew ..."
        "$BREW" install python@3.12 python-tk@3.12 && PY="$("$BREW" --prefix)/bin/python3.12"
    fi
fi
if [ -z "$PY" ] || ! "$PY" -c "import sys; sys.exit(sys.version_info < (3, 11))" 2>/dev/null; then
    box "Python 3.11 or newer is not installed. Install Python 3.12 from python.org (or Homebrew from brew.sh), then run Set Up again."
    exit 1
fi

exec "$PY" "$(pwd)/Windows/launcher.py" setup "$@"
