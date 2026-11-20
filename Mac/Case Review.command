#!/bin/sh
# Case Review (Mac): opens the window to go through a run's review cases and record decisions in the QA register.
# Run Set Up once first on a new Mac.
cd "$(dirname "$0")/.." || exit 1
if [ ! -x .venv/bin/python ]; then
    osascript -e 'display dialog "The tool is not set up on this Mac yet. Double-click Set Up first." with title "Case Review: error" buttons {"OK"} with icon stop' >/dev/null 2>&1
    exit 1
fi
if ! .venv/bin/python -c "import tkinter" 2>/dev/null; then
    osascript -e 'display dialog "This Python has no tkinter, which the window needs. With Homebrew: brew install python-tk@3.12, then run Set Up again. Or install Python from python.org, which includes it." with title "Case Review: error" buttons {"OK"} with icon stop' >/dev/null 2>&1
    exit 1
fi
.venv/bin/python -m drawing_qa.viewer &
