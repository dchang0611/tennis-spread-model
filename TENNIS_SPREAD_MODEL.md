# Repaired spread model: version 2.0.0

## What changed

The July snapshot scoring path is disabled. `player_features.py` rebuilds player
states after each verified completed match and calls one shared feature function
for historical and live observations. It keeps per-surface Elo and margin
histories and recomputes workload and rest for the requested date. Opponent
adjustment now compares serve points won to 1 minus the opponent's return points
won, and return points won to 1 minus the opponent's serve points won. Matchup
interaction treats stronger opposing return as unfavorable to the server.

## Sources and dates

Seasonal and ongoing ATP statistics: https://stats.tennismylife.org/tennis-match-database
Independent competition timestamps/results: ESPN ATP scoreboard.
Live surface: the matched Tennis Explorer event header.
Current paired spread prices: Novig event pages.

The seasonal schema is not uniformly day-resolved: older files have one date for
an entire tournament. The loader excludes events with only a tournament-level
date and nonstandard Next Gen scoring. Events with distinct match dates are
replayed by date; current result pairs/scores are independently checked. This
exclusion currently removes much of the older history and reduces the training
sample. A future source schema change must be checked before accepting more rows.

Date-only results become available the next UTC day, not at an invented completion
time. All observations within a day are created before any outcome from that day
updates state. Unknown within-day ordering is therefore never used for prediction.
Live players with a known same-day result are excluded. A result feed fetched today
but ending more than one completed calendar day ago fails freshness checks.

The current and previous two years are selected dynamically. Every required source
is freshly fetched; local copies are evidence, never silent fallback inputs. Unknown
or new events without unique current surface, format, level, and start evidence
are excluded. No year-end surface calendar or fixed best-of-three fallback remains
in the active pipeline.

## Decisions and evidence

The existing compact Elastic Net and default 4 percentage point probability-edge,
5% expected-return gates are frozen for research. Same-format rolling residuals
are required; best-of-three errors never substitute for best-of-five errors.
Only the strongest qualifying line per match becomes PAPER; other lines are PASS.
These probabilities remain unvalidated, and no live BET output is supported.

The archive only accepts a quote captured before its independently verified start,
within 30 minutes of capture. The first qualifying quote is immutable. Results
settle separately. Feature receipts include all live features, training cutoff,
model version, and source hashes. Workflow artifacts retain raw input captures
for 90 days; committed paper feature receipts and prediction records persist.
Raw source hashes alone do not guarantee that a mutable upstream source can be
reconstructed after artifact expiry.

## Evaluation and return to live use

Expanding-window folds train only on earlier dates. Hypothetical half-game spread
calibration uses only residuals from earlier folds. Those lines have no captured
prices, so they are explicitly diagnostics, never a historical betting ROI claim.

`data/paper_history.json` is prospective and separate from the legacy ledger.
Its evaluation records calibration, Brier score versus the captured paired-market
baseline, returns on decided stakes, and a day-cluster bootstrap interval. At least
200 settled selections over 60 days and a positive lower ROI interval are necessary
for manual review; they do not establish a profitable strategy. There is no automatic
promotion. Review must additionally examine selection, quote execution, source
coverage, closing prices, and an independent untouched holdout before a live release.

## Legacy reconciliation

Legacy prices, selected players and model probabilities remain locked. Missing
factor rationales are not filled retrospectively. Newly resolved outcomes have
source receipts; uncertain or conflicting matches remain pending. Legacy date-only
records cannot prove that a bet was executable before the actual match start and
are excluded from prospective validation.

## Checks

`python -m unittest discover -v` tests post-match updates, surface separation,
shared live/training features, within-day leakage prevention, age-out of workload,
missing metadata, source conflicts, quote expiry, immutable selections, and the
publication-level prohibition on live BET picks.
