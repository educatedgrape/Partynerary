"""Demo party — four members with colliding calendars.

demo_party() returns a list of MemberAgent objects ready for the concession
engine. The calendars are chosen to produce 3+ rounds of visible concession.
"""

from src.party.preferences import Ceiling, MemberPreferences
from src.party.ics import preferences_from_ics


# Fixed origin date for deterministic tests
_ORIGIN = "20260827"

# Demo ICS calendars with deliberate collisions
# Marcus: busy on the first two Fridays
_MARCUS_ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART:20260904
DTEND:20260905
SUMMARY:Conference
END:VEVENT
BEGIN:VEVENT
DTSTART:20260911
DTEND:20260912
SUMMARY:Team offsite
END:VEVENT
END:VCALENDAR"""

# Sara: busy on the first Saturday and second Friday
_SARA_ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART:20260905
DTEND:20260906
SUMMARY:Wedding
END:VEVENT
BEGIN:VEVENT
DTSTART:20260911
DTEND:20260912
SUMMARY:Workshop
END:VEVENT
END:VCALENDAR"""

# Jin: busy on the first Friday
_JIN_ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART:20260904
DTEND:20260905
SUMMARY:Deadline
END:VEVENT
END:VCALENDAR"""

# Alex: no calendar (uses name-based offset)
_ALEX_ICS = None


def _make_prefs(name, origin, ceiling_amount, ics_text, avatar,
                preferences_text=""):
    """Build MemberPreferences from a calendar."""
    ranking = preferences_from_ics(ics_text, name, origin_date=origin)
    ceiling = Ceiling(member=name, amount=ceiling_amount)

    return MemberPreferences(
        member=name,
        origin="SIN",
        ceiling=ceiling,
        date_ranking=ranking,
        avatar=avatar,
        preferences=preferences_text,
    )


def demo_party():
    """Four members with colliding calendars.

    Returns a list of MemberPreferences. Their favourite dates differ,
    guaranteeing visible concession rounds.
    """
    members = [
        _make_prefs("Marcus", _ORIGIN, 350.0, _MARCUS_ICS, "🧔",
                    "beach and relax, somewhere tropical"),
        _make_prefs("Sara", _ORIGIN, 280.0, _SARA_ICS, "👩",
                    "great street food but no nightlife"),
        _make_prefs("Jin", _ORIGIN, 400.0, _JIN_ICS, "🧑",
                    "kimchi and kpop"),
        _make_prefs("Alex", _ORIGIN, 300.0, _ALEX_ICS, "🙂",
                    "anywhere but bangkok"),
    ]
    return members
