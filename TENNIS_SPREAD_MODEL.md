# Novig tennis game-spread model

This spread-first model replaces the old pick'em restriction with a direct
estimate of the probability that each player covers each offered game line.
The original feature builder remains intact and supplies pre-match player
snapshots.

## What decides a play

The model predicts the final game differential with a compact Elastic Net
using one or two representatives from each feature family. Rolling,
chronological out-of-sample residuals turn the margin estimate into a cover
probability for every Novig line.

A line is marked `BET` only when all default gates pass:

- probability edge versus the paired no-vig market is at least 4 percentage points;
- expected ROI at the displayed American odds is at least 5%; and
- the conservative cover probability remains above the raw break-even probability.

Everything else is a `PASS`. These thresholds are intentionally conservative
and should not be tuned on the final evaluation period.

When several alternate lines from the same match qualify, only the line with
the highest uncertainty-adjusted expected ROI is marked `BET`. This prevents
the output from recommending several strongly correlated positions on one
match.

## Core feature families

- overall and surface Elo;
- surface-specific recent game margin;
- opponent-adjusted serve and return quality;
- hold and break proxies;
- serve-versus-return matchup interaction;
- recent workload and days of rest;
- surface, tournament level, and match format.

The compact list prevents several transformations of the same tennis concept
from receiving several independent votes.

## Novig input

Copy `novig_spreads_template.csv` and enter one row for every paired spread.
Alternate spreads for the same match are separate rows. Required columns are:

```text
player_a,player_b,spread_a,odds_a,spread_b,odds_b
```

Recommended context columns are:

```text
date,tournament,surface,best_of
```

Run:

```powershell
python tennis_spread_model.py --markets novig_spreads_template.csv
```

Outputs are written to `tennis_model_output`:

- `spread_validation_summary.csv`
- `spread_rolling_predictions.csv`
- `novig_spread_recommendations.csv`
- `spread_results_history.csv` stores only recommendations recorded before match time and their later verified settlement. The dashboard keeps these actual betting results separate from model validation.

## Settlement safeguards

Half-game lines cannot push. Whole-game lines can push and are represented
explicitly in expected-value calculations. Future historical feature builds
exclude partial retirement scores from completed game-margin targets. Live
grading must still apply Novig's rule that an unfinished spread stands only
when its result was already unequivocally determined; otherwise it is void.
