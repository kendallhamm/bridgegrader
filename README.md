# Bridge Cost Grading Tool

A Streamlit app that checks a batch of competing bridge design submissions
for structural soundness, then grades qualifying submissions by submitted
cost. Upload the PDF plan sheets for a round; the app extracts each
design's cost and designer name, disqualifies any submission that fails
the soundness check, ranks the rest, assigns a recommended grade, and lets
you export the results as a CSV alongside two charts, each downloadable
as PNG.

> **Note:** the grading formula below is written in LaTeX. It renders on
> GitHub, GitLab, and most Markdown previewers with math support (e.g.
> VS Code's built-in preview); it will show as raw `$...$` text in plain
> text editors or viewers without a math renderer.

## What it does

1. Upload any number of PDF plan sheets (one per submission).
2. For each PDF, the app reads the first page and pulls:
   - **Cost** — from a line like `Cost: $401,521.91 Date: 22 July 2026 Iteration: 6`
   - **Designer name** — from a line like `Designed by: John Smith`
3. Each PDF has a **"Status:"** line on the plan sheet. Based on its
   content:
   - **"passes all tests"** → qualifying (**GO**), included in ranking, no note.
   - **"fails load test"** → disqualified (**NO GO**), excluded from ranking, Notes column records `fails load test`.
   - **"no valid analysis"** → disqualified (**NO GO**), excluded from ranking, Notes column records `no valid analysis`.
   - Anything else (or a missing Status line) → treated as disqualified (**NO GO**) as a safe default, with the raw status text (or "no status line found") recorded in Notes.
4. Qualifying (**GO**) submissions are ranked cheapest to most expensive
   and graded:

   | Rank | Grade |
   |---|---|
   | 1st cheapest | 100 |
   | 2nd and 3rd cheapest | 97 |
   | 4th cheapest through most expensive | 96 down to 80, scaled by % more expensive than the 3rd-cheapest bid |

   For the 4th-cheapest submission and beyond, the grade is based on how
   much more expensive the bid is than the 3rd-cheapest bid, **as a
   percentage** — not on where it happens to fall in this particular
   batch's min-max range. A configurable "grading sensitivity" setting
   (in the app) controls how large that percentage premium needs to be
   before a bid hits the floor of 80. This matters because a batch of,
   say, 50 submissions might have costs tightly clustered within 1-2% of
   each other, or spread across a much wider range — a plain min-max
   stretch would always force someone down to 80 and someone up to 96
   regardless of which situation you're in, which can penalize a tightly
   clustered batch much more harshly than the actual cost differences
   justify. Percentage-based grading means a batch with little real cost
   spread stays clustered near 96, while a batch with wide spread still
   uses the full 80-96 range.

   $$
   G(c_i) =
   \begin{cases}
   100 & i = 1 \\[4pt]
   97 & i \in \{2, 3\} \\[4pt]
   96 - \min\!\left(\dfrac{p_i}{P},\ 1\right) \left(96 - 80\right) & i > 3
   \end{cases}
   \qquad \text{where } p_i = \dfrac{c_i - c_3}{c_3} \times 100
   $$

   Plain text equivalent:

   ```
   grade(c_i) = 100                                          if i = 1
   grade(c_i) = 97                                           if i = 2 or i = 3
   grade(c_i) = 96 - min(p_i / P, 1) * (96 - 80)             if i > 3

   where:
     p_i = (c_i - c_3) / c_3 * 100    (% premium over the 3rd-cheapest cost)
     P   = the "grading sensitivity" setting (default 20): the premium
           percentage at which a bid hits the floor grade of 80
   ```

   where c_1 < c_2 < ... < c_n are the sorted costs of the n qualifying
   submissions and c_3 is the 3rd-cheapest cost. A bid priced exactly at
   c_3 scores 96; a bid priced P percent or more above c_3 scores 80;
   bids in between scale linearly.

5. Results are shown in one combined table (qualifying submissions ranked
   and graded, disqualified submissions listed with no rank/grade but a
   Notes explanation), exportable as CSV, plus two charts of qualifying
   submissions: cost vs. grade, and cost vs. rank. Both charts can also be
   downloaded as PNG files.

## Structural soundness check

Each plan sheet carries a "Status:" line recording the result of a
structural analysis performed elsewhere. This app reads that line and
classifies it into three outcomes:

| Status line contains | Result | Notes column |
|---|---|---|
| "passes all tests" | GO — graded and ranked | (empty) |
| "fails load test" | NO GO — excluded from ranking | `fails load test` |
| "no valid analysis" | NO GO — excluded from ranking | `no valid analysis` |
| anything else / missing | NO GO (safe default) — excluded from ranking | the raw status text, or `no status line found` |

This is a simple text match, not an independent engineering analysis. If
the wording of any status changes, update the phrase constants
(`STRUCTURAL_PASS_PHRASE`, `STRUCTURAL_FAIL_LOAD`,
`STRUCTURAL_FAIL_ANALYSIS`) near the top of `streamlit_app.py`.

## Extraction method

Field extraction uses plain string splitting, not regex, since the plan
sheets follow a fixed template:

- Each field sits on its own `Label: value` line.
- The cost is always the token between `$` and the following `Date:` on
  that line.
- The structural status comes from the "Status:" line, matched
  case-insensitively against the three known phrases above.

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