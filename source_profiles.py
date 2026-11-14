"""Explicit source choices; no genre inference or model dependency."""

PROFILES = {"general", "animation", "broadcast", "sports_gaming", "presentation"}
FULL_FRAME = {"broadcast", "sports_gaming", "presentation"}


def validate_profile(value):
    if not isinstance(value, str) or value not in PROFILES:
        raise ValueError("Choose a supported source profile")
    return value


def profile_zones(zones, profile):
    # Episode-specific evidence is not evidence that a broadcast segment is disposable.
    validate_profile(profile)
    return [z for z in zones if profile == "animation" or z.get("source") == "manual override"]
