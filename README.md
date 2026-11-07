# Workshop Allocation Optimiser — Components 1 & 2

A Streamlit application for validating school workshop workbooks and allocating sessions with Google OR-Tools CP-SAT. The engine optimises across all students and sessions together. All examples and tests are synthetic. The Component 3 reporting/comparison dashboard is not included.

## Run locally

Use Python 3.11 or newer. Python 3.14 is supported by the pinned Streamlit, PyArrow and OR-Tools versions.

```bash
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell instead: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Open the local address printed by Streamlit. The repository config binds to `127.0.0.1`, disables usage telemetry, and limits uploads to 10 MB. Stop with Ctrl+C. No database, credentials or remote solver is needed.

```bash
python -m pytest -q
```

## Workflow and workbook

1. Download the blank template or synthetic example, or open `examples/synthetic_demo.xlsx`.
2. Complete the workbook, preserving these three worksheets:
   - **Students & Preferences**: `Student_ID`, `Class`, `Choice_1`, `Choice_2`, …
   - **Workshops & Schedule**: `Workshop_ID`, `Workshop_Name`, `Session_ID`, `Day`, `Time_Slot`, `Capacity`
   - **Rules**: `Rule`, `Value` (one rule per row), plus optional `Description` and `Accepted_Values` guidance.
3. Upload it. Review validation messages, counts and previews; download the JSON validation report if needed.
4. Choose an allocation strategy and time limit, then click **Run Allocation**. Invalid input cannot be run.
5. Review assignments, per-student outcomes, incomplete allocations and solver certification. Changing the workbook, strategy or time limit clears stale results. The UI strategy override is explicit and does not modify the workbook.

The three-sheet template, defaults, column names and parser are retained. Older two-column Rules sheets still work. The optional template descriptions were written for Component 1: references to a future engine, Balanced-only support, and blank-choice overrides are superseded by the Component 2 rules below. Guidance is not solver input.

### Validation and input contract

Identifiers, class, workshop names, day and time slot are literal text. Numeric-looking IDs must be stored as Excel text to preserve leading zeros. Matching is case-sensitive. Trailing spaces in schedule Workshop_ID and student Choice_N references are ignored with cell-level warnings; previews and validated data show the resulting IDs, while the original workbook is unchanged. Leading spaces and internal spaces are not removed. Other fields require text without surrounding whitespace.

Choice headers must be consecutive from Choice_1; the number of columns is detected automatically. Empty choice cells and gaps are allowed, and submitted ranks are preserved. Duplicates and unknown Workshop_ID values are errors. Entirely empty rows are ignored, while report row numbers match Excel. Formulas are rejected. Enter Day and Time_Slot as text labels, not Excel date/time values. Each scheduled session has a unique Session_ID, a positive integer capacity and one Workshop_ID. Multiple sessions of that workshop must have the same Workshop_Name.

| Rule | Default | Meaning / accepted values |
| --- | --- | --- |
| Workshops_Required_Per_Student | 2 | Positive integer: target sessions per student. |
| Allow_Unranked_Allocation | false | Excel TRUE/FALSE or lowercase `true`/`false`: permits workshops absent from a student's submitted choices. |
| Allow_Repeat_Workshop | false | Boolean: permits different sessions of the same Workshop_ID for a student. |
| Allocation_Strategy | Balanced | One of the four strategy names below. |
| Fairness_Top_K | 3 | Positive integer: coverage of the first K valid submitted choices, capped at that student's valid choice count. |
| Lottery_Seed | 42 | Nonnegative integer: reproducible seeded tie-breaking. |

Omitted rules use documented defaults with warnings; blank, unknown or duplicate rules are errors. **Component 2's strict eligibility supersedes the earlier blank-choice exception:** with unranked allocation false, even partial or blank-choice students can receive only workshops they ranked. With it true, unranked workshops are eligible with zero points. Students with no choices are included in the same global optimisation, not processed sequentially at the end. Blank cells are never populated with invented preferences.

Validation warns when aggregate capacity is below requested places. Sufficient aggregate capacity does not establish feasibility: timing, eligibility and repetition constraints also matter. Uploads above 10 MB, archives above 50 MB expanded, or sheets above 100,000 rows / 2,000 columns are rejected. `.xlsx` only.

## Allocation principles

Binary student/session variables enforce all hard constraints:

- Session capacity is never exceeded.
- Each student attends at most one session for an exactly equal `(Day, Time_Slot)` pair. Other days or slots do not clash. Uniform duration is assumed.
- Each student receives at most the required number of sessions. Incomplete allocation is allowed.
- Repetition false means at most one session per Workshop_ID; true permits multiple distinct, non-clashing sessions, but not the same session twice.
- Unranked eligibility follows the global rule above.

Linear indicator constraints track full allocation, any allocation, first-choice coverage and top-K coverage. Preference satisfaction is counted once per student/workshop even if repeated sessions are allowed. The default integer score for rank `r` is `N - r + 1`, where `N` is the number of Choice_N columns, so equivalent ranks have equal value across students. Unranked assignments score zero. Output Preference_Rank preserves the original Choice_N position; first choice means Choice_1, while top-K coverage uses the first K nonblank submitted choices.

### Strategy priorities

Every strategy first maximises **fully allocated students**, then **total assigned places**. Subsequent priorities are:

| Strategy | Third objective | Fourth objective | Final objective |
| --- | --- | --- | --- |
| Balanced | Students with at least one top-K choice | Preference satisfaction | Seeded tie-break |
| First-Choice Priority | Students receiving Choice_1 | Preference satisfaction | Seeded tie-break |
| Overall Satisfaction | Preference satisfaction | Top-K coverage | Seeded tie-break |
| Maximum Participation | Students with any allocation | Preference satisfaction | Seeded tie-break |

These are separate lexicographic solves. Only a **proven optimum** is fixed before the next objective starts. A timeout ends the sequence and returns the best known feasible assignment; an unproven objective is never treated as optimal. The time budget covers validation, model construction and the objective sequence; preprocessing and result formatting can extend total elapsed time beyond the requested search budget. Output includes each stage's status/value/certification and total elapsed time. If search produces no solution before its limit, the safe empty assignment is available and is explicitly not certified optimal.

Students who remain incomplete receive a neutral explanation. Neither a partially filled solution nor a timeout establishes a particular cause of infeasibility. Even a fully proven lexicographic optimum may leave students incomplete. Since assignment is optional, infeasible full-allocation requirements normally yield a feasible partial or empty solution instead of solver infeasibility.

Identifiers are sorted before modelling. A local RNG seeded by Lottery_Seed supplies tie-break coefficients, and CP-SAT uses one worker. Same inputs, settings, seed, solver version and completed search reproduce results regardless of workbook row order. Runs interrupted by wall-clock limits can differ because search time depends on the machine; inspect stage certifications. This is an optimisation tie-break, not a guarantee of equal lottery probabilities. Lexicographic solving and student/session variables can be expensive on large school workbooks; use the time limit and inspect status.

## Engine API and modules

```python
from pathlib import Path
from workshop_input.validation import validate_workbook
from workshop_allocation import allocate

validated = validate_workbook(Path("examples/synthetic_demo.xlsx").read_bytes())
if not validated.valid:
    raise ValueError(validated.issue_frame().to_string())
result = allocate(validated, strategy="Balanced", time_limit_seconds=30)
print(result.summary)
print(result.assignments)
```

The engine requires a successful Component 1 ValidationResult, revalidates the data boundary, and raises ValueError for invalid inputs or unsupported settings. It never uses the old blank-choice exception metadata to bypass eligibility. An optional `scoring(rank, N)` callable configures ranked preference points; use strictly decreasing positive integer scores as ranks increase, so every ranked workshop scores above an unranked one. Unranked points remain zero.

- `workshop_input/`: existing parser, workbook templates, validation and input models.
- `workshop_allocation/`: independently testable CP-SAT model, staged solves and structured results.
- `app.py`: existing validation flow plus single-run allocation UI.
- `tests/test_allocation.py`: hand-verifiable synthetic cases, strategy priorities, hard constraints, repeats, eligibility, row-order reproducibility, timeout behaviour and invalid input.
- `examples/synthetic_demo.xlsx`: existing synthetic template example with two students, three workshops and four sessions; all students can receive two ranked, non-clashing workshops.

Assignments contain Student_ID, Class, Workshop_ID, Workshop_Name, Session_ID, Day, Time_Slot, nullable Preference_Rank and Is_Unranked_Allocation. Student summaries contain required/allocated counts and full/top-K/first-choice flags. The overall summary includes population/session/capacity totals, complete/partial/zero counts, assigned places, coverage counts, ranked/unranked assignments, preference score, status, timing and certification. Coverage is a student count, not a percentage. Zero-allocation outputs retain their columns.

## Privacy and deployment

Uploaded workbooks and solver results stay in server memory for the current application session. Application code does not write uploads to disk, send student data to external services, log student records or use a shared data cache. Protect downloaded reports. Clear the upload or close the app when finished. Dependency installation needs network access; validation and allocation do not.

Keep the app local for real student records. A hosted Streamlit deployment processes uploads on its server; do not upload real records to a public deployment without an appropriate access/privacy review. This component adds no external telemetry, external solver or authentication service.

For synthetic testing on Streamlit Community Cloud, deploy branch `main`, entrypoint `app.py`. Python 3.14 is supported by the pinned dependencies; reboot if changes do not trigger a rebuild. The multi-strategy comparison dashboard and advanced allocation exports belong to Component 3 and are not implemented.

## Verification

The full automated suite passes on Python 3.12.14 and 3.14.9: **87 tests** on each. Tests include known objective tradeoffs, constraints, repeat scoring, four strategies, deterministic row-order behaviour, invalid inputs, and feasible/unknown timeout outcomes. Browser checks exercise each strategy, strict blank-choice eligibility, zero allocations, upload gating, and stale-result clearing.
