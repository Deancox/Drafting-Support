"""The Case Review window (tkinter). Only wiring: the logic is in the other viewer modules.

Left: the case list with filters. Right: the case (rule, message, what the rule checks, fix, crops A and B,
nearby text, drawing warnings, Copilot's verdict) and the decision buttons. Keys: Up/Down move, 1-5 decide,
0 clears, Enter accepts Copilot's verdict, / searches, W opens the wider view.
"""

from __future__ import annotations

import base64
import json
import sys
import tempfile
import tkinter as tk
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from drawing_qa.cases.crops import CropError, render_crop
from drawing_qa.gui import PREFS, open_path
from drawing_qa.viewer.cases import VERDICTS, Case, load_cases_with_problems, related_index
from drawing_qa.viewer.pictures import PictureError, render_fit
from drawing_qa.viewer.filters import CaseFilter, copilot_entry, filter_cases, plan_accept_copilot, plan_bulk
from drawing_qa.viewer.register_writer import Entry, NoDecisionColumns, has_decision_columns, is_locked
from drawing_qa.viewer.review_switch import enable_review
from drawing_qa.viewer.run_folder import RunLocation, RunNotFound, find_run, with_run_dir
from drawing_qa.viewer.saver import DecisionSaver, SaveResult

DECISIONS = [("1", "CONFIRMED", "Confirmed"), ("2", "FALSE_POSITIVE", "False positive"), ("3", "FIXED", "Fixed"),
             ("4", "WAIVED", "Waived"), ("5", "DEFERRED", "Deferred")]
STATUSES = ["UNDECIDED", "ANY", "DECIDED", "CONFIRMED", "FALSE_POSITIVE", "FIXED", "WAIVED", "DEFERRED"]
COPILOT = ["ANY", "NONE", "HIGH", "DISAGREES", *VERDICTS]
ALL = "(all)"
RETRY_MS = 5000
CROP_BOX = (600, 460)  # the most a crop is shown at in the case panel, in pixels (two sides)
WIDE_CROP_W = 900  # one side: it has the whole width of the panel
ZOOMS = (1.5, 2.0, 3.0, 4.0)
WIDE_MARGIN = 400.0
MUTED = "#6b6b66"


class ReviewApp(ttk.Frame):
    def __init__(self, root: tk.Tk, output_dir: Path | None, config_path: Path | None,
                 cases_folder: str | None = None) -> None:
        super().__init__(root, padding=10)
        self.root = root
        self.prefs_path = PREFS
        self.config_path = config_path
        self.cases_folder = cases_folder
        self.loc: RunLocation | None = None
        self.saver: DecisionSaver | None = None
        self.cases: list[Case] = []
        self.shown: list[Case] = []
        self.current: Case | None = None
        self.related: dict[str, list[str]] = {}
        self.only: frozenset[str] | None = None  # Show related: just these cases
        self._images: list[tk.PhotoImage] = []  # kept so tkinter does not drop them
        self._retry: str | None = None
        self.bar = tk.StringVar()
        self.comment = tk.StringVar()
        self.title_text = tk.StringVar(value="Case Review")
        self.rule = tk.StringVar(value=ALL)
        self.severity = tk.StringVar(value=ALL)
        self.status = tk.StringVar(value="UNDECIDED")
        self.copilot = tk.StringVar(value="ANY")
        self.search = tk.StringVar()
        self.count = tk.StringVar()
        self._build()
        self.grid(sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        for var in (self.rule, self.severity, self.status, self.copilot, self.search):
            var.trace_add("write", lambda *_: self.refresh())
        if output_dir is not None:
            self.open_output(output_dir)

    # --- prefs ----------------------------------------------------------------------------------

    def _prefs(self) -> dict:
        try:
            data = json.loads(self.prefs_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _remember(self, **values) -> None:
        prefs = self._prefs()
        prefs.update(values)
        try:
            self.prefs_path.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
        except OSError:
            pass

    def reviewer(self) -> str:
        name = self._prefs().get("reviewer", "")
        if not name:
            name = (simpledialog.askstring("Case Review", "Your name or initials, for the Reviewed by column:",
                                           parent=self.root) or "").strip()
            if name:
                self._remember(reviewer=name)
        return name

    # --- layout ---------------------------------------------------------------------------------

    def _build(self) -> None:
        top = ttk.Frame(self)
        top.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(top, textvariable=self.title_text, font=("Segoe UI", 11, "bold")).pack(side="left")
        ttk.Button(top, text="Open output folder...", command=self._browse_output).pack(side="right")
        self.find_btn = ttk.Button(top, text="Find the run folder...", command=self._browse_run)
        self.switch_btn = ttk.Button(top, text="Switch review on...", command=self._switch_review)

        self.bar_label = ttk.Label(self, textvariable=self.bar, foreground="#9a3b00", wraplength=1100)
        self.bar_label.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 4))

        filters = ttk.Frame(self)
        filters.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self.rule_box = self._combo(filters, "Rule", self.rule, [ALL], 18)
        self.sev_box = self._combo(filters, "Severity", self.severity, [ALL], 10)
        self._combo(filters, "Status", self.status, STATUSES, 15)
        self._combo(filters, "Copilot", self.copilot, COPILOT, 15)
        ttk.Label(filters, text="Search").pack(side="left", padx=(10, 4))
        self.search_entry = ttk.Entry(filters, textvariable=self.search, width=24)
        self.search_entry.pack(side="left")

        left = ttk.Frame(self)
        left.grid(row=3, column=0, sticky="nsw", padx=(0, 10))
        self.list = ttk.Treeview(left, columns=("ref", "rule", "sev", "mark"), show="headings", height=28,
                                 selectmode="browse")
        for col, text, width in (("ref", "Ref", 70), ("rule", "Rule", 130), ("sev", "Severity", 70), ("mark", "", 40)):
            self.list.heading(col, text=text)
            self.list.column(col, width=width, stretch=False)
        self.list.grid(row=0, column=0, sticky="ns")
        scroll = ttk.Scrollbar(left, command=self.list.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.list["yscrollcommand"] = scroll.set
        left.rowconfigure(0, weight=1)
        self.list.bind("<<TreeviewSelect>>", lambda _: self._on_select())
        ttk.Label(left, textvariable=self.count, foreground=MUTED).grid(row=1, column=0, sticky="w", pady=(4, 0))
        bulk = ttk.Frame(left)
        bulk.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Button(bulk, text="Decide all shown...", command=self.decide_all_shown).pack(fill="x")
        ttk.Button(bulk, text="Accept Copilot for all shown...", command=self.accept_copilot_all_shown).pack(fill="x", pady=(4, 0))

        right = ttk.Frame(self)
        right.grid(row=3, column=1, sticky="nsew")
        self.columnconfigure(1, weight=1)
        self.rowconfigure(3, weight=1)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)  # the crops get the spare height
        self.detail = tk.Text(right, wrap="word", height=13, relief="flat", background="#fbfaf7", font=("Segoe UI", 10))
        self.detail.grid(row=0, column=0, sticky="nsew")
        self.detail.tag_configure("head", font=("Segoe UI", 11, "bold"))
        self.detail.tag_configure("muted", foreground=MUTED)
        self.detail.tag_configure("label", font=("Segoe UI", 10, "bold"))
        crops = ttk.Frame(right)
        crops.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        crops.rowconfigure(0, weight=1)
        self.crop_labels = {}
        for i, role in enumerate(("A", "B")):
            frame = ttk.LabelFrame(crops, text=role, padding=4)
            frame.grid(row=0, column=i, sticky="nsew", padx=(0, 6))
            crops.columnconfigure(i, weight=1)
            label = ttk.Label(frame, anchor="center", justify="center")
            label.pack(fill="both", expand=True)
            label.bind("<Double-Button-1>", lambda _, r=role: self.open_crop(r))
            self.crop_labels[role] = (frame, label)
        actions = ttk.Frame(right)
        actions.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        ttk.Button(actions, text="Wider view (W)", command=self.wider_view).pack(side="left")
        self.accept_btn = ttk.Button(actions, text="Accept Copilot (Enter)", command=self.accept_copilot)
        self.accept_btn.pack(side="left", padx=6)
        self.related_btn = ttk.Button(actions, text="Show related (R)", command=self.show_related)
        self.related_btn.pack(side="left")
        self.all_btn = ttk.Button(actions, text="Show all", command=self.show_all)
        decide = ttk.Frame(right)
        decide.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        for key, value, label in DECISIONS:
            ttk.Button(decide, text=f"{key} {label}", command=lambda v=value: self.decide(v)).pack(side="left", padx=(0, 4))
        ttk.Button(decide, text="0 Clear", command=lambda: self.decide("OPEN")).pack(side="left", padx=(0, 4))
        ttk.Label(decide, text="Comment").pack(side="left", padx=(10, 4))
        self.comment_entry = ttk.Entry(decide, textvariable=self.comment)
        self.comment_entry.pack(side="left", fill="x", expand=True)
        for entry in (self.comment_entry, self.search_entry):  # Enter or Esc: back to the list, ready for a key
            entry.bind("<Return>", self._back_to_list)
            entry.bind("<Escape>", self._back_to_list)

        self.root.bind("<Key>", self._on_key)

    def _combo(self, parent, label, var, values, width) -> ttk.Combobox:
        ttk.Label(parent, text=label).pack(side="left", padx=(10 if parent.winfo_children() else 0, 4))
        box = ttk.Combobox(parent, textvariable=var, values=values, width=width, state="readonly")
        box.pack(side="left")
        return box

    # --- opening --------------------------------------------------------------------------------

    def _browse_output(self) -> None:
        start = self._prefs().get("viewer_output") or self._prefs().get("output") or ""
        folder = filedialog.askdirectory(title="The output folder (with QA_Register.xlsx)", initialdir=start or None,
                                         parent=self.root)
        if folder:
            self.open_output(Path(folder))

    def _browse_run(self) -> None:
        if self.loc is None:
            return
        folder = filedialog.askdirectory(title=f"The run folder (named {self.loc.run_id})", parent=self.root)
        if not folder:
            return
        try:
            self._open(with_run_dir(self.loc, Path(folder)))
        except RunNotFound as exc:
            messagebox.showerror("Case Review", str(exc), parent=self.root)

    def open_output(self, output_dir: Path) -> None:
        output_dir = Path(output_dir)
        self._remember(viewer_output=str(output_dir))
        try:
            cases_folder = self.cases_folder
            if cases_folder is None:  # started on its own: the Drawing QA window's Cases folder, as its runs used
                cases_folder = self._prefs().get("cases_folder")
            loc = find_run(output_dir, self.config_path, cases_folder if isinstance(cases_folder, str) else None)
        except Exception as exc:  # RunNotFound, or a config that can't be read
            self._clear(str(exc))
            return
        self._open(loc)

    def _open(self, loc: RunLocation) -> None:
        self.loc = loc
        self.saver = None
        self.find_btn.pack_forget()
        self.switch_btn.pack_forget()
        self.title_text.set(f"Case Review — {loc.output_dir.name} · run {loc.run_id}")
        if loc.run_dir is None:
            self._clear(f"No cases were found for run {loc.run_id}. Make the cases from the Drawing QA window, "
                        "or find the run folder by hand.")
            self.find_btn.pack(side="right", padx=6)
            return
        self.cases, problems = load_cases_with_problems(loc.run_dir)
        self.related = related_index(self.cases)
        self.only = None
        self.all_btn.pack_forget()
        notes = problems[:3]
        if not loc.register.is_file():
            notes.insert(0, f"There is no {loc.register.name} in {loc.output_dir}, so decisions can't be saved.")
        else:
            try:
                if has_decision_columns(loc.register):
                    self.saver = DecisionSaver(loc.register)
                    if self.saver.problem:
                        notes.insert(0, self.saver.problem)
                else:
                    notes.insert(0, f"{loc.register.name} was made with review off, so it has no decision columns and "
                                    "the cases are read-only. Switch review on, then run the review again.")
                    if self.config_path is not None:
                        self.switch_btn.pack(side="right", padx=6)
            except NoDecisionColumns as exc:
                notes.insert(0, str(exc))
            except Exception as exc:  # an unreadable register
                notes.insert(0, f"{loc.register.name} can't be read ({exc}), so decisions can't be saved.")
        self.rule_box["values"] = [ALL, *sorted({c.rule_id for c in self.cases})]
        self.sev_box["values"] = [ALL, *sorted({c.severity for c in self.cases})]
        self.bar.set("  ".join(notes))
        self.refresh()
        if self.saver is not None and self.saver.waiting:
            self._show_result(self.saver.flush())

    def _clear(self, message: str) -> None:
        self.cases, self.shown, self.current = [], [], None
        self.list.delete(*self.list.get_children())
        self._show(None)
        self.count.set("")
        self.bar.set(message)

    def _switch_review(self) -> None:
        if self.config_path is None:
            return
        if not messagebox.askyesno("Switch review on",
                                   f"Set [review] enabled = true in {self.config_path}?\n\nNothing else in the file "
                                   "changes. Then run the review again, so the register gets the decision columns.",
                                   parent=self.root):
            return
        try:
            enable_review(self.config_path)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Case Review", str(exc), parent=self.root)
            return
        self.switch_btn.pack_forget()
        self.bar.set("Review is on. Run the review again (Drawing QA window or Steve), then open this folder again.")

    # --- the list -------------------------------------------------------------------------------

    def _filter(self) -> CaseFilter:
        return CaseFilter(
            rule=None if self.rule.get() == ALL else self.rule.get(),
            severity=None if self.severity.get() == ALL else self.severity.get(),
            status=self.status.get(), copilot=self.copilot.get(), text=self.search.get(), only=self.only,
        )

    def _decisions(self) -> dict[str, Entry]:
        return self.saver.decisions if self.saver is not None else {}

    def refresh(self, keep: str | None = None) -> None:
        keep = keep or (self.current.fingerprint if self.current else None)
        decisions = self._decisions()
        self.shown = filter_cases(self.cases, decisions, self._filter())
        self.list.delete(*self.list.get_children())
        for i, case in enumerate(self.shown):
            decided = decisions.get(case.fingerprint)
            mark = ("✓" if decided and decided.decision != "OPEN" else "") + ("●" if case.verdict else "")
            self.list.insert("", "end", iid=str(i), values=(case.ref, case.rule_id, case.severity, mark))
        self.count.set(f"{len(self.shown)} of {len(self.cases)} shown")
        index = next((i for i, c in enumerate(self.shown) if c.fingerprint == keep), 0)
        self.select(index) if self.shown else self._show(None)

    def select(self, index: int) -> None:
        if not self.shown:
            self._show(None)
            return
        index = max(0, min(index, len(self.shown) - 1))
        iid = str(index)
        self.list.selection_set(iid)
        self.list.focus(iid)  # the list's arrow keys move from its focus item
        self.list.see(iid)
        self._show(self.shown[index])

    def _on_select(self) -> None:
        sel = self.list.selection()
        if sel and self.shown:
            self._show(self.shown[int(sel[0])])

    def _index(self) -> int:
        return next((i for i, c in enumerate(self.shown) if self.current and c.fingerprint == self.current.fingerprint), 0)

    # --- the case -------------------------------------------------------------------------------

    def _show(self, case: Case | None) -> None:
        self.current = case
        text = self.detail
        text.configure(state="normal")
        text.delete("1.0", "end")
        self._images.clear()
        for role, (frame, label) in self.crop_labels.items():
            label.configure(image="", text="")
            frame.configure(text=role)
        if case is None:
            text.insert("end", "No case to show.", "muted")
            text.configure(state="disabled")
            return
        d = case.data
        text.insert("end", f"{case.ref} · {case.rule_id} {d.get('rule_title', '')} · {case.severity}\n", "head")
        text.insert("end", f"{d.get('message', '')}\n\n")
        if case.rule_description:
            text.insert("end", "What this checks: ", "label")
            text.insert("end", f"{case.rule_description}\n")
        if d.get("recommendation"):
            text.insert("end", "Fix: ", "label")
            text.insert("end", f"{d['recommendation']}\n")
        decided = self._decisions().get(case.fingerprint)
        if self.saver is not None and decided is None:
            text.insert("end", "\nThis case is not on the register's Findings tab (an older run?), so it can't be decided.\n", "muted")
        elif decided and decided.decision != "OPEN":
            by = f" by {decided.reviewer}" if decided.reviewer else ""
            text.insert("end", "\nDecided: ", "label")
            text.insert("end", f"{decided.decision}{by} {decided.date}  {decided.comment}\n")
        related = self.related.get(case.fingerprint, [])
        if related:
            by_fp = {c.fingerprint: c for c in self.cases}
            names = ", ".join(f"{by_fp[fp].ref} {by_fp[fp].rule_id}" for fp in related[:6])
            more = f" and {len(related) - 6} more" if len(related) > 6 else ""
            text.insert("end", "\nRelated cases: ", "label")
            text.insert("end", f"{names}{more} (same tag or text; R lists them together)\n")
        text.insert("end", "\nCopilot: ", "label")
        if case.verdict is not None:
            v = case.verdict
            stale = "  (made on an older version of this finding)" if v.stale else ""
            text.insert("end", f"{v.verdict} ({v.confidence}){stale}\n{v.reasoning}\n")
        elif case.verdict_problem:
            text.insert("end", f"{case.verdict_problem}\n", "muted")
        else:
            text.insert("end", "not judged\n", "muted")
        sides = d.get("sides") or {}
        for role in ("A", "B"):
            side = sides.get(role)
            frame, label = self.crop_labels[role]
            if not side:
                label.configure(text="No B side" if role == "B" else "No A side")
                if role == "B":
                    frame.grid_remove()  # one-sided case: A gets the whole width
                continue
            frame.grid()
            frame.configure(text=f"{role}: {side.get('drawing', '')} · {side.get('grid_cell') or '-'}  “{side.get('raw_text', '')}”")
            self._put_crop(label, case, side)
            nearby = ", ".join(f"{n.get('text')} ({n.get('grid') or '-'})" for n in side.get("nearby") or [])
            if nearby:
                text.insert("end", f"\nNearby text, {role}: ", "label")
                text.insert("end", f"{nearby}\n", "muted")
            for warning in side.get("drawing_warnings") or []:
                text.insert("end", f"\nDrawing warning, {role}: ", "label")
                text.insert("end", f"{warning}\n")
        text.configure(state="disabled")
        usable = case.verdict is not None and not case.verdict.stale
        self.accept_btn.configure(state="normal" if usable and self.saver else "disabled")
        self.related_btn.configure(state="normal" if related else "disabled")

    def _put_crop(self, label: ttk.Label, case: Case, side: dict) -> None:
        one_sided = not (case.data.get("sides") or {}).get("B")
        image = self._crop_from_pdf(side, (WIDE_CROP_W, CROP_BOX[1]) if one_sided else CROP_BOX)
        if image is not None:
            self._images.append(image)
            label.configure(image=image, text="")
            return
        path = case.cases_dir / str(side.get("crop") or "")
        if side.get("crop_problem") or not path.is_file():
            label.configure(text=side.get("crop_problem") or "No picture for this side")
            return
        try:
            image = tk.PhotoImage(file=str(path))
        except tk.TclError as exc:
            label.configure(text=f"The picture can't be shown: {exc}")
            return
        step = max(1, -(-image.width() // CROP_BOX[0]), -(-image.height() // CROP_BOX[1]))
        if step > 1:
            image = image.subsample(step)
        self._images.append(image)
        label.configure(image=image, text="")

    def _crop_from_pdf(self, side: dict, size: tuple[int, int]) -> tk.PhotoImage | None:
        """The crop drawn from the PDF at the panel's size, sharp; None when the drawings are not to hand."""
        if self.loc is None or self.loc.input_root is None or not side.get("source_pdf") or not side.get("bbox"):
            return None
        try:
            png = render_fit(self.loc.input_root / side["source_pdf"], int(side.get("page_index") or 0), side["bbox"], *size)
            return tk.PhotoImage(data=base64.b64encode(png).decode("ascii"))
        except (PictureError, tk.TclError):
            return None  # the case's own PNG is shown instead

    def open_crop(self, role: str) -> None:
        if self.current is None:
            return
        side = (self.current.data.get("sides") or {}).get(role)
        if side and (self.current.cases_dir / str(side.get("crop") or "")).is_file():
            open_path(self.current.cases_dir / side["crop"])

    # --- deciding -------------------------------------------------------------------------------

    def _ready_to_decide(self) -> bool:
        if self.saver is None:
            self.root.bell()
            return False
        return True

    def decide(self, decision: str) -> None:
        case = self.current
        if case is None or not self._ready_to_decide():
            return
        if case.fingerprint not in self.saver.decisions:
            self.root.bell()
            return
        reviewer = self.reviewer()
        entry = Entry(decision, self.comment.get().strip(), reviewer, date.today().isoformat())
        if decision == "OPEN":
            entry = Entry("OPEN", "", "", "")
        self._apply({case.fingerprint: entry}, after=self._index())

    def accept_copilot(self) -> None:
        case = self.current
        if case is None or not self._ready_to_decide() or case.fingerprint not in self.saver.decisions:
            return
        entry = copilot_entry(case, self.reviewer(), date.today().isoformat())
        if entry is None:
            self.root.bell()
            return
        self._apply({case.fingerprint: entry}, after=self._index())

    def decide_all_shown(self) -> None:
        if not self.shown or not self._ready_to_decide():
            return
        choice = _ask_decision(self.root, len(self.shown))
        if choice is None:
            return
        decision, comment = choice
        plan = plan_bulk(self.shown, self.saver.decisions, decision, comment, self.reviewer(), date.today().isoformat())
        skipped = f"\n{plan.skipped} skipped (not in the register)." if plan.skipped else ""
        if plan.changes and messagebox.askyesno(
            "Decide all shown",
            f"Set {len(plan.changes)} cases to {decision}? {plan.overwrites} already have a different decision "
            f"and will be overwritten.{skipped}", parent=self.root):
            self._apply(plan.changes, after=self._index())

    def accept_copilot_all_shown(self) -> None:
        if not self.shown or not self._ready_to_decide():
            return
        plan = plan_accept_copilot(self.shown, self.saver.decisions, self.reviewer(), date.today().isoformat())
        if not plan.changes:
            messagebox.showinfo("Accept Copilot", "None of the cases shown has a current Copilot verdict.", parent=self.root)
            return
        if messagebox.askyesno(
            "Accept Copilot for all shown",
            f"Set {len(plan.changes)} cases to Copilot's verdict? {plan.overwrites} already have a different decision "
            f"and will be overwritten.\n{plan.skipped} skipped (no current verdict, or not in the register).",
            parent=self.root):
            self._apply(plan.changes, after=self._index())

    def _apply(self, changes: dict[str, Entry], after: int) -> None:
        try:
            result = self.saver.decide(changes)
        except Exception as exc:  # the register vanished or can't be read
            if not self.saver.waiting:
                messagebox.showerror("Case Review", f"The decision could not be saved: {exc}", parent=self.root)
                return
            result = None
            self._held_after_error(exc)
        self.comment.set("")
        if result is not None:
            self._show_result(result)
        decisions = self.saver.decisions
        # Move on: the first case shown after this one, or this position if it dropped out of the list.
        remaining = filter_cases(self.cases, decisions, self._filter())
        later = [c for c in remaining if c.ref > (self.shown[after].ref if self.shown else "")]
        keep = (later[0] if later else (remaining[-1] if remaining else None))
        self.refresh(keep=keep.fingerprint if keep else None)
        self.list.focus_set()

    def _back_to_list(self, _event=None) -> str:
        self.list.focus_set()
        return "break"

    def _schedule_retry(self) -> None:
        if self._retry is not None:
            self.after_cancel(self._retry)
        self._retry = self.after(RETRY_MS, self._flush)

    def _held_after_error(self, exc: Exception) -> None:
        self.bar.set(f"{self.saver.waiting} decision(s) held, not saved yet ({exc}). Trying again in a few seconds.")
        self._schedule_retry()

    def _show_result(self, result: SaveResult) -> None:
        notes = []
        if result.waiting:
            name = self.loc.register.name
            note = f"{result.waiting} decision(s) waiting — close {name} in Excel and they will be saved."
            if is_locked(self.loc.register):
                note += (f" (If Excel isn't open, a crash may have left the file ~${name} behind in "
                         f"{self.loc.output_dir}; delete it.)")
            notes.append(note)
        if result.gone:
            notes.append(f"{len(result.gone)} decision(s) dropped: no longer on the register ({', '.join(result.gone[:5])}).")
        if result.saved and not notes:
            notes.append(f"Saved to {self.loc.register.name}.")
        self.bar.set("  ".join(notes))
        for conflict in result.conflicts:
            keep_mine = messagebox.askyesno(
                "The register changed",
                f"Case {conflict.fingerprint} was changed in the register after you decided it.\n\n"
                f"Yours: {conflict.mine.decision} {conflict.mine.comment} ({conflict.mine.reviewer} {conflict.mine.date})\n"
                f"Register: {conflict.theirs.decision} {conflict.theirs.comment} "
                f"({conflict.theirs.reviewer} {conflict.theirs.date})\n\nKeep yours? (No keeps the register's.)",
                parent=self.root)
            self._show_result(self.saver.resolve(conflict.fingerprint, "mine" if keep_mine else "theirs"))
            return
        if result.waiting and self._retry is None:
            self._schedule_retry()

    def _flush(self) -> None:
        if self._retry is not None:
            self.after_cancel(self._retry)
        self._retry = None
        if self.saver is None:
            return
        try:
            result = self.saver.flush()
        except Exception as exc:
            self._held_after_error(exc)
            return
        self._show_result(result)
        if result.saved or result.gone or result.conflicts:  # a quiet retry leaves the list and case as they are
            self.refresh()

    def closing(self) -> bool:
        """Save what is waiting; True when the window may close."""
        if self.saver is None or not self.saver.waiting:
            return True
        self._flush()  # cancels the scheduled retry first, so declining to close doesn't start a second one
        if self.saver.waiting:
            return messagebox.askyesno(
                "Case Review", f"{self.saver.waiting} decision(s) are still waiting for {self.loc.register.name} to be "
                "closed in Excel. They are kept and saved the next time Case Review opens this folder. Close now?",
                parent=self.root)
        return True

    # --- related cases --------------------------------------------------------------------------

    def show_related(self) -> None:
        """List this case and the cases related to it, whatever their status, ready to decide together."""
        case = self.current
        if case is None or not self.related.get(case.fingerprint):
            return
        self.only = frozenset([case.fingerprint, *self.related[case.fingerprint]])
        self.all_btn.pack(side="left", padx=6)
        if self.status.get() != "ANY":
            self.status.set("ANY")  # refreshes
        else:
            self.refresh(keep=case.fingerprint)

    def show_all(self) -> None:
        self.only = None
        self.all_btn.pack_forget()
        self.refresh()

    # --- wider view -----------------------------------------------------------------------------

    def wider_view(self) -> None:
        case = self.current
        if case is None:
            return
        side = (case.data.get("sides") or {}).get("A")
        if not side or not side.get("bbox") or not side.get("source_pdf"):
            messagebox.showinfo("Wider view", "This case has no spot on a drawing to show.", parent=self.root)
            return
        if self.loc is None or self.loc.input_root is None:
            messagebox.showinfo("Wider view", "The drawing package folder is not known or no longer there, so the "
                                "sheet can't be shown.", parent=self.root)
            return
        WiderView(self.root, self.loc.input_root, case.data.get("sides") or {})

    # --- keys -----------------------------------------------------------------------------------

    def _on_key(self, event: tk.Event) -> None:
        if isinstance(event.widget, (tk.Entry, ttk.Entry, ttk.Combobox)):
            if event.keysym == "Escape":
                self.list.focus_set()
            return
        keys = {k: v for k, v, _ in DECISIONS}
        if event.char in keys:
            self.decide(keys[event.char])
        elif event.char == "0":
            self.decide("OPEN")
        elif event.keysym == "Return":
            self.accept_copilot()
        elif event.char == "/":
            self.search_entry.focus_set()
        elif event.char in ("w", "W"):
            self.wider_view()
        elif event.char in ("r", "R"):
            self.show_related()


class WiderView(tk.Toplevel):
    """A bigger area of the sheet around the evidence, drawn from the PDF (read-only), with zoom steps."""

    def __init__(self, root: tk.Tk, input_root: Path, sides: dict) -> None:
        super().__init__(root)
        self.input_root = input_root
        self.sides = {r: s for r, s in sides.items() if s and s.get("bbox") and s.get("source_pdf")}
        self.role = tk.StringVar(value="A")
        self.zoom = 1
        self.image: tk.PhotoImage | None = None
        self.title("Wider view")
        self.geometry("1100x800")
        bar = ttk.Frame(self, padding=6)
        bar.pack(fill="x")
        if "B" in self.sides:
            for role in self.sides:
                ttk.Radiobutton(bar, text=role, value=role, variable=self.role, command=self.draw).pack(side="left")
        ttk.Button(bar, text="−", width=3, command=lambda: self._step(-1)).pack(side="left", padx=(10, 2))
        ttk.Button(bar, text="+", width=3, command=lambda: self._step(1)).pack(side="left")
        self.caption = ttk.Label(bar, foreground=MUTED)
        self.caption.pack(side="left", padx=10)
        self.canvas = tk.Canvas(self, background="#ffffff")
        xs = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        ys = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=xs.set, yscrollcommand=ys.set)
        ys.pack(side="right", fill="y")
        xs.pack(side="bottom", fill="x")
        self.canvas.pack(fill="both", expand=True)
        self.draw()

    def _step(self, delta: int) -> None:
        self.zoom = max(0, min(len(ZOOMS) - 1, self.zoom + delta))
        self.draw()

    def draw(self) -> None:
        side = self.sides[self.role.get()]
        pdf = self.input_root / side["source_pdf"]
        self.caption.configure(text=f"{side.get('drawing', '')} · {side.get('grid_cell') or '-'} · zoom {ZOOMS[self.zoom]}×")
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "view.png"
            try:
                render_crop(pdf, int(side.get("page_index") or 0), side["bbox"], png, margin=WIDE_MARGIN,
                            zoom=ZOOMS[self.zoom])
                self.image = tk.PhotoImage(file=str(png))
            except (CropError, tk.TclError) as exc:
                self.canvas.delete("all")
                self.canvas.create_text(20, 20, anchor="nw", text=str(exc))
                return
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self.image)
        self.canvas.configure(scrollregion=(0, 0, self.image.width(), self.image.height()))
        self.canvas.xview_moveto(max(0.0, 0.5 - 550 / max(1, self.image.width())))
        self.canvas.yview_moveto(max(0.0, 0.5 - 400 / max(1, self.image.height())))


def _ask_decision(root: tk.Tk, count: int) -> tuple[str, str] | None:
    """A small dialog: which decision for all shown, and an optional comment. None when cancelled."""
    dialog = tk.Toplevel(root)
    dialog.title("Decide all shown")
    dialog.transient(root)
    dialog.grab_set()
    choice = tk.StringVar(value="FALSE_POSITIVE")
    comment = tk.StringVar()
    answer: list[tuple[str, str]] = []
    frame = ttk.Frame(dialog, padding=12)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text=f"Decision for the {count} cases shown:").pack(anchor="w")
    for _, value, label in DECISIONS:
        ttk.Radiobutton(frame, text=label, value=value, variable=choice).pack(anchor="w")
    ttk.Label(frame, text="Comment (optional)").pack(anchor="w", pady=(8, 2))
    ttk.Entry(frame, textvariable=comment, width=50).pack(fill="x")
    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(10, 0))

    def ok() -> None:
        answer.append((choice.get(), comment.get().strip()))
        dialog.destroy()

    ttk.Button(buttons, text="Next...", command=ok).pack(side="right")
    ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right", padx=6)
    root.wait_window(dialog)
    return answer[0] if answer else None


def main(output_dir: Path | None, config_path: Path | None, cases_folder: str | None = None) -> None:
    root = tk.Tk()
    root.title("Case Review")
    root.geometry("1280x860")
    root.minsize(1000, 680)
    try:
        ttk.Style().theme_use("vista" if sys.platform == "win32" else "clam")
    except tk.TclError:
        pass
    app = ReviewApp(root, output_dir, config_path, cases_folder)
    if output_dir is None:
        last = app._prefs().get("viewer_output")
        if last and Path(last).is_dir():
            app.open_output(Path(last))
        else:
            app.bar.set("Open an output folder (the one with QA_Register.xlsx) to start.")

    def closing() -> None:
        if app.closing():
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", closing)
    root.mainloop()
