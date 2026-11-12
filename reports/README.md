# Reports: the review results

Each review saves its results in a folder here, named after its input folder (`input/Main` -> `reports/Main`),
unless another output folder is chosen in the Drawing QA window:

| File | What it is |
|---|---|
| `QA_Register.xlsx` | the findings, one row each, with a Decision column for the checker; decisions are carried to the next run |
| `report.md` | the summary: sheets read, traces, findings by rule |
| `system_map.html` | the package as a map, panel to PLC module to JB to field device; open it in a browser |
| `findings.json`, `review.json` | everything the run produced, for other tools |

Results are made from the client's drawings, so everything in `reports/` except this file is left out of git.
