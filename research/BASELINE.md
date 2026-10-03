# Auditable baseline and fixed factor comparison

The production release is `3.0.0-elo-paper`. The simplest candidate (overall and
surface Elo, plus surface/event-level controls) is frozen for prospective paper
collection, chosen for simplicity before inspecting the new historical results.
It is not a backtest winner promoted to live betting. Models are refitted daily
using fresh eligible historical results. No fixed end date exists in production.

The fixed nested candidates are Elo, Elo plus opponent-adjusted serve/return,
and those factors plus recent surface game margin. All share the same eligibility
universe, fitting parameters, same-format residual calibration and selection
thresholds. Separate BO3/BO5 fits close independently when evidence is inadequate.
Market no-vig probabilities at the exact archived paired line are the benchmark.

`player_features.py` is the single current feature engine. Both production and
research call its training/state builder, live feature function and common
eligibility gate. `tennis_spread_model.py` supplies both fitting and scoring.
The old `research/original_*` code is a frozen audit of the old formulas, not a
second active implementation of the current model.

Production reuses recovered historical dates only when fresh records agree on
event, match, player names, score, surface and format. It keeps freshly downloaded
statistics, checks current source freshness, and records the date-receipt hash.
Historical date corrections do not become a fallback for a failed source download.

Reproduce from the repository root:

```
python -m unittest discover -v
python research/evaluate_baseline.py
python research/verify_baseline.py
python build_spread_site.py
```

The evaluator writes its fixed protocol and input/code hashes before evaluation.
Full receipts, exclusions, scored quotes and selected picks live in
`data/baseline_evaluation/<run_id>/`. The dashboard reads the compact
`data/baseline_comparison.json`. Input receipts preserve exact source bytes;
source hashes identify the local bytes used (line-ending changes can change a
run identity). Dependencies are pinned to the tested versions.

## What the evidence does and does not establish

- Exact quote capture and archival timing must precede scheduled start. The
  archival decision must occur within the same 30-minute quote-age rule as live.
- Each prediction uses only matches before its UTC cutoff. An additional-day
  delay is tested separately. Same-day outcomes cannot update that day's state.
- Historical results are corrected source data; original publication vintages
  are unavailable. This cannot prove the exact information available in real time.
- The dated training source contains 7,226 recovered matches. Another 888 rows
  lack uniquely resolved match dates and are excluded in the preserved input
  receipt. Complete fields and recent point-stat coverage do **not** certify
  that every historical match was recovered. This remains retrospective research.
- Predictor eligibility never depends on the subsequent win/loss. Unverified
  settlement stays unresolved, with counts and worst/best settlement bounds.
- Probability comparisons choose one paired line per match using capture time
  and market balance, not model predictions or outcomes. Other quotes remain
  inspectable but do not inflate the independent sample.
- The strategy uses the production thresholds and first qualifying capture,
  one selection per match per candidate. Its line can differ from the primary
  probability sample. Do not equate those samples.
- Probability intervals resample matches; ROI intervals resample capture weeks.
  They are descriptive and do not remove player/tournament dependence, multiple
  comparisons or the bias from earlier research on this period.
- Gross returns use displayed prices without verified fills. A 1% stake-cost
  scenario is a sensitivity calculation, not a claim about actual exchange fees.
- The whole historical period is development data. No untouched holdout is
  claimed. All prospective results are separated by model version and format.

The website retains its original branding, stylesheet, cards, date controls and
tab layout. A dataset selector keeps current versioned paper history separate
from the fixed historical baseline replay; the format selector applies to the
board, history and factor views. Confluence uses numeric feature differences,
not regex matches against top-three rationale text. Its groups overlap and are
descriptive. Legacy ledgers and comparison receipts remain preserved. Nothing
automatically enables live bets.
