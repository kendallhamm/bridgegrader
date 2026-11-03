"""
Bridge Cost Grading Tool
------------------------
Streamlit app: upload a batch of bridge plan PDFs, confirm structural
soundness via a text check, extract the designer's name and submitted
cost, rank and grade qualifying submissions, and export results.
Also produces an order of merit list (OML) covering every parsed
submission: GO submissions first by cost, then NO GO submissions by cost.
If one person submits more than once, only their cheapest GO submission
is kept (or their cheapest NO GO if none of theirs passed).

Run locally with:
    streamlit run streamlit_app.py
"""

import io
import zipfile

import altair as alt
import pandas as pd
import pdfplumber
import streamlit as st

# ---------------------------------------------------------------------------
# Field extraction (name, cost, structural check)
# ---------------------------------------------------------------------------
# The plan sheets follow a fixed template, so plain string splitting is
# simpler and just as reliable as regex here:
#   - Each field is on its own "Label: value" line.
#   - The cost line always looks like "Cost: $xxx,xxx.xx Date: ... Iteration: N",
#     so the dollar amount always ends right before "Date:".
#   - A submission may run onto more than one page. Text from all pages is
#     joined in page order before searching, so the first matching line
#     (earliest page) wins when a label appears more than once.

STRUCTURAL_FAIL_LOAD = "fails load test"
STRUCTURAL_FAIL_ANALYSIS = "no valid analysis"
STRUCTURAL_PASS_PHRASE = "passes all tests"


def get_line_value(text: str, label: str) -> str:
    """Return the text after the first ':' on the line that starts with label."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(label):
            return stripped.split(":", 1)[1].strip()
    return ""


def classify_structural_status(text: str):
    """Read the "Status:" line and classify it into GO/NO GO plus a note.

    Returns (status, note):
        "fails load test"   -> ("NO GO", "fails load test")
        "no valid analysis" -> ("NO GO", "no valid analysis")
        "passes all tests"  -> ("GO", "")
        anything else / missing line -> ("NO GO", raw status text or "no status line found")
    """
    status_line = get_line_value(text, "Status")
    lower = status_line.lower()

    if STRUCTURAL_FAIL_LOAD in lower:
        return "NO GO", STRUCTURAL_FAIL_LOAD
    elif STRUCTURAL_FAIL_ANALYSIS in lower:
        return "NO GO", STRUCTURAL_FAIL_ANALYSIS
    elif STRUCTURAL_PASS_PHRASE in lower:
        return "GO", ""
    else:
        return "NO GO", status_line or "no status line found"


def extract_record(file_name: str, file_bytes: bytes):
    """Read every page of a PDF and pull the designer name, cost, and
    structural soundness status.

    All pages are joined in order, so a single submission that spans two
    or more pages is read as one plan sheet.

    Returns a dict, or None if no cost could be found (file is reported to
    the user as unparsed rather than silently skipped).
    """
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        # Join all pages so fields or the Status line on page 2+ are found.
        text = "\n".join((page.extract_text() or "") for page in pdf.pages)

    # Label includes the colon so headings like "Cost Summary" on a later
    # page are not mistaken for the cost line.
    cost_line = get_line_value(text, "Cost:")
    if not cost_line:
        return None

    # Cost always ends right before "Date:" on the same line.
    cost_str = cost_line.split("Date:")[0].strip().replace("$", "").replace(",", "")
    try:
        cost = float(cost_str)
    except ValueError:
        return None

    name = get_line_value(text, "Designed by") or "Unknown"

    status, note = classify_structural_status(text)

    return {"file": file_name, "name": name, "cost": cost, "status": status, "note": note}


# ---------------------------------------------------------------------------
# Uploads: individual PDFs and/or ZIP files of PDFs
# ---------------------------------------------------------------------------
# A ZIP (e.g. Canvas "Download Submissions") uploads as one request instead of
# one request per PDF, which avoids server errors on large batches. Each PDF
# inside a ZIP is treated exactly like an individually uploaded PDF.

def list_submissions(uploaded_files):
    """Expand uploads into individual PDF submissions without reading PDF bytes.

    Returns (submissions, skipped, bad_zips):
        submissions -> list of (display_name, uploaded_file, zip_member or None)
        skipped     -> non-PDF entries found inside ZIPs (listed, not graded)
        bad_zips    -> list of (zip_name, reason) for ZIPs that cannot be opened

    Display names are made unique (" (2)", " (3)", ...) so manual entries and
    results always point to exactly one file.
    """
    submissions = []
    skipped = []
    bad_zips = []
    seen = {}

    def unique(name):
        count = seen.get(name, 0) + 1
        seen[name] = count
        return name if count == 1 else f"{name} ({count})"

    for f in uploaded_files:
        if f.name.lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(f.getvalue())) as zf:
                    members = zf.namelist()
            except Exception as e:
                bad_zips.append((f.name, f"{type(e).__name__}: {e}"))
                continue
            for member in members:
                base = member.rsplit("/", 1)[-1]
                # Skip folders and macOS metadata entries.
                if not base or member.startswith("__MACOSX/") or base.startswith("._"):
                    continue
                if base.lower().endswith(".pdf"):
                    submissions.append((unique(base), f, member))
                else:
                    skipped.append(f"{f.name}: {member}")
        else:
            submissions.append((unique(f.name), f, None))

    return submissions, skipped, bad_zips


def read_submission_bytes(uploaded_file, zip_member):
    """Return the PDF bytes for one submission (a plain upload or a ZIP member)."""
    # getvalue() returns the full contents regardless of the file's read
    # position, so a previously read file never comes back empty.
    if zip_member is None:
        return uploaded_file.getvalue()
    with zipfile.ZipFile(io.BytesIO(uploaded_file.getvalue())) as zf:
        return zf.read(zip_member)


# ---------------------------------------------------------------------------
# Duplicate submissions (same designer name)
# ---------------------------------------------------------------------------

def normalize_name(name: str) -> str:
    """Lowercase and collapse whitespace so "Jane Doe" matches "jane  doe"."""
    return " ".join(name.split()).lower()


def dedupe_by_name(records: list[dict]):
    """Keep one submission per designer name.

    The kept submission is the cheapest GO submission for that name. If
    none of that name's submissions are GO, the cheapest NO GO is kept.
    Ties (same status and cost) keep the first one uploaded.

    Names that are blank or "Unknown" are never merged, since they most
    likely belong to different people who forgot to enter a name.

    Returns (kept, dropped):
        kept    -> records to grade/rank, in upload order
        dropped -> list of (dropped_record, kept_record) pairs
    """
    def sort_key(r):
        # GO beats NO GO; within the same status, cheaper wins.
        return (0 if r["status"] == "GO" else 1, r["cost"])

    # Pass 1: find the winning record for each name.
    winners = {}
    for r in records:
        key = normalize_name(r["name"])
        if key in ("", "unknown"):
            continue
        if key not in winners or sort_key(r) < sort_key(winners[key]):
            winners[key] = r

    # Pass 2: split into kept and dropped, pairing each dropped record with
    # its name's final winner.
    kept = []
    dropped = []
    for r in records:
        key = normalize_name(r["name"])
        if key in ("", "unknown") or winners[key] is r:
            kept.append(r)
        else:
            dropped.append((r, winners[key]))

    return kept, dropped


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------

def grade(records: list[dict], full_penalty_pct: float = 20.0) -> list[dict]:
    """Sort ascending by cost and assign grades to GO (qualifying) submissions.

    Rank 1 (cheapest):  100
    Ranks 2-3:            97
    Ranks 4-N: graded on how much more expensive the bid is than the
               3rd-cheapest (c3), as a percentage, not on where it falls
               in the batch's min-max range. This avoids stretching a
               tightly clustered batch across the full 96-80 point range
               just because someone has to be "most expensive."

        premium_pct = (cost - c3) / c3 * 100
        grade = 96 - min(premium_pct / full_penalty_pct, 1.0) * (96 - 80)

    A bid priced at c3 scores 96. A bid priced full_penalty_pct percent
    (or more) above c3 scores the floor of 80. Bids in between scale
    linearly. If the batch's actual spread never reaches full_penalty_pct,
    nobody hits 80 — scores stay clustered near 96, proportional to how
    close costs actually are.
    """
    if len(records) < 3:
        raise ValueError("Need at least 3 qualifying submissions to grade (top-3 tiers require 3).")
    if full_penalty_pct <= 0:
        raise ValueError("full_penalty_pct must be greater than 0.")

    records = sorted(records, key=lambda r: r["cost"])
    c3 = records[2]["cost"]

    for i, r in enumerate(records, start=1):
        r["rank"] = i
        if i == 1:
            r["grade"] = 100.0
        elif i in (2, 3):
            r["grade"] = 97.0
        elif c3 == 0:
            r["grade"] = 80.0  # degenerate case: can't compute a percentage premium over $0
        else:
            premium_pct = (r["cost"] - c3) / c3 * 100.0
            fraction = min(max(premium_pct / full_penalty_pct, 0.0), 1.0)
            r["grade"] = round(96.0 - fraction * (96.0 - 80.0), 2)

    return records


# ---------------------------------------------------------------------------
# Order of merit list (OML)
# ---------------------------------------------------------------------------

def build_oml(go_records: list[dict], no_go_records: list[dict]) -> list[dict]:
    """Order of merit list covering every parsed submission.

    GO submissions come first, cheapest to most expensive. NO GO
    submissions follow, also cheapest to most expensive. Positions run
    continuously from 1 through N across both groups. Ties in cost keep
    upload order (Python's sort is stable). No grades are assigned here;
    grading is handled separately by grade() and needs at least 3 GO
    submissions, while the OML does not. The Notes column carries the
    NO GO reason from classify_structural_status().
    """
    go_sorted = sorted(go_records, key=lambda r: r["cost"])
    no_go_sorted = sorted(no_go_records, key=lambda r: r["cost"])

    rows = []
    for pos, r in enumerate(go_sorted + no_go_sorted, start=1):
        rows.append(
            {
                "OML Position": pos,
                "Name": r["name"],
                "Cost": r["cost"],
                "Structural Status": r["status"],
                "Notes": r["note"],
                "Source File": r["file"],
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Results assembly (shared by the PDF mode and the saved-OML mode)
# ---------------------------------------------------------------------------

def build_oml_and_results(go_records: list[dict], no_go_records: list[dict], full_penalty_pct: float) -> None:
    """Build the OML and the graded results table and store both in
    st.session_state ("oml_df", "results_df"). Shows the fewer-than-3-GO
    error when grading is not possible. Used by both input modes so they
    always produce identical tables and CSVs.
    """
    # The OML covers every parsed submission and does not depend on
    # grading, so it is built even when fewer than 3 are GO. It is
    # built before grade() runs, from the same parsed records.
    if go_records or no_go_records:
        st.session_state["oml_df"] = pd.DataFrame(build_oml(go_records, no_go_records))
    else:
        st.session_state.pop("oml_df", None)

    if len(go_records) < 3:
        st.error(
            f"Only {len(go_records)} qualifying submission(s) found. "
            "At least 3 are required to grade (the top-3 tiers need 3 estimates)."
        )
        if go_records or no_go_records:
            st.info("The order of merit list is still produced below.")
        st.session_state.pop("results_df", None)
    else:
        graded = grade(go_records, full_penalty_pct=full_penalty_pct)

        rows = []
        for r in graded:
            rows.append(
                {
                    "Rank": r["rank"],
                    "Name": r["name"],
                    "Cost": r["cost"],
                    "Recommended Grade": r["grade"],
                    "Structural Status": "GO",
                    # Empty for PDF-parsed GO submissions; "manually
                    # entered" for rows from the manual entry table.
                    "Notes": r["note"],
                    "Source File": r["file"],
                }
            )
        for r in no_go_records:
            rows.append(
                {
                    "Rank": None,
                    "Name": r["name"],
                    "Cost": r["cost"],
                    "Recommended Grade": None,
                    "Structural Status": "NO GO",
                    "Notes": r["note"],
                    "Source File": r["file"],
                }
            )

        results_df = pd.DataFrame(rows)
        results_df["Rank"] = results_df["Rank"].astype("Int64")
        st.session_state["results_df"] = results_df


# ---------------------------------------------------------------------------
# Saved OML input (re-grade without re-uploading PDFs)
# ---------------------------------------------------------------------------
# A bridge_oml.csv from a previous session already reflects PDF parsing,
# manual entries, and duplicate removal, so it holds everything grading
# needs: name, cost, GO/NO GO status, notes, and source file. The OML
# Position column is ignored; order is recomputed from cost.

OML_REQUIRED_COLUMNS = ["Name", "Cost", "Structural Status"]


def load_oml_csv(file_bytes: bytes):
    """Read a saved OML CSV into records for grading.

    Returns (records, problems):
        records  -> list of dicts in the same shape extract_record() returns
        problems -> list of strings describing rows that were skipped

    Raises ValueError if the file is not a CSV with the required columns.
    Costs edited in Excel (e.g. "$300,000.00") are accepted. Rows with a
    missing or non-numeric cost, or a status other than GO / NO GO, are
    skipped and reported rather than guessed.
    """
    try:
        df = pd.read_csv(io.BytesIO(file_bytes), dtype=str, keep_default_na=False)
    except Exception as e:
        raise ValueError(f"could not read the file as CSV ({type(e).__name__}: {e})")

    missing = [c for c in OML_REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            "missing required column(s): " + ", ".join(missing)
            + ". Upload a bridge_oml.csv exported by this app."
        )

    records = []
    problems = []
    for i, row in df.iterrows():
        line = i + 2  # CSV line number, counting the header as line 1
        name = row["Name"].strip() or "Unknown"

        cost_text = row["Cost"].strip().replace("$", "").replace(",", "")
        try:
            cost = float(cost_text)
        except ValueError:
            problems.append(f"line {line} ({name}): cost \"{row['Cost']}\" is not a number")
            continue

        status = " ".join(row["Structural Status"].split()).upper()
        if status not in ("GO", "NO GO"):
            problems.append(
                f"line {line} ({name}): status \"{row['Structural Status']}\" is not GO or NO GO"
            )
            continue

        records.append(
            {
                "file": row.get("Source File", "").strip() or "(not recorded)",
                "name": name,
                "cost": cost,
                "status": status,
                "note": row.get("Notes", "").strip(),
            }
        )

    return records, problems


# ---------------------------------------------------------------------------
# Page setup
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Bridge Cost Grading Tool", layout="wide")

st.title("ASCE Bridge Designer App Grading Tool")

st.markdown(
    f"""
This tool checks a batch of competing bridge design submissions for
structural soundness, then grades qualifying submissions by **submitted
cost**. Cheaper is better: the least expensive qualifying design scores
highest. It also produces an **order of merit list (OML)** of all
submissions.

**How to use it**

0. Run a competition or require an assignment using the ASCE Bridge Designer, linked at bottom of this page. 

1a. Students should use File > Print to access the PDF Elevation View rendering of their bridge. Ensure that they have applied their name in the app prior to printing. 

1b. Have them submit those files to you via the most accessible medium. I prefer creating an assignment in Canvas that they submit to because it allows a mass download of all submissions.

1c. Upload all PDF plan sheets for this round **in one bulk upload, all files < 200 MB** (one PDF per submission; a submission may be more than one page). For large batches, upload the **ZIP file** from Canvas "Download Submissions" instead of the individual PDFs; it uploads as one file and avoids upload errors. Non-PDF files inside a ZIP are skipped and listed.

2. Click **Check and grade submissions**.

3. Review the results: qualifying submissions are ranked and graded;
   disqualified submissions are listed separately.

4. Download the CSV, and review the chart.

5. Review and download the OML, which ranks every submission: GO
   submissions first by cost, then NO GO submissions by cost.

6. To adjust the grading spread later, choose **Re-grade from a saved OML
   CSV** and upload the bridge_oml.csv from step 5. Grades update as soon as
   you change the grading sensitivity; no PDFs or manual entries needed.

**Structural soundness check**
Each plan sheet has a **"{STRUCTURAL_PASS_PHRASE}"**, **"{STRUCTURAL_FAIL_LOAD}"**,
or **"{STRUCTURAL_FAIL_ANALYSIS}"** status line. Only submissions with a
"{STRUCTURAL_PASS_PHRASE}" status are graded; the other two are excluded
from cost ranking, and the reason is recorded in the CSV's Notes column.
The Status line may be on any page. NO GO submissions still appear at the
bottom of the OML.

**Duplicate submissions**
If the same name appears on more than one submission, only that person's
cheapest GO submission is kept. If none of their submissions passed, their
cheapest NO GO is kept. Names are matched ignoring capitalization and extra
spaces; "Unknown" names are never merged. Removed duplicates are listed in
a warning and excluded from all results.

**Grading scale** (qualifying submissions only)
| Rank | Grade |
|---|---|
| 1st cheapest | 100 |
| 2nd & 3rd cheapest | 97 |
| 4th cheapest ... most expensive | 96 down to 80, scaled by % more expensive than the 3rd-cheapest bid (not by min-max stretch) — see "Grading sensitivity" below |
"""
)

st.divider()

with st.expander("Grading sensitivity (advanced)"):
    st.caption(
        "Controls how quickly cost above the 3rd-cheapest bid costs grade "
        "points. A bid priced at the 3rd-cheapest cost scores 96; a bid "
        "priced this percentage (or more) above it scores the floor of 80. "
        "If your batch's actual cost spread never reaches this percentage, "
        "nobody hits 80. Scores stay clustered near 96, proportional to "
        "how close costs actually are. Increase this if scores are "
        "spreading too widely for tightly clustered costs; decrease it if "
        "you want cost differences to matter more."
    )
    full_penalty_pct = st.number_input(
        "Cost premium (%) above 3rd-cheapest that earns the grade floor of 80",
        min_value=0.1,
        value=20.0,
        step=1.0,
    )

st.divider()

# ---------------------------------------------------------------------------
# Upload + processing
# ---------------------------------------------------------------------------

MODE_PDF = "Grade from PDFs"
MODE_OML = "Re-grade from a saved OML CSV"

input_mode = st.radio(
    "Input",
    [MODE_PDF, MODE_OML],
    horizontal=True,
    help=(
        "Use \"Re-grade from a saved OML CSV\" to load the bridge_oml.csv "
        "from an earlier session and adjust the grading sensitivity without "
        "re-uploading PDFs or re-entering manual rows."
    ),
)

# Switching modes discards the other mode's results so the tables below
# always match the selected input.
if st.session_state.get("input_mode") != input_mode:
    for key in ("graded_with", "results_df", "oml_df"):
        st.session_state.pop(key, None)
    st.session_state["input_mode"] = input_mode

if input_mode == MODE_PDF:
    uploaded_files = st.file_uploader(
        "Upload bridge plan PDFs, or a ZIP of PDFs",
        type=["pdf", "zip"],
        accept_multiple_files=True,
    )

    if uploaded_files:
        # Expand ZIPs into individual PDF submissions. Every later step works on
        # this list, so a PDF inside a ZIP is handled exactly like a plain upload.
        submissions, skipped_in_zip, bad_zips = list_submissions(uploaded_files)
        submission_names = [name for name, _, _ in submissions]

        st.write(
            f"{len(uploaded_files)} file(s) selected, containing "
            f"{len(submissions)} PDF submission(s)."
        )
        if bad_zips:
            st.error(
                "The following ZIP file(s) could not be opened and are excluded:\n"
                + "\n".join(f"- {name} ({reason})" for name, reason in bad_zips)
            )
        if skipped_in_zip:
            st.info(
                "The following non-PDF file(s) inside ZIP uploads were skipped:\n"
                + "\n".join(f"- {name}" for name in skipped_in_zip)
            )

        # Identifies the current upload set. If files are added or removed, or the
        # sensitivity setting changes, after grading, the stored results no longer
        # match the inputs and are discarded so stale grades are never shown.
        file_sig = tuple((f.name, f.size) for f in uploaded_files)
        graded_with = st.session_state.get("graded_with")
        if graded_with is not None and graded_with != {"sig": file_sig, "penalty": full_penalty_pct}:
            for key in ("graded_with", "results_df", "oml_df"):
                st.session_state.pop(key, None)
            st.info(
                "The uploaded files or the grading sensitivity changed. Click "
                "**Check and grade submissions** again."
            )

        # -----------------------------------------------------------------------
        # Manual entry + grade button
        # -----------------------------------------------------------------------
        # The manual entry table and the button are in one form, so editing the
        # table does not rerun the app. Entries are applied only when the button
        # is clicked. A manual row replaces everything the app would read from
        # that file (the file is not parsed). Rows with no file selected or a
        # blank cost are ignored. Status defaults to NO GO (the same safe default
        # used for an unrecognized Status line), so set GO only after confirming
        # "passes all tests" on the plan sheet.
        with st.form("grading_form"):
            st.markdown("**Manual entry (optional)**")
            st.caption(
                "Fill this in before clicking the button. Add a row for any file "
                "the app cannot read: pick the file, then enter the designer's "
                "name, cost, and structural status. A manual row replaces anything "
                "the app reads from that file. Rows with a blank cost are ignored. "
                "Manual rows are marked \"manually entered\" in the Notes column of "
                "both CSVs."
            )
            manual_df = st.data_editor(
                pd.DataFrame(
                    {
                        "Source File": pd.Series(dtype="object"),
                        "Name": pd.Series(dtype="object"),
                        "Cost": pd.Series(dtype="float64"),
                        "Structural Status": pd.Series(dtype="object"),
                        "Notes": pd.Series(dtype="object"),
                    }
                ),
                key="manual_entry_editor",
                hide_index=True,
                num_rows="dynamic",
                use_container_width=True,
                column_config={
                    "Source File": st.column_config.SelectboxColumn(
                        "Source File", options=submission_names
                    ),
                    "Name": st.column_config.TextColumn("Name"),
                    "Cost": st.column_config.NumberColumn("Cost", min_value=0.0, format="$%.2f"),
                    "Structural Status": st.column_config.SelectboxColumn(
                        "Structural Status", options=["GO", "NO GO"], default="NO GO"
                    ),
                    "Notes": st.column_config.TextColumn("Notes (optional)"),
                },
            )
            submitted = st.form_submit_button("Check and grade submissions", type="primary")

        if submitted:
            # Convert valid manual rows to records, keyed by file. If the same
            # file is entered twice, the later row wins.
            uploaded_names = set(submission_names)
            manual_by_file = {}
            for _, row in manual_df.iterrows():
                file_name = row["Source File"]
                if pd.isna(file_name) or file_name not in uploaded_names or pd.isna(row["Cost"]):
                    continue
                name = "" if pd.isna(row["Name"]) else str(row["Name"]).strip()
                user_note = "" if pd.isna(row["Notes"]) else str(row["Notes"]).strip()
                status = row["Structural Status"] if row["Structural Status"] in ("GO", "NO GO") else "NO GO"
                manual_by_file[file_name] = {
                    "file": file_name,
                    "name": name or "Unknown",
                    "cost": float(row["Cost"]),
                    "status": status,
                    "note": "manually entered" + (f"; {user_note}" if user_note else ""),
                }

            go_records = []
            no_go_records = []
            unparsed = []
            # Files pdfplumber cannot open at all (corrupt, empty, encrypted, or
            # not actually a PDF). Kept separate from "unparsed" so the grader can
            # tell a broken file apart from a valid PDF that lacks a cost line.
            unreadable = []

            parsed = []

            for sub_name, f, zip_member in submissions:
                # Files with a manual row are taken from the manual table only.
                if sub_name in manual_by_file:
                    continue
                # getvalue() returns the full contents regardless of the file's
                # read position, so a previously read file never comes back empty.
                # read_submission_bytes() uses it for plain PDFs and ZIP members.
                try:
                    file_bytes = read_submission_bytes(f, zip_member)
                except Exception as e:
                    unreadable.append((sub_name, f"{type(e).__name__}: {e}"))
                    continue
                if not file_bytes:
                    unreadable.append((sub_name, "file is empty"))
                    continue
                try:
                    rec = extract_record(sub_name, file_bytes)
                except Exception as e:
                    # One bad file should not stop the whole batch.
                    unreadable.append((sub_name, f"{type(e).__name__}: {e}"))
                    continue
                if rec is None:
                    unparsed.append(sub_name)
                else:
                    parsed.append(rec)

            parsed += list(manual_by_file.values())

            # Keep one submission per designer name before splitting GO/NO GO,
            # so duplicates never reach grading, the OML, or either CSV.
            parsed, duplicates = dedupe_by_name(parsed)

            for rec in parsed:
                if rec["status"] == "GO":
                    go_records.append(rec)
                else:
                    no_go_records.append(rec)

            if unreadable:
                st.error(
                    "The following file(s) could not be opened as PDFs and are "
                    "excluded. Re-upload a valid PDF, or add a row for each in "
                    "Manual entry above and click the button again:\n"
                    + "\n".join(f"- {name} ({reason})" for name, reason in unreadable)
                )

            if unparsed:
                st.warning(
                    "Could not find a cost on the following file(s); they are "
                    "excluded unless you add a row for each in Manual entry above "
                    "and click the button again:\n"
                    + "\n".join(f"- {name}" for name in unparsed)
                )

            if duplicates:
                # "\\$" escapes the dollar sign so Streamlit does not render the
                # text between two dollar amounts as LaTeX math.
                st.warning(
                    "Multiple submissions were found for the same name. Only the "
                    "cheapest GO submission per name is kept (or the cheapest NO GO "
                    "if none passed). The following were removed:\n"
                    + "\n".join(
                        f"- {d['file']} ({d['name']}, \\${d['cost']:,.2f}, {d['status']}): "
                        f"kept {k['file']} (\\${k['cost']:,.2f}, {k['status']})"
                        for d, k in duplicates
                    )
                )

            if no_go_records:
                st.error(
                    "The following submission(s) failed the structural soundness "
                    "check and are excluded from cost ranking:\n"
                    + "\n".join(f"- {r['name']} ({r['file']}): {r['note']}" for r in no_go_records)
                )

            # Record what these results were computed from, for the stale check.
            st.session_state["graded_with"] = {"sig": file_sig, "penalty": full_penalty_pct}

            build_oml_and_results(go_records, no_go_records, full_penalty_pct)
    else:
        # Uploader cleared: drop results from the previous batch.
        for key in ("graded_with", "results_df", "oml_df"):
            st.session_state.pop(key, None)

else:
    # -----------------------------------------------------------------------
    # Saved OML mode
    # -----------------------------------------------------------------------
    # No PDF parsing or button: grading is cheap, so it reruns on every
    # change, and editing the sensitivity setting above updates the grades
    # immediately.
    oml_file = st.file_uploader("Upload a saved bridge_oml.csv", type="csv")

    if oml_file is None:
        for key in ("graded_with", "results_df", "oml_df"):
            st.session_state.pop(key, None)
    else:
        try:
            oml_records, oml_problems = load_oml_csv(oml_file.getvalue())
        except ValueError as e:
            oml_records, oml_problems = None, []
            st.error(f"{oml_file.name}: {e}")
            for key in ("graded_with", "results_df", "oml_df"):
                st.session_state.pop(key, None)

        if oml_records is not None:
            if oml_problems:
                st.warning(
                    "The following row(s) were skipped. Fix them in the CSV "
                    "and upload it again:\n"
                    + "\n".join(f"- {p}" for p in oml_problems)
                )

            # A saved OML is already one row per designer. Deduplicating
            # again is a safeguard in case rows were added by hand.
            oml_records, oml_duplicates = dedupe_by_name(oml_records)
            if oml_duplicates:
                # "\\$" escapes the dollar sign so Streamlit does not render
                # the text between two dollar amounts as LaTeX math.
                st.warning(
                    "The CSV has more than one row for the same name. Only the "
                    "cheapest GO row per name is kept (or the cheapest NO GO if "
                    "none passed). Removed:\n"
                    + "\n".join(
                        f"- {d['name']} (\\${d['cost']:,.2f}, {d['status']}): "
                        f"kept \\${k['cost']:,.2f}, {k['status']}"
                        for d, k in oml_duplicates
                    )
                )

            go_records = [r for r in oml_records if r["status"] == "GO"]
            no_go_records = [r for r in oml_records if r["status"] != "GO"]
            st.write(
                f"Loaded {len(oml_records)} submission(s) from {oml_file.name}: "
                f"{len(go_records)} GO, {len(no_go_records)} NO GO. "
                f"Grading with a {full_penalty_pct:g}% sensitivity."
            )

            build_oml_and_results(go_records, no_go_records, full_penalty_pct)

# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

if "results_df" in st.session_state:
    df = st.session_state["results_df"]
    graded_df = df[df["Structural Status"] == "GO"].copy()

    st.subheader("Results")
    st.dataframe(
        df.style.format({"Cost": "${:,.2f}", "Recommended Grade": "{:.2f}", "Rank": "{:.0f}"}, na_rep="-"),
        use_container_width=True,
        hide_index=True,
    )

    csv_bytes = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download CSV",
        data=csv_bytes,
        file_name="bridge_grades.csv",
        mime="text/csv",
    )

    if not graded_df.empty:
        st.subheader("Cost vs. Grade (qualifying submissions)")
        st.caption(
            "Grading is a "
            "continuous function of cost (not of rank), so a scatter plot "
            "makes the linear interpolation between rank 3 and the most "
            "expensive bid visible directly, and makes it easy to spot "
            "clusters or outliers in submitted costs. Point color reflects "
            "grade. Disqualified (NO GO) submissions are not plotted."
        )

        chart = (
            alt.Chart(graded_df)
            .mark_circle(size=110)
            .encode(
                x=alt.X("Cost:Q", title="Submitted Cost ($)", axis=alt.Axis(format="$,.0f")),
                y=alt.Y(
                    "Recommended Grade:Q",
                    title="Recommended Grade",
                    scale=alt.Scale(domain=[78, 101]),
                ),
                color=alt.Color(
                    "Recommended Grade:Q",
                    scale=alt.Scale(scheme="redyellowgreen", domain=[80, 100]),
                    legend=alt.Legend(title="Grade"),
                ),
                tooltip=["Rank", "Name", alt.Tooltip("Cost:Q", format="$,.2f"), "Recommended Grade"],
            )
            .properties(height=420)
            .interactive()
        )
        st.altair_chart(chart, use_container_width=True)

        try:
            grade_chart_png = chart.to_image(format="png", scale=2)
            st.download_button(
                "Download this chart as PNG",
                data=grade_chart_png,
                file_name="cost_vs_grade.png",
                mime="image/png",
                key="download_grade_chart_png",
            )
        except Exception as e:
            st.caption(
                f"PNG export unavailable ({e}). Make sure vl-convert-python "
                "is installed (see requirements.txt)."
            )

        st.subheader("Cost vs. Rank (qualifying submissions)")
        st.caption(
            "Rank 1 (cheapest) is plotted at the top. This view makes it "
            "easy to see how tightly or loosely spaced the actual costs "
            "are between consecutive ranks — e.g. whether rank 1 and rank "
            "2 are nearly tied in cost or far apart."
        )

        rank_chart = (
            alt.Chart(graded_df)
            .mark_circle(size=110)
            .encode(
                x=alt.X("Cost:Q", title="Submitted Cost ($)", axis=alt.Axis(format="$,.0f")),
                y=alt.Y(
                    "Rank:O",
                    title="Rank",
                    sort=alt.SortField(field="Rank", order="ascending"),
                ),
                color=alt.Color(
                    "Recommended Grade:Q",
                    scale=alt.Scale(scheme="redyellowgreen", domain=[80, 100]),
                    legend=alt.Legend(title="Grade"),
                ),
                tooltip=["Rank", "Name", alt.Tooltip("Cost:Q", format="$,.2f"), "Recommended Grade"],
            )
            .properties(height=420)
            .interactive()
        )
        st.altair_chart(rank_chart, use_container_width=True)

        try:
            rank_chart_png = rank_chart.to_image(format="png", scale=2)
            st.download_button(
                "Download this chart as PNG",
                data=rank_chart_png,
                file_name="cost_vs_rank.png",
                mime="image/png",
                key="download_rank_chart_png",
            )
        except Exception as e:
            st.caption(
                f"PNG export unavailable ({e}). Make sure vl-convert-python "
                "is installed (see requirements.txt)."
            )

# ---------------------------------------------------------------------------
# Order of merit list (shown independently of grading)
# ---------------------------------------------------------------------------

if "oml_df" in st.session_state:
    oml_df = st.session_state["oml_df"]

    st.subheader("Order of Merit List (all submissions)")
    st.caption(
        "GO submissions are listed first, cheapest to most expensive. NO GO "
        "submissions follow, also cheapest to most expensive. Positions are "
        "continuous across both groups. Unparsed files are not included."
    )
    st.dataframe(
        oml_df.style.format({"Cost": "${:,.2f}"}),
        use_container_width=True,
        hide_index=True,
    )

    oml_csv_bytes = oml_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download OML CSV",
        data=oml_csv_bytes,
        file_name="bridge_oml.csv",
        mime="text/csv",
        key="download_oml_csv",
    )

st.divider()

# ---------------------------------------------------------------------------
# Placeholders for acknowledgments / further reading
# ---------------------------------------------------------------------------

with st.expander("Acknowledgments"):
    st.markdown(
        """
        Thanks to the development team of the ASCE Bridge Designer for adding a feature to enable this app!
        """
    )

with st.expander("Further Reading"):
    st.markdown(
        """
        - [ASCE Bridge Designer](https://www.asce.org/career-growth/pre-college-outreach/bridge-designer)


        """
    )