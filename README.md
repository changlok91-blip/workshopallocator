# Workshop Allocation Optimiser — Component 1

A local Streamlit application for downloading, uploading and validating school workshop input workbooks. **No allocation engine is implemented.** All included examples are synthetic.

## Run locally

Requires Python 3.11 or newer. Python 3.14 is supported with Streamlit 1.67.0 and PyArrow 26.0.1; the full test suite and browser upload workflow were verified on Python 3.14.9.

```bash
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell instead: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Open the local address printed by Streamlit. The repository config binds the app to `127.0.0.1`, disables usage telemetry, and limits uploads to 10 MB. Stop with Ctrl+C.

```bash
python -m pytest -q
```

## Workflow

1. Download the blank template or synthetic example.
2. Fill in the workbook, preserving exactly these three worksheets:
   - **Students & Preferences**: `Student_ID`, `Class`, `Choice_1`, `Choice_2`, …
   - **Workshops & Schedule**: `Workshop_ID`, `Workshop_Name`, `Session_ID`, `Day`, `Time_Slot`, `Capacity`
   - **Rules**: `Rule`, `Value` (one rule per row), plus optional `Description` and `Accepted_Values` guidance columns
3. Upload the `.xlsx` workbook. Review counts, errors, warnings and previews.
4. Download the JSON validation report. Excel row numbers include the header row. Fix errors in the source workbook and upload again.

### Input contract

The downloaded Rules sheet spells out what every rule means, gives examples and accepted values, and labels settings reserved for the future engine. Edit the yellow **Value** cells; all rules apply to every student. Descriptions wrap across tall rows so they can be read in Excel. Guidance columns are not used by the allocation data boundary, and older workbooks containing only `Rule` / `Value` remain supported. Exactly three worksheets are still required.

Identifiers, class, workshop names, day and time slot must be literal text with no surrounding whitespace. Numeric-looking identifiers must be stored as Excel text to preserve leading zeros. Matching is exact and case-sensitive. Day and Time_Slot are labels; their pair is the scheduling unit. Uniform workshop duration is assumed. Each scheduled session has a unique Session_ID and a positive integer capacity. A Workshop_ID may appear in multiple sessions with the same Workshop_Name.

Choice headers must run consecutively from Choice_1 with no gaps or unexpected columns. Any number is detected within workbook resource limits. Blank preference cells are allowed, including interior gaps and completely empty preferences. Submitted ranks and workbook values remain unchanged. Students with no choices are marked for allocation after students with preferences. For partial preferences, consider submitted choices first, then use random unranked fallback for missing choices if needed. This permission for blank choices overrides `Allow_Unranked_Allocation=false`; that rule still controls unranked allocation for complete preference rows. Random fallback must respect session capacity, timetable conflicts and the repeat-workshop rule; it does not guarantee an allocation. Component 1 records this policy and warnings only, without filling cells or allocating students. The future engine should use `Lottery_Seed` for reproducible random decisions. Duplicate choices and unknown workshop IDs are errors. There is no silent trimming, renaming, deduplication or repair. Entirely empty rows are ignored; reported row numbers still match Excel. Formulas are rejected rather than evaluated. Dates/times should be entered as text labels, not Excel date/time objects.

| Rule | Default | Accepted values |
| --- | --- | --- |
| Workshops_Required_Per_Student | 2 | Positive integer |
| Allow_Unranked_Allocation | false | Excel boolean or lowercase `true`/`false` text |
| Allow_Repeat_Workshop | false | Excel boolean or lowercase `true`/`false` text |
| Allocation_Strategy | Balanced | `Balanced` (reserved for a future engine) |
| Fairness_Top_K | 3 | Positive integer |
| Lottery_Seed | 42 | Nonnegative integer |

An omitted rule uses its documented default with a warning; an explicitly blank or invalid value is an error. Unknown and duplicate rules are errors. A complete preference row with too few ranked workshops for the requested allocations generates a warning when both unranked allocation and repeats are disallowed. Rows with blanks instead receive an explicit random-fallback policy warning.

Aggregate capacity below required allocations generates a warning. **Sufficient aggregate capacity does not establish feasibility.** No scheduling, preference compatibility, fairness optimisation or allocation is performed in this component. Counts on invalid workbooks are provisional; total capacity is shown as invalid if any capacity is invalid. Structural errors must be fixed before aggregate validation can run.

## Modules and future integration

- `workshop_input/workbook.py`: in-memory template, workbook and report I/O.
- `workshop_input/validation.py`: validation independent of Streamlit.
- `workshop_input/models.py`: result, issue and validated-data boundary.
- `app.py`: upload/download UI, counts and previews.

Call `validate_workbook(xlsx_bytes)`. Only a result without errors exposes `result.data` (`ValidatedData`), with student/session DataFrames, detected choice columns, effective typed rules and per-student `student_allocation_policy` metadata. This is the boundary for a future OR-Tools component; no engine dependency is installed. DataFrame cells retain original values; defaults apply only to omitted rules, and accepted boolean rule text is explicitly parsed.

## Privacy

Uploaded workbooks are handled in application memory and are not written to disk, sent to external services, or cached across users by application code. Do not deploy publicly or override the localhost binding for real student records without a separate security review. Streamlit retains uploads during a browser session; clear the upload or close the application when finished. Downloads contain report details and remain on your device. The application does not log student records. Dependency installation requires network access; workbook validation does not.

Resource limits reject uploads above 10 MB and archives above 50 MB expanded, and worksheets with more than 100,000 rows or 2,000 columns. This component accepts `.xlsx` only.

## Streamlit Community Cloud

Deploy branch `main` with entrypoint `app.py`. Python 3.14 is supported by the pinned requirements. The explicit PyArrow pin supplies Python 3.14 wheels and avoids the source-build failure caused by the previous Streamlit dependency limit. Reboot the app after pulling these changes if Community Cloud does not rebuild automatically.
