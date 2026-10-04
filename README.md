# Tennis Spread Lab — paper-only repair release

Live betting is disabled. Version 3 (`3.0.0-elo-paper`) produces PAPER/PASS research observations;
it cannot emit BET or automatically promote itself to live betting.

Run `python -m unittest discover -v`, then `python novig_scraper.py --tournament ATP --surface Auto --minimum-matches 1`, `python run_paper_pipeline.py`, and `python build_spread_site.py`.
The scheduled workflow does this automatically and publishes an explicit CLOSED
board when any required input fails. A deployed page does not imply healthy data.

Every run downloads the current and preceding two seasonal statistics files plus
ongoing tournaments. No cached or fixed-date model file supplies player state.
Independently recovered historical dates are reused only when they exactly match
fresh event/player/score/format records; fresh statistics are always retained.
Other tournament-only dates are excluded rather than used as match dates. Same-day
results are withheld until the next UTC day; players with known same-day results
are excluded until their state can include those results. The latest completed
match date must be within one calendar day, and recent player results are checked
against an independent schedule/results feed.

Training and inference use the same feature function and post-match state engine.
Surface histories are separate; workload/rest are computed for the target start.
Missing player data, ambiguous identities, unresolved event format, expired
quotes, or already-started matches are excluded. Every paper pick has an immutable
feature receipt, model version, source hashes, price, start time and capture time.

The old results ledger is preserved and clearly labeled as legacy records whose
pre-start capture was not enforced. It is never mixed into new prospective results.
Each format's paper review requires at least 200 settled selections spanning 60 days,
favorable calibration/market comparison and uncertainty checks, plus manual
review of execution and an independent holdout. Reaching those counts alone is
not approval and never enables live betting.

See [research/BASELINE.md](research/BASELINE.md) for the current fixed comparison,
reproduction steps, data-quality limits and prospective protocol. Historical
original-model reconstructions are audit artifacts, not the current model.

## Small-edge experiment

The separate Small-edge experiment tab tracks `small-edge-4-8-v1`. Its frozen
definition and activation timestamp are in `small_edge_experiment.json`. It
filters the current Elo baseline's first locked selection to an original claimed
edge of at least 0.04 and strictly below 0.08. It does not select another line,
rescore a match, change calibration, or modify the baseline. All existing data,
price-age, and pre-start checks still apply. Matches locked before activation
are excluded; historical replay is never backfilled into prospective results.

Every scheduled refresh enrolls eligible lines before start, preserves their
original prediction/price/provenance, and copies only verified settlement fields
from the baseline. The independent `data/small_edge_history.json` ledger and
`data/small_edge_evaluation.json` report are persisted by the existing workflow.
Definition and prediction hashes detect changes to locked evidence. An experiment
error preserves its ledger and reports the failure without changing the baseline.
The activation date is a lower bound, not an expiry; collection has no end date.

The tab shows the line, captured price, probabilities, claimed edge and outcome.
Date and format filters apply; the historical dataset selector never substitutes
replay results. BO3/BO5 progress and returns remain separate. Returns assume one
unit per line before execution costs; pending/void rows do not count as decided
risk. The 200-decision/60-day minimum is a review checkpoint, not proof of an edge
or permission to enable live betting. Offline recalibration is a separate study
and is not part of this experiment.
