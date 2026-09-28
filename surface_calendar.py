"""Sourced, tournament-scoped surface assignments; unknown dates stay closed."""
from datetime import date

SOURCE = "https://www.atptour.com/en/news/what-is-the-2026-atp-tour-calendar/"
# Main-draw dates from the ATP calendar. Do not extend these over Davis Cup,
# qualifying, or a new season without verifying the relevant event.
TOURNAMENTS = [
    ("Washington", "2026-07-27", "2026-08-02", "Hard"),
    ("Los Cabos", "2026-07-27", "2026-08-02", "Hard"),
    ("Montreal", "2026-08-02", "2026-08-12", "Hard"),
    ("Cincinnati", "2026-08-13", "2026-08-23", "Hard"),
    ("Winston-Salem", "2026-08-23", "2026-08-29", "Hard"),
    ("US Open", "2026-08-31", "2026-09-13", "Hard"),
    ("Chengdu", "2026-09-23", "2026-09-29", "Hard"),
    ("Hangzhou", "2026-09-23", "2026-09-29", "Hard"),
    ("Laver Cup", "2026-09-25", "2026-09-27", "Hard"),
    ("Tokyo", "2026-09-30", "2026-10-06", "Hard"),
    ("Beijing", "2026-09-30", "2026-10-06", "Hard"),
    ("Shanghai", "2026-10-07", "2026-10-18", "Hard"),
    ("Almaty", "2026-10-19", "2026-10-25", "Hard"),
    ("Brussels", "2026-10-19", "2026-10-25", "Hard"),
    ("Lyon", "2026-10-19", "2026-10-25", "Hard"),
    ("Vienna", "2026-10-26", "2026-11-01", "Hard"),
    ("Basel", "2026-10-26", "2026-11-01", "Hard"),
]


def resolve_surface(match_date: date, tournament: str = "ATP", calendar=None) -> dict:
    entries = TOURNAMENTS if calendar is None else calendar
    active = [row for row in entries if date.fromisoformat(row[1]) <= match_date <= date.fromisoformat(row[2])]
    if tournament.strip().lower() != "atp":
        active = [row for row in active if row[0].casefold() == tournament.strip().casefold()]
    surfaces = {row[3] for row in active}
    if len(surfaces) != 1:
        raise RuntimeError(f"No unambiguous ATP surface calendar entry exists for {match_date.isoformat()} ({tournament}); refusing to label the slate.")
    return {"surface": surfaces.pop(), "tournaments": [row[0] for row in active],
            "method": "named_tournament" if tournament.strip().lower() != "atp" else "same_surface_calendar_consensus",
            "source": SOURCE, "verified_on": "2026-09-28",
            "coverage_through": max(row[2] for row in entries)}
