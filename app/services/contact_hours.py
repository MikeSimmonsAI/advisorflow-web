"""MAY WE TEXT THIS PERSON RIGHT NOW? - permitted contact hours, in THEIR time.

WHY THIS EXISTS
---------------
The cadence engine had no clock. `touch_def["send_hour"]` was read into the
schedule and never used, so every touch fired whenever the runner happened to
come round - which, on a UTC server, is 3am in Dallas often enough to matter.
The engine never actually sent anything, so nobody was woken up. It is about
to be able to send, and that is the moment to put the clock in.

THE RULE IS ABOUT THE RECIPIENT, NOT US
---------------------------------------
Permitted-contact-time rules are written around the time where the PERSON
BEING CONTACTED is, not where the business is or where the server runs. So
this resolves the lead's own zone and compares there. An organization in
Chicago texting a lead in California at 8:30am Chicago time is texting them at
6:30am, and that is the case this module exists to refuse.

WHAT WE CAN ACTUALLY KNOW
-------------------------
There is no `leads.timezone` column, and inventing one would not fill it. What
a lead row does carry is `state`, `zip_code` and `phone`. So:

    state          the primary basis. Reliable where the lead's address was
                   captured, which is most imported books.
    area code      a fallback. Weaker - number portability means an area code
                   is where somebody got their phone, not where they live -
                   so it is marked UNCERTAIN and treated conservatively.
    nothing        NOT PERMITTED. See below.

Several states span two zones. Rather than guessing which half a lead is in,
a lead in a split state carries BOTH zones and is permitted only when the
moment is inside the window in EVERY candidate zone. That is not a compromise:
if it is permitted everywhere the person could be, it is permitted.

UNKNOWN MEANS NO
----------------
A lead whose zone cannot be determined is refused, and the refusal names the
missing field. This follows the rule `workforce/policy.within_operating_hours`
already states for a different clock: "UNCONFIGURED MEANS NOT PERMITTED TO
REACH ANYONE, not 'any time'." A defaulted-open window is exactly the harm.

This will refuse real leads on day one - every row with no state and no usable
phone. That is the honest answer, and `cadence_backlog` counts them for you
before anything is switched on, so the size of the problem is a number rather
than a surprise.

THE WINDOW
----------
09:00-20:00 local, every day. The federal floor for telemarketing calls is
08:00-21:00; this ships deliberately narrower, because an hour of margin at
each end costs a customer nothing and the difference between 8:01am and 7:59am
is not a line worth standing on. Widen it in ONE place if you decide to.

WHAT THIS MODULE DOES NOT CLAIM
-------------------------------
It is not legal advice and it does not model state-specific rules - several
states restrict Sunday contact, some narrow the window further, and those are
jurisdiction questions with real answers that belong in a table somebody
maintains, not in a guess made here. What it does is refuse the clear-cut
cases and refuse the unknown ones, which is strictly better than no clock at
all and is honest about being a floor rather than a ceiling.
"""

from __future__ import annotations

import re
from datetime import datetime, time, timezone as _timezone
from typing import Any, Dict, List, Optional, Tuple

# The permitted window, local to the recipient. One place.
WINDOW_START = time(9, 0)
WINDOW_END = time(20, 0)

# Why a lead was refused - stable codes, so a diagnostic can group by them.
PERMITTED = "permitted"
OUTSIDE_WINDOW = "outside_window"
ZONE_UNKNOWN = "zone_unknown"
ZONE_UNRESOLVABLE = "zone_unresolvable"

# How the zone was arrived at.
BASIS_STATE = "state"
BASIS_AREA_CODE = "area_code"

_ET = "America/New_York"
_CT = "America/Chicago"
_MT = "America/Denver"
_MT_NO_DST = "America/Phoenix"
_PT = "America/Los_Angeles"
_AKT = "America/Anchorage"
_HT = "Pacific/Honolulu"

# US state / territory -> the zones it spans. A state with two entries is
# genuinely split; both are carried and both must permit.
STATE_ZONES: Dict[str, List[str]] = {
    "AL": [_CT], "AK": [_AKT], "AZ": [_MT_NO_DST], "AR": [_CT],
    "CA": [_PT], "CO": [_MT], "CT": [_ET], "DE": [_ET], "DC": [_ET],
    "FL": [_ET, _CT],            # panhandle
    "GA": [_ET], "HI": [_HT], "ID": [_MT, _PT], "IL": [_CT],
    "IN": [_ET, _CT],            # north-west corner and Evansville area
    "IA": [_CT], "KS": [_CT, _MT], "KY": [_ET, _CT], "LA": [_CT],
    "ME": [_ET], "MD": [_ET], "MA": [_ET], "MI": [_ET, _CT],
    "MN": [_CT], "MS": [_CT], "MO": [_CT], "MT": [_MT],
    "NE": [_CT, _MT], "NV": [_PT, _MT], "NH": [_ET], "NJ": [_ET],
    "NM": [_MT], "NY": [_ET], "NC": [_ET], "ND": [_CT, _MT],
    "OH": [_ET], "OK": [_CT], "OR": [_PT, _MT], "PA": [_ET],
    "RI": [_ET], "SC": [_ET], "SD": [_CT, _MT], "TN": [_ET, _CT],
    "TX": [_CT, _MT],            # El Paso and Hudspeth
    "UT": [_MT], "VT": [_ET], "VA": [_ET], "WA": [_PT],
    "WV": [_ET], "WI": [_CT], "WY": [_MT],
    "PR": ["America/Puerto_Rico"], "VI": ["America/St_Thomas"],
    "GU": ["Pacific/Guam"],
}

_STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT",
    "delaware": "DE", "district of columbia": "DC", "florida": "FL",
    "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY",
    "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH",
    "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH",
    "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "puerto rico": "PR",
}

# Area code -> zone. NOT a complete NANP table, and deliberately so: a partial
# table that is right where it speaks is more useful than a full one nobody
# maintains. An unlisted area code falls through to "unknown", which is a
# refusal, not a default-open.
AREA_CODE_ZONES: Dict[str, List[str]] = {}
for _codes, _zone in (
    ("212 213 310 323 408 415 424 442 510 530 559 562 619 626 628 650 657 "
     "661 669 707 714 747 760 805 818 831 858 909 916 925 949 951 971 503 "
     "541 206 253 360 425 509 564 702 725 775 808", _PT),
    ("303 719 720 970 385 435 801 505 575 406 208 986 928 480 520 602 623",
     _MT),
    ("214 254 281 325 346 361 409 430 432 469 512 682 713 737 806 817 830 "
     "832 903 915 936 940 956 972 979 210 972 316 620 785 913 319 515 563 "
     "641 712 218 320 507 612 651 763 952 314 417 573 636 660 816 975 "
     "224 309 312 331 618 630 708 773 779 815 847 872 205 251 256 334 938 "
     "479 501 870 225 318 337 504 985 228 601 662 769 402 531 308 605 "
     "615 629 731 901 931 405 539 572 580 918 262 414 534 608 715 920", _CT),
    ("201 203 207 212 215 216 220 234 240 267 272 276 301 302 304 305 315 "
     "321 326 330 339 340 351 352 386 401 410 434 440 443 475 484 508 513 "
     "516 517 518 540 551 561 567 570 571 585 603 607 609 610 614 616 631 "
     "646 678 680 681 689 703 704 706 716 717 718 724 727 732 737 740 754 "
     "757 762 770 772 774 781 786 803 804 810 813 814 828 843 845 848 856 "
     "857 860 862 863 864 878 904 908 910 912 914 917 919 937 941 947 954 "
     "959 973 978 980 984 989", _ET),
    ("907", _AKT),
    ("808", _HT),
):
    for _c in _codes.split():
        AREA_CODE_ZONES.setdefault(_c, [])
        if _zone not in AREA_CODE_ZONES[_c]:
            AREA_CODE_ZONES[_c].append(_zone)


def _zone(name: str):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:                                        # noqa: BLE001
        return None


def _area_code(raw: Optional[str]) -> Optional[str]:
    """The NPA from a US number, or None. Tolerates +1, spaces and dashes."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    npa = digits[:3]
    # N-X-X: an area code never starts with 0 or 1.
    return npa if npa[0] not in "01" else None


def normalize_state(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    s = str(raw).strip()
    if len(s) == 2 and s.upper() in STATE_ZONES:
        return s.upper()
    return _STATE_NAMES.get(s.lower())


def zones_for_lead(lead) -> Dict[str, Any]:
    """Every zone this lead could plausibly be in, and how we decided.

    Returns {"zones": [...], "basis": str|None, "certain": bool}. An empty
    `zones` list is the honest "we do not know", and every caller treats it as
    a refusal rather than as a licence.
    """
    st = normalize_state(getattr(lead, "state", None))
    if st and st in STATE_ZONES:
        zones = list(STATE_ZONES[st])
        return {"zones": zones, "basis": BASIS_STATE, "certain": len(zones) == 1}

    for attr in ("phone", "phone_raw", "callback_phone"):
        npa = _area_code(getattr(lead, attr, None))
        if npa and npa in AREA_CODE_ZONES:
            zones = list(AREA_CODE_ZONES[npa])
            # Never "certain" from an area code. Numbers move; people keep them.
            return {"zones": zones, "basis": BASIS_AREA_CODE, "certain": False}

    return {"zones": [], "basis": None, "certain": False}


def local_times(lead, now_utc: Optional[datetime] = None) -> List[Tuple[str, datetime]]:
    """(zone name, local wall clock) for each candidate zone."""
    now_utc = now_utc or datetime.utcnow()
    if now_utc.tzinfo is None:
        aware = now_utc.replace(tzinfo=_timezone.utc)
    else:
        aware = now_utc
    out = []
    for name in zones_for_lead(lead)["zones"]:
        tz = _zone(name)
        if tz is None:
            continue
        out.append((name, aware.astimezone(tz).replace(tzinfo=None)))
    return out


def check(lead, now_utc: Optional[datetime] = None) -> Dict[str, Any]:
    """May this lead be contacted at this moment?

    {"permitted": bool, "code": str, "reason": str, "basis": str|None,
     "zones": [...], "local": {zone: "HH:MM"}}

    `permitted` is True only when every candidate zone is inside the window.
    """
    now_utc = now_utc or datetime.utcnow()
    resolved = zones_for_lead(lead)
    zones = resolved["zones"]

    if not zones:
        return {"permitted": False, "code": ZONE_UNKNOWN,
                "reason": ("No time zone can be determined for this lead - it "
                           "has no usable state and no recognised area code, "
                           "so there is no way to know whether it is the "
                           "middle of the night where they are."),
                "basis": None, "zones": [], "local": {}}

    pairs = local_times(lead, now_utc)
    if not pairs:
        return {"permitted": False, "code": ZONE_UNRESOLVABLE,
                "reason": ("This deployment cannot resolve the time zone %s "
                           "(no zoneinfo database)." % ", ".join(zones)),
                "basis": resolved["basis"], "zones": zones, "local": {}}

    local = {name: dt.strftime("%H:%M") for name, dt in pairs}
    outside = [name for name, dt in pairs
               if not (WINDOW_START <= dt.time() <= WINDOW_END)]
    if outside:
        first = outside[0]
        return {"permitted": False, "code": OUTSIDE_WINDOW,
                "reason": ("It is %s in %s, outside the permitted %s-%s "
                           "contact window."
                           % (local[first], first,
                              WINDOW_START.strftime("%H:%M"),
                              WINDOW_END.strftime("%H:%M"))),
                "basis": resolved["basis"], "zones": zones, "local": local}

    return {"permitted": True, "code": PERMITTED,
            "reason": "Inside the permitted contact window in %s."
                      % ", ".join(local),
            "basis": resolved["basis"], "zones": zones, "local": local}


def is_permitted(lead, now_utc: Optional[datetime] = None) -> bool:
    return check(lead, now_utc)["permitted"]


def next_permitted_utc(lead, now_utc: Optional[datetime] = None) -> Optional[datetime]:
    """The next naive-UTC moment this lead may be contacted, or None if never.

    Walks forward in 15-minute steps for at most 48 hours. A loop rather than
    arithmetic because the answer must hold across DST transitions and across
    several candidate zones at once, and 192 cheap comparisons is not worth
    being clever about.

    None means the zone is unknown - there is no "later" that fixes a missing
    state, and returning a plausible time would hide that.
    """
    from datetime import timedelta
    now_utc = now_utc or datetime.utcnow()
    if not zones_for_lead(lead)["zones"]:
        return None
    probe = now_utc.replace(second=0, microsecond=0)
    for _ in range(4 * 48):
        if check(lead, probe)["permitted"]:
            return probe
        probe += timedelta(minutes=15)
    return None
