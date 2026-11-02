# Bridge Cost Grading Tool

A Streamlit app that checks a batch of competing bridge design submissions
for structural soundness, then grades qualifying submissions by submitted
cost. Upload the PDF plan sheets for a round; the app extracts each
design's cost and designer name, disqualifies any submission that fails
the soundness check, ranks the rest, assigns a recommended grade, and lets
you export the results as a CSV alongside a chart. It also produces a
separate order of merit list (OML) of every parsed submission, with its
own CSV export.

> **Note:** the grading formula below is written in LaTeX. It renders on
> GitHub, GitLab, and most Markdown previewers with math support (e.g.
> VS Code's built-in preview); it will show as raw `$...$` text in plain
> text editors or viewers without a math renderer.

## What it does

1. Upload any number of PDF plan sheets (one PDF per submission). A
   submission may be more than one page.
2. For each PDF, the app reads all pages (joined in page order) and pulls:
   - **Cost** — from a line like `Cost: $401,521.91 Date: 22 July 2026 Iteration: 6`
   - **Designer name** — from a line like `Designed by: John Smith`
3. Each PDF is checked for a line containing the phrase **"passes all
   tests"** (case-insensitive, anywhere on the line, on any page).
   Submissions without that line are marked **NO GO** and excluded from
   ranking and grading, regardless of cost.
4. Qualifying (**GO**) submissions are ranked cheapest to most expensive
   and graded:

   | Rank | Grade |
   |---|---|
   | 1st cheapest | 100 |
   | 2nd and 3rd cheapest | 97 |
   | 4th cheapest through most expensive | scaled 96 down to 80, by cost value (linear interpolation, not by rank) |

   Formally, let $c_1 < c_2 < \dots < c_n$ be the sorted costs of the $n$
   qualifying submissions, with $c_3$ the 3rd-cheapest cost and $c_n$ the
   most expensive. The grade $G(c_i)$ assigned to the submission with cost
   $c_i$ is:

   $$
   G(c_i) =
   \begin{cases}
   100 & i = 1 \\[4pt]
   97 & i \in \{2, 3\} \\[4pt]
   96 - \dfrac{c_i - c_3}{c_n - c_3} \left(96 - 80\right) & i > 3
   \end{cases}
   $$

   Plain text equivalent:

   ```
   grade(c_i) = 100                                         if i = 1
   grade(c_i) = 97                                          if i = 2 or i = 3
   grade(c_i) = 96 - (c_i - c_3) / (c_n - c_3) * (96 - 80)   if i > 3
   ```

   where c_1 < c_2 < ... < c_n are the sorted costs of the n qualifying
   submissions, c_3 is the 3rd-cheapest cost, and c_n is the most
   expensive cost.

   For $i > 3$, the grade is a linear interpolation **by cost value**
   (not by rank position): it equals 96 when $c_i = c_3$, decreases
   linearly to 80 when $c_i = c_n$, and is unaffected by how many
   submissions fall between them. If $c_n = c_3$ (all remaining costs are
   identical), every submission with $i > 3$ receives a grade of 96.

5. Results are shown in one combined table (qualifying submissions ranked
   and graded, disqualified submissions listed with no rank/grade),
   exportable as CSV, plus a cost-vs-grade scatter chart of qualifying
   submissions only.
6. An order of merit list is shown in a separate table and exported as
   `bridge_oml.csv` (see below).

## Order of merit list (OML)

The OML ranks every successfully parsed submission:

- **GO** submissions first, cheapest to most expensive.
- **NO GO** submissions after all GO submissions, also cheapest to most
  expensive.
- Positions run continuously from 1 through N across both groups.
- Ties in cost keep upload order.
- No grades are shown on the OML.
- Unparsed files (no readable cost) are not included.

Columns: `OML Position`, `Name`, `Cost`, `Structural Status`,
`Source File`.

The OML does not depend on grading, so it is produced even when fewer
than 3 submissions are GO.

## Structural soundness check

Every submission is assumed to have already been checked for structural
soundness elsewhere; this app just looks for that result recorded on the
plan sheet as a line containing "passes all tests". This is a simple text
match, not an independent engineering analysis — if the phrase or
wording convention changes, update `STRUCTURAL_PASS_PHRASE` near the top
of `streamlit_app.py`.

## Extraction method

Field extraction uses plain string splitting, not regex, since the plan
sheets follow a fixed template:

- Text from all pages of the PDF is joined in page order before
  searching, so a submission that spans two or more pages is read as one
  plan sheet. If a label appears more than once, the first occurrence
  (earliest page) is used.
- Each field sits on its own `Label: value` line.
- The cost line is matched by the label `Cost:` (with the colon), so a
  heading such as "Cost Summary" is not mistaken for it.
- The cost is always the token between `$` and the following `Date:` on
  that line.
- The structural check looks for a line containing "passes all tests"
  anywhere in the extracted text (case-insensitive).

If a PDF doesn't match this template (missing "Cost:" line, or a cost that
doesn't parse as a number), the app excludes it entirely and lists it by
filename as unparsed, rather than silently dropping or guessing it.

Each PDF is treated as one submission. If one design were split across
two separate PDF files, the app would treat them as two submissions.

## Requirements

- At least 3 GO submissions to produce the graded table (the top-3 tiers
  require 3 estimates to define). Below that, the app reports the
  shortfall and does not produce a graded table. The OML is still
  produced.

## Running locally

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## Deploying to Streamlit Community Cloud

Push this repo (including `streamlit_app.py`, `requirements.txt`, and this
README) to GitHub, then create a new app on
[share.streamlit.io](https://share.streamlit.io) pointing at
`streamlit_app.py`. No additional configuration is required for typical
batches of PDF plan sheets (well under the default 200MB per-file upload
limit).

## Acknowledgments

*Placeholder — to be filled in later.*

## Further Reading

*Placeholder — to be filled in later.*