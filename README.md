# Tennis Spread Lab

An evidence-first tennis game-spread model and GitHub Pages dashboard designed for paired Novig lines.

## What it does

- predicts the final game differential with a compact, regularized model;
- converts rolling out-of-sample errors into cover probabilities;
- removes the paired market hold from both sides of each spread;
- calculates probability edge and expected ROI;
- applies an uncertainty haircut and conservative decision gates; and
- recommends no more than one alternate line per match.

The public dashboard fails closed when current Novig lines have not been scored. Sample markets are never published as live recommendations.

## Current market input

Update `data/novig_spreads.csv` with one row per paired spread. Pushing that file to `main`, or manually running the workflow, rebuilds and publishes the dashboard.

## Model safeguards

- expanding-window rolling validation;
- compact feature families to reduce correlated duplicate signals;
- Elastic Net shrinkage;
- explicit whole-game push handling;
- partial retirement scores excluded from completed-margin training targets;
- minimum 4 percentage-point edge and 5% expected ROI;
- conservative probability requirement; and
- one qualified position per match.

See `TENNIS_SPREAD_MODEL.md` for the full methodology.

## Disclaimer

This is a research tool, not a guarantee of profit or financial advice. Prices move, historical performance may not persist, and incomplete or stale inputs must produce no play.
