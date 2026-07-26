"""
Bridge Cost Grading Tool
------------------------
Streamlit app: upload a batch of bridge plan PDFs, confirm structural
soundness via a text check, extract the designer's name and submitted
cost, rank and grade qualifying submissions, and export results.

Run locally with:
    streamlit run streamlit_app.py
"""

import io

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
    """Read the first page of a PDF and pull the designer name, cost, and
    structural soundness status.

    Returns a dict, or None if no cost could be found (file is reported to
    the user as unparsed rather than silently skipped).
    """
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        text = pdf.pages[0].extract_text() or ""

    cost_line = get_line_value(text, "Cost")
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
# Page setup
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Bridge Cost Grading Tool", layout="wide")

st.title("ASCE Bridge Designer App Grading Tool")

st.markdown(
    f"""
This tool checks a batch of competing bridge design submissions for
structural soundness, then grades qualifying submissions by **submitted
cost**. Cheaper is better: the least expensive qualifying design scores
highest.

**How to use it**
0. Run a competition or require an assignment using the ASCE Bridge Designer, linked at bottom of this page. 

1a. Students should use File > Print to access the PDF Elevation View rendering of their bridge. Ensure that they have applied their name in the app prior to printing. 

1b. Have them submit those files to you via the most accessible medium. I prefer creating an assignment in Canvas that they submit to because it allows a mass download of all submissions.

1c. Upload all PDF plan sheets for this round **in one bulk upload, all files < 200 MB**(one PDF per submission). 

2. Click **Check and grade submissions**.

3. Review the results: qualifying submissions are ranked and graded;
   disqualified submissions are listed separately.

4. Download the CSV, and review the chart.

**Structural soundness check**
Each plan sheet has a **"{STRUCTURAL_PASS_PHRASE}"**, **"{STRUCTURAL_FAIL_LOAD}"**,
or **"{STRUCTURAL_FAIL_ANALYSIS}"** status line. Only submissions with a
"{STRUCTURAL_PASS_PHRASE}" status are graded; the other two are excluded
from cost ranking, and the reason is recorded in the CSV's Notes column.

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

uploaded_files = st.file_uploader(
    "Upload bridge plan PDFs",
    type="pdf",
    accept_multiple_files=True,
)

if uploaded_files:
    st.write(f"{len(uploaded_files)} file(s) selected.")

    if st.button("Check and grade submissions", type="primary"):
        go_records = []
        no_go_records = []
        unparsed = []

        for f in uploaded_files:
            rec = extract_record(f.name, f.read())
            if rec is None:
                unparsed.append(f.name)
            elif rec["status"] == "GO":
                go_records.append(rec)
            else:
                no_go_records.append(rec)

        if unparsed:
            st.warning(
                "Could not find a cost on the following file(s); they are "
                "excluded entirely:\n" + "\n".join(f"- {name}" for name in unparsed)
            )

        if no_go_records:
            st.error(
                "The following submission(s) failed the structural soundness "
                "check and are excluded from cost ranking:\n"
                + "\n".join(f"- {r['name']} ({r['file']}): {r['note']}" for r in no_go_records)
            )

        if len(go_records) < 3:
            st.error(
                f"Only {len(go_records)} qualifying submission(s) found. "
                "At least 3 are required to grade (the top-3 tiers need 3 estimates)."
            )
            st.session_state.pop("results_df", None)
        else:
            graded = grade(go_records, full_penalty_pct=full_penalty_pct)

            rows = []
            for r in graded:
                rows.append(
                    {
                        "Rank": int(r["rank"]),
                        "Name": r["name"],
                        "Cost": r["cost"],
                        "Recommended Grade": r["grade"],
                        "Structural Status": "GO",
                        "Notes": "",
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

            st.session_state["results_df"] = pd.DataFrame(rows)

# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

if "results_df" in st.session_state:
    df = st.session_state["results_df"]
    graded_df = df[df["Structural Status"] == "GO"].copy()

    st.subheader("Results")
    st.dataframe(
        df.style.format({"Cost": "${:,.2f}", "Recommended Grade": "{:.2f}"}, na_rep="-"),
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