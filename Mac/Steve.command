#!/bin/sh
# Steve (Mac): reviews the drawings in input/Main and saves the results in reports/Main, then a box says how it
# went. Run Set Up once first on a new Mac.
cd "$(dirname "$0")/.." || exit 1
if [ ! -x .venv/bin/python ]; then
    osascript -e 'display dialog "The tool is not set up on this Mac yet. Double-click Set Up first." with title "Steve: error" buttons {"OK"} with icon stop' >/dev/null 2>&1
    exit 1
fi
exec .venv/bin/python "$(pwd)/steve.py" review
