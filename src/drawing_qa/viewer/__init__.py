"""The Case Review window: go through one run's review cases and record decisions in the QA register.

    python -m drawing_qa.viewer --output reports/Main      (or double-click "Case Review")

Everything with logic is in the small modules here (run_folder, cases, register_writer, saver, review_switch,
filters); window.py only wires tkinter widgets to them. The drawings are only read, never changed.
"""
