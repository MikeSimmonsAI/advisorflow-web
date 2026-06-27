"""
Generic per-advisor feature toggle system.

Per Mike's explicit request: "I need to be able to put... whether I can
turn it off for someone or not... a command center for user and account
activity." He specifically asked which approach is "bulletproof" -
named toggles tied to real code, or a freeform/configurable system
where an admin could type in arbitrary permission names. The answer,
explained to him directly: named toggles are bulletproof because every
one of them is wired to a real, specific code check at the moment it's
created - there's no way for a typo'd or unused flag name to silently
do nothing, the way a freeform string would. A flag that exists in
KNOWN_FEATURE_FLAGS below but isn't checked anywhere yet is immediately
visible as dead code in review; a freeform flag that doesn't match
whatever string the code happens to check for fails completely
silently, forever, with nothing to catch it.

HOW THIS DIFFERS FROM User.can_import_leads: that's a real, dedicated
boolean column, kept that way deliberately - it controls something with
real weight (bulk lead import) and already has a tested, working,
directly-queryable implementation. This module is for LIGHTER-WEIGHT
feature gates added over time - early access to a new view, an
experimental feature - where adding a new database column for each one
would get tedious. Storage here is a single comma-separated string
column (User.feature_flags), not a new column per flag.

ADDING A NEW FLAG: add an entry to KNOWN_FEATURE_FLAGS below with a
real description, then check `has_feature_flag(user, "your_flag_name")`
wherever that feature's access should be gated. That's the only step -
no new migration, no new column. has_feature_flag/get_enabled_flags/
set_feature_flags below all validate against this same list, so a
typo'd flag name is rejected immediately rather than silently doing
nothing.
"""

# Real flags, in {name: description} form. Empty for now - no
# lightweight feature exists yet that needs this kind of toggle (the
# only real per-advisor permission today, can_import_leads, deliberately
# keeps its own dedicated column - see module docstring above). This is
# the registry the next one gets added to, not a list invented ahead of
# actual need.
KNOWN_FEATURE_FLAGS: dict[str, str] = {}


def get_enabled_flags(user) -> set[str]:
    """Returns the set of feature flag names currently enabled for this user."""
    if not user.feature_flags:
        return set()
    return {name.strip() for name in user.feature_flags.split(",") if name.strip()}


def has_feature_flag(user, flag_name: str) -> bool:
    """
    The actual gate check - call this wherever a lightweight feature's
    access should depend on a per-advisor toggle. Returns False for any
    flag name not in KNOWN_FEATURE_FLAGS, even if it happens to be
    present in the user's stored string (defensive - that shouldn't
    happen if set_feature_flags below is the only way flags get written,
    but a gate check should never trust stored data blindly).
    """
    if flag_name not in KNOWN_FEATURE_FLAGS:
        return False
    return flag_name in get_enabled_flags(user)


def set_feature_flags(user, flag_names: list[str]) -> None:
    """
    Replaces the user's full set of enabled flags with exactly the given
    list - not an add/remove toggle, a full replace, so the caller
    (the Users admin panel) always sends the complete desired state.

    Raises ValueError on any name not in KNOWN_FEATURE_FLAGS - this is
    the actual "bulletproof" guarantee: an admin (or a bug) can never
    silently set a flag that doesn't correspond to a real, working
    feature check somewhere in the code.
    """
    unknown = [name for name in flag_names if name not in KNOWN_FEATURE_FLAGS]
    if unknown:
        raise ValueError(f"Unknown feature flag(s): {', '.join(unknown)}")
    user.feature_flags = ",".join(sorted(set(flag_names))) if flag_names else None
