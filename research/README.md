# Original-model reconstruction

> Legacy audit notes for the initial pooled reconstruction. Later format-separated
> artifacts have different counts. The current production model and new fixed
> comparison are documented in [BASELINE.md](BASELINE.md); do not use these old
> dashboard descriptions or counts as current validation.

The original compact spread model was replayed with refreshed player state and chronological training, without the new model's 10-match, 5-surface-match, or 150 same-format-residual exclusion gates. Original Elastic Net settings, imputation, feature formulas, residual fallback, thresholds, driver labels and V2 filter were preserved. Each day is retrained using only earlier dated outcomes. First qualifying prices are locked once per match; later quotes never improve historical fills.

Recovered 7,226 independently dated training matches (2,638 in 2024; 2,440 in 2025; 2,148 in 2026). 888 source rows could not be uniquely dated and remain explicitly excluded. The original market recovery preserves 2,822 distinct paired quote observations. This reconstruction assessed 4,362 sides across 219 matches; metadata/player gaps remain preserved in exclusion files. Five selected matches have ungraded finishes; no settlement rules were invented for them.

| Cohort | Selections | Wins | Losses | Ungraded | Profit | ROI on decided selections |
|---|---:|---:|---:|---:|---:|---:|
| Original rules, refreshed as-of inputs | 167 | 67 | 95 | 5 | -13.976u | -8.63% |
| Unchanged original V2 filter | 94 | 37 | 54 | 3 | -9.098u | -10.00% |

September 13 has 20 assessed sides, trained on 7,138 earlier matches with the latest result dated September 11. Every individual reconstruction records its capture time, training cutoff, actual latest training result, factor rationale, original quoted odds and source archive commit.

These negative results do not support assuming a fresh feed would restore a profitable edge. Confluence groups remain useful research evidence, but overlapping groups selected after seeing results are not independent validation.

## Chronology and methodological limits

No results dated on or after a quote's UTC day enter that fit or player state. Date-only outcomes become usable the following UTC day. Actual source publication timestamps and correction histories were not archived, so these are reconstructed availability assumptions, not a claim to reproduce the exact bytes known that day. Independently recovered match dates replace coarse tournament-week dates. Matches on the same date are predicted as one batch before that day's results update state; A/B alternation follows the resulting chronological order. Current-event surface/format is reconstructed from matched records rather than blindly assigning best-of-three or reusing a different surface.

These are necessary timing/metadata changes, not a byte-for-byte replay of the faulty snapshot pipeline. Original statistical formulas are intentionally retained for this comparison, including their known limitations; the separate repaired prospective paper model is not replaced by this research protocol. July's stale rows are never reused. Factor rationales are recomputed, never copied from old selections.

The previous strict repaired-model replay with 36 selections is a separate experiment. Its -6.09u outcome is not the result of this original-protocol reconstruction.

## Dashboard and protection

Reconstructed Slates exposes every assessed saved quote. Reconstructed Results and Reconstructed V2 use only the new as-of records. Factor Performance and Factor Confluence draw their historical comparisons exclusively from those new records. Legacy Archive preserves original selections and outcomes, prominently marked INVALID MODEL INPUTS. There is no fallback from missing reconstruction data to old factor history.

The live pipeline already downloads rolling current seasons without a fixed terminal year, rebuilds state and rejects stale/missing inputs. An additional guard now rejects old training rows even when accompanied by a fresh source receipt. Date boundaries require explicit timezone-aware decision times and are tested at year rollover. Live betting remains disabled; prospective paper evidence stays separate.

## Validation and reproduction

63 automated tests pass, including September 13 future-result exclusion, actual publication-time handling when provided, future-outcome perturbation invariance, stale-training rejection, year rollover, and original-protocol manifest parity. 8,809 artifact checks verify prices, outcomes, chronological state, first qualifying choice, and unchanged original model functions. Browser checks verify September 13, 167 reconstructed selections, 94 V2 selections and the separate 229-row legacy archive.

From the repository root, install requirements and run:

`python research/reconstruct_original.py --through 2026-10-01`

`python research/verify_reconstruction.py`

`--through` is mandatory; execution date is never substituted for a historical cutoff. Optional `--input-dir` and `--output-dir` support other archived datasets. Input CSVs and source receipts are preserved under `data/reconstruction_inputs`. The program cannot fabricate quotes absent from that input. It writes separate retrospective output and never alters actual or paper ledgers.

Each run also writes a content-addressed immutable copy under `data/reconstructions`, identified by its data, code, and requested end date. The dashboard copy points to that run identity.

Full local exports: `all_reconstructed_lines.csv`, `locked_reconstructed_picks.csv`, `factor_confluence.csv`, `feature_receipts.csv`, `training_audit.csv`, `market_metadata_exclusions.csv`, `scoring_exclusions.csv`, and `september_13_slate.csv` in this report's directory. Original dated API responses remain in the local research folders; receipt hashes and source URLs are preserved in the repository.
