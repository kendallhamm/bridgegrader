"""
Bridge Cost Grading Tool
------------------------
Streamlit app: upload a batch of bridge plan PDFs, confirm structural
soundness via a text check, extract the designer's name and submitted
cost, rank and grade qualifying submissions, and export results.
Also produces an order of merit list (OML) covering every parsed
submission: GO submissions first by cost, then NO GO submissions by cost.

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
#   - A submission may run onto more than one page. Text from all pages is
#     joined in page order before searching, so the first matching line
#     (earliest page) wins when a label appears more than once.

STRUCTURAL_PASS_PHRASE = "passes all tests"


def get_line_value(text: str, label: str) -> str:
    """Return the text after the first ':' on the line that starts with label."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(label):
            return stripped.split(":", 1)[1].strip()
    return ""


def has_line_containing(text: str, phrase: str) -> bool:
    """True if any line contains phrase, case-insensitive."""
    phrase = phrase.lower()
    return any(phrase in line.lower() for line in text.splitlines())


def extract_record(file_name: str, file_bytes: bytes):
    """Read every page of a PDF and pull the designer name, cost, and
    structural soundness status.

    All pages are joined in order, so a single submission that spans two
    or more pages is read as one plan sheet.

    Returns a dict, or None if no cost could be found (file is reported to
    the user as unparsed rather than silently skipped).
    """
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        # Join all pages so fields or the pass phrase on page 2+ are found.
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

    structurally_sound = has_line_containing(text, STRUCTURAL_PASS_PHRASE)
    status = "GO" if structurally_sound else "NO GO"

    return {"file": file_name, "name": name, "cost": cost, "status": status}


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------

def grade(records: list[dict]) -> list[dict]:
    """Sort ascending by cost and assign grades to GO (qualifying) submissions.

    Rank 1 (cheapest):  100
    Ranks 2-3:            97
    Ranks 4-N: linear interpolation BY COST VALUE, anchored between
               rank-3's cost (96) and the most expensive cost (80):

        grade(c) = 96 - (c - c3) / (c_max - c3) * (96 - 80)
    """
    if len(records) < 3:
        raise ValueError("Need at least 3 qualifying submissions to grade (top-3 tiers require 3).")

    records = sorted(records, key=lambda r: r["cost"])
    c3 = records[2]["cost"]
    c_max = records[-1]["cost"]
    span = c_max - c3

    for i, r in enumerate(records, start=1):
        r["rank"] = i
        if i == 1:
            r["grade"] = 100.0
        elif i in (2, 3):
            r["grade"] = 97.0
        elif span == 0:
            r["grade"] = 96.0  # all remaining costs identical
        else:
            r["grade"] = round(96.0 - (r["cost"] - c3) / span * (96.0 - 80.0), 2)

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
    submissions, while the OML does not.
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
                "Source File": r["file"],
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Page setup
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Bridge Cost Grading Tool", layout="wide")

st.title("Bridge Cost Grading Tool")

st.markdown(
    f"""
This tool checks a batch of competing bridge design submissions for
structural soundness, then grades qualifying submissions by **submitted
cost**. Cheaper is better: the least expensive qualifying design scores
highest. It also produces an **order of merit list (OML)** of all
submissions.

**How to use it**
1. Upload all PDF plan sheets for this round (one PDF per submission;
   a submission may be more than one page).
2. Click **Check and grade submissions**.
3. Review the results: qualifying submissions are ranked and graded;
   disqualified submissions are listed separately.
4. Download the CSV, and review the chart.
5. Review and download the OML, which ranks every submission: GO
   submissions first by cost, then NO GO submissions by cost.

**Structural soundness check**
A submission is considered structurally sound if its plan sheet contains
a line with the phrase **"{STRUCTURAL_PASS_PHRASE}"** (case-insensitive)
on any page. A submission missing that line is disqualified (**NO GO**)
and excluded from cost ranking and grading, regardless of its price. It
still appears at the bottom of the OML.

**Grading scale** (qualifying submissions only)
| Rank | Grade |
|---|---|
| 1st cheapest | 100 |
| 2nd & 3rd cheapest | 97 |
| 4th cheapest ... most expensive | scaled 96 down to 80, by cost value (not by rank) |
"""
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
                f'check (no "{STRUCTURAL_PASS_PHRASE}" line found) and are '
                "excluded from cost ranking:\n"
                + "\n".join(f"- {r['name']} ({r['file']})" for r in no_go_records)
            )

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
            graded = grade(go_records)

            rows = []
            for r in graded:
                rows.append(
                    {
                        "Rank": r["rank"],
                        "Name": r["name"],
                        "Cost": r["cost"],
                        "Recommended Grade": r["grade"],
                        "Structural Status": "GO",
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
            "Scatter plot recommended over a bar chart here: grading is a "
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
    )

st.divider()

# ---------------------------------------------------------------------------
# Placeholders for acknowledgments / further reading
# ---------------------------------------------------------------------------

with st.expander("Acknowledgments"):
    st.markdown(
        """
        *Placeholder — add acknowledgments here.*

        - [Contributor or organization name](https://example.com)
        """
    )

with st.expander("Further Reading"):
    st.markdown(
        """
        *Placeholder — add reference links here.*

        - [Reference title](https://example.com)
        - [Reference title](https://example.com)
        """
    )