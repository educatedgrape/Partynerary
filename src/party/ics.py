"""ICS calendar parsing — turn a calendar into a date ranking.

preferences_from_ics() turns a calendar into a ranking: clashing days are
dropped entirely, and Fri/Sat starts preferred.

Members with no calendar must not all receive the identical ranking — derive
a deterministic per-name offset into the candidate window.
"""

import hashlib
from datetime import datetime, timedelta


# Candidate departure dates — a window of Friday/Saturday starts
# over the next 8 weeks
CANDIDATE_WINDOW = 8

# Deterministic anchor for the candidate window. None = today. Pinned to a
# fixed reference date so the ranking window stays stable regardless of when
# the code runs.
RANK_REFERENCE_DATE = "20260910"


def _candidate_dates(origin_date=None):
    """Generate candidate departure dates (Fri/Sat) over the next N weeks.

    Returns a list of YYYYMMDD strings, ordered chronologically.
    """
    if origin_date is None:
        origin_date = datetime.now()
    elif isinstance(origin_date, str):
        origin_date = datetime.strptime(origin_date.replace("-", ""), "%Y%m%d")

    candidates = []
    current = origin_date + timedelta(days=1)
    end = origin_date + timedelta(weeks=CANDIDATE_WINDOW)

    while current <= end:
        # Friday = 4, Saturday = 5
        if current.weekday() in (4, 5):
            candidates.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)

    return candidates


def _name_offset(name):
    """Deterministic per-name offset into the candidate window.

    Uses a hash of the name so members without calendars get different
    rankings. Without this, all no-calendar members land on the same date
    in round one and the concession mechanism is invisible.
    """
    h = hashlib.sha256(name.lower().encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def preferences_from_ics(ics_text, name, origin_date=None):
    """Turn a calendar (ICS text) into a date ranking.

    Clashing days are dropped entirely. Fri/Sat starts are preferred.

    Args:
        ics_text:    raw ICS/VCALENDAR text (or None/empty for no calendar)
        name:        member name (for deterministic offset when no calendar)
        origin_date: base date for candidate generation; defaults to
                     RANK_REFERENCE_DATE when set, else today

    Returns:
        list of YYYYMMDD strings, best first.
    """
    if origin_date is None and RANK_REFERENCE_DATE:
        origin_date = RANK_REFERENCE_DATE
    candidates = _candidate_dates(origin_date)

    if not ics_text:
        # No calendar — use name-based offset for deterministic variety
        return _ranking_by_offset(candidates, name)

    # Parse busy days from ICS
    busy_days = _parse_busy_days(ics_text, origin_date)

    # Filter out candidates that clash with busy days
    available = [d for d in candidates if d not in busy_days]

    if not available:
        # Everything clashes — fall back to offset-based ranking
        return _ranking_by_offset(candidates, name)

    # Rank: prefer Fri/Sat, then by chronological order
    ranked = sorted(available, key=lambda d: _ranking_key(d))
    return ranked


def _ranking_by_offset(candidates, name):
    """Rank candidates using a deterministic name-based rotation.

    The offset rotates the candidate list so different names start at
    different positions, preventing all no-calendar members from agreeing
    instantly.
    """
    if not candidates:
        return []

    # First sort by preference (Fri/Sat first, then chronological)
    pre_sorted = sorted(candidates, key=lambda d: _ranking_key(d))

    # Then rotate by the name-based offset so different names start
    # at different positions in the sorted list
    offset = _name_offset(name) % len(pre_sorted)
    rotated = pre_sorted[offset:] + pre_sorted[:offset]
    return rotated


def _ranking_key(date_str):
    """Sort key: prefer Fri (0) and Sat (1), then chronological."""
    dt = datetime.strptime(date_str, "%Y%m%d")
    weekday = dt.weekday()
    # Friday=4 → priority 0, Saturday=5 → priority 1, others → priority 2
    if weekday == 4:
        priority = 0
    elif weekday == 5:
        priority = 1
    else:
        priority = 2
    return (priority, date_str)


def _parse_busy_days(ics_text, origin_date=None):
    """Extract busy day dates from ICS text.

    Returns a set of YYYYMMDD strings for days with events.
    Simple parser — handles VEVENT DTSTART/DTEND.
    """
    if origin_date is None:
        origin_date = datetime.now()
    elif isinstance(origin_date, str):
        origin_date = datetime.strptime(origin_date.replace("-", ""), "%Y%m%d")

    busy = set()
    # Simple line-by-line ICS parsing
    current_event_start = None
    current_event_end = None

    for line in ics_text.splitlines():
        line = line.strip()
        if line.startswith("DTSTART"):
            current_event_start = _parse_ics_date(line)
        elif line.startswith("DTEND"):
            current_event_end = _parse_ics_date(line)
        elif line == "END:VEVENT":
            if current_event_start:
                start = current_event_start
                end = current_event_end or start
                # Add all days in the range
                current = start
                while current <= end:
                    busy.add(current.strftime("%Y%m%d"))
                    current += timedelta(days=1)
            current_event_start = None
            current_event_end = None

    return busy


def _parse_ics_date(line):
    """Parse a DTSTART or DTEND value from an ICS line.

    Handles formats:
      DTSTART:20260918
      DTSTART;VALUE=DATE:20260918
      DTSTART;TZID=...:20260918T090000
    """
    # Extract the value after the last ':'
    if ":" not in line:
        return None
    value = line.split(":")[-1].strip()
    # Take just the date part (first 8 chars)
    date_str = value[:8]
    try:
        return datetime.strptime(date_str, "%Y%m%d")
    except ValueError:
        return None
