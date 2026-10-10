# Match metadata and publication contract

Collection and match dates are different facts. Novig's public ATP inventory
supplies stable event IDs and absolute start timestamps. The collector visits
those events within the next 48 hours, verifies their participants and pregame
state again on the event response, and reads the displayed paired spread prices.
Relative Today/Tomorrow labels are never used as the slate boundary.

Each match uses its own Pacific date for the board and archive identity. Its
dated Tennis Explorer schedule/header supplies the surface. Every request pins the
site's London timezone setting and verifies the returned timezone label. Calendar
dates use Europe/London (including DST), never the tournament location or an
unverified site default; source timezone is preserved with each captured line. The linked edition's
singles winner ranking points identify supported ATP 250/500/1000 or Grand Slam
categories; no completed matches or previous year's tournament are required.
Unknown editions/categories remain excluded. The format follows ATP Tour singles
BO3 or Grand Slam men's main draw BO5, cross-checked against ESPN's major flag and
round. Qualifying remains outside the frozen candidate. ESPN's generic regulation
period count is not used: the live feed contains five-period qualifying records.

Rule references: ATP Official Rulebook, Circuit Regulations, Matches - Number of
Sets (https://www.atptour.com/en/corporate/rulebook), and Grand Slam Rulebook,
Number of Sets (https://www.itftennis.com/en/about-us/governance/rules-and-regulations/).
Novel formats require an explicit reviewed implementation, never an ATP default.

The independent schedule must confirm a future pregame start and agree with the
surface record's explicitly verified London calendar date. If the two confirmed start times differ,
the earlier time closes collection/scoring. A TBD time is not a verified start.
Conflicting independent snapshots exclude the affected competition rather than
allowing a later loop iteration to overwrite it silently.

Training still excludes same-UTC-day and future results. All player-history,
recent-result completeness, source-age, quote-age and separate-format checks
remain in force. Neither model parameters nor experiment eligibility changed.
The first recorded selection remains immutable; event URL and competition ID
also prevent recording it again after a date change. Existing prediction hashes
and experiment history are not rewritten.

GitHub schedules one daily refresh at midnight America/Los_Angeles, including DST.
The previous Codex dispatch automation is paused to avoid duplicate refreshes.
Scheduling or execution can be late; the site marks snapshots overdue after 26 hours.
The 30-minute quote limit remains enforced at scoring and publication. In the
browser it distinguishes current quotes from expired recorded prices; it no longer
deletes valid scored evidence. Recorded start times passing also change labels
rather than removing snapshot lines. Date and format filters still apply.
The last successful snapshot is persisted with its original timestamp. Failed
refreshes expose it as recorded evidence with the current failure reason. History,
experiment selection rules, and locked original prices are never recomputed from it.
The browser reloads the published payload every five minutes and rechecks quote
age every 30 seconds. Captured prices remain visible with timestamps and expiry
labels, independently of whether they qualify for scoring. They are not enrolled
retroactively. A verified empty inventory is distinct from a source/schema error.

Deployment success is not data readiness. Inspect collection coverage, exclusions,
source freshness, scoring, settlement and public asset/data parity separately.
Unresolved names, TBD starts, unsupported categories, retirement reconciliation,
and unsettled legacy records are disclosed, not guessed away. Source outages and
schema changes can still require intervention; this contract prevents them from
silently turning into current model inputs.

Regression validation: unittest discovery includes future-year first-day events,
midnight and DST boundaries, current edition/category parsing, unknown/qualifying
events, conflicting schedules, changed participants and rescheduled immutable
selections. Frozen baseline receipt verification must continue to pass.
