# Bridge Cost Grading Tool

A Streamlit app that checks a batch of competing bridge design submissions
for structural soundness, then grades qualifying submissions by submitted
cost. Upload the PDF plan sheets for a round; the app extracts each
design's cost and designer name, disqualifies any submission that fails
the soundness check, ranks the rest, assigns a recommended grade, and lets
you export the results as a CSV alongside a chart.

> **Note:** the grading formula below is written in LaTeX. It renders on
> GitHub, GitLab, and most Markdown previewers with math support (e.g.
> VS Code's built-in preview); it will show as raw `$...$` text in plain
> text editors or viewers without a math renderer.

## What it does

1. Upload any number of PDF plan sheets (one per submission).
2. For each PDF, the app reads the first page and pulls:
   - **Cost** — from a line like `Cost: $401,521.91 Date: 22 July 2026 Iteration: 6`
   - **Designer name** — from a line like `Designed by: John Smith`
3. Each PDF is checked for a line containing the phrase **"passes all
   tests"** (case-insensitive, anywhere on the line). Submissions without
   that line are marked **NO GO** and excluded from ranking, regardless of
   cost.
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

- Each field sits on its own `Label: value` line.
- The cost is always the token between `$` and the following `Date:` on
  that line.
- The structural check looks for a line containing "passes all tests"
  anywhere in the extracted text (case-insensitive).

If a PDF doesn't match this template (missing "Cost:" line, or a cost that
doesn't parse as a number), the app excludes it entirely and lists it by
filename as unparsed, rather than silently dropping or guessing it.

## Requirements

- At least 3 successfully parsed submissions (the top-3 tiers require 3
  estimates to define). Below that, the app reports the shortfall and does
  not produce a graded table.

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

Thanks to the development team for the ASCE Bridge Designer for helping us incorporate this grading program into the structure of the Plan Outputs from the designer. Also for adding a Structural Soundness check to the PDF upon our request!

## Further Reading

[ASCE Bridge Designer](https://www.asce.org/career-growth/pre-college-outreach/bridge-designer)