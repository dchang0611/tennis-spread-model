# Baseline comparison

Run: `f7ce287753990c5286cee4901c9d8831dcd4baea8ba1839c8b78280e8619f52c`

Retrospective development research. No untouched holdout; displayed prices are not fills.

| Data delay | Format | Candidate | Evaluated matches | Brier | Market Brier | Picks W-L | Gross units | Unresolved picks |
|---|---|---|---:|---:|---:|---|---:|---:|
| 1 day(s) | BO3 | elo | 108 | 0.2641 | 0.2524 | 43-49 | -3.08 | 2 |
| 1 day(s) | BO3 | elo_serve_return | 108 | 0.2660 | 0.2524 | 35-49 | -8.90 | 2 |
| 1 day(s) | BO3 | elo_serve_return_margin | 108 | 0.2635 | 0.2524 | 39-50 | -7.31 | 2 |
| 1 day(s) | BO5 | elo | 64 | 0.2865 | 0.2481 | 17-34 | -10.85 | 1 |
| 1 day(s) | BO5 | elo_serve_return | 64 | 0.2911 | 0.2481 | 21-33 | -9.16 | 1 |
| 1 day(s) | BO5 | elo_serve_return_margin | 64 | 0.2891 | 0.2481 | 22-32 | -5.28 | 1 |
| 2 day(s) | BO3 | elo | 108 | 0.2639 | 0.2524 | 39-50 | -6.80 | 2 |
| 2 day(s) | BO3 | elo_serve_return | 108 | 0.2661 | 0.2524 | 38-50 | -7.04 | 2 |
| 2 day(s) | BO3 | elo_serve_return_margin | 108 | 0.2635 | 0.2524 | 40-50 | -5.24 | 2 |
| 2 day(s) | BO5 | elo | 64 | 0.2882 | 0.2481 | 17-34 | -11.52 | 1 |
| 2 day(s) | BO5 | elo_serve_return | 64 | 0.2933 | 0.2481 | 23-33 | -7.25 | 1 |
| 2 day(s) | BO5 | elo_serve_return_margin | 64 | 0.2926 | 0.2481 | 22-33 | -8.19 | 1 |

Probability metrics use one predetermined line per match. Selection results use the first qualifying capture per candidate; those are different samples.

All comparisons use the same production features, quality gates, fit, calibration and scoring functions. Original-protocol artifacts remain a separate historical audit.

The simple Elo candidate is frozen for prospective paper collection, chosen for simplicity before inspecting this comparison. No automatic promotion or retrospective winner selection.

See summary.json for paired uncertainty, added-factor comparisons, calibration bins, weekly ROI intervals, unresolved-outcome bounds and cost sensitivity.