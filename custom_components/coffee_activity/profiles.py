"""Validated, per-machine profiles without fabricated manufacturer presets."""
from copy import deepcopy
from collections.abc import Mapping
import json
from .engine import DEFAULT_OPTIONS, DetectionOptions
from .analytics import feature_options

ROCKET_PROFILE = "Rocket Appartamento"
CUSTOM_PROFILE = "Individuell"
REFINED_ROCKET_PROFILE = "Rocket Appartamento (optimiert)"
MAX_PROFILES = 20

def normalise_options(raw=None):
    raw = raw or {}
    original = DetectionOptions.from_mapping(raw).as_dict()
    library = raw.get("profiles")
    if isinstance(library, Mapping):
        if len(library) > MAX_PROFILES:
            raise ValueError("Too many profiles")
        profiles = {validate_name(name): DetectionOptions.from_mapping(values).as_dict()
                    for name, values in library.items() if isinstance(values, Mapping)}
    else:
        profiles = {}
    # On the first upgrade preserve the actual configured thresholds, not just defaults.
    profiles.setdefault(ROCKET_PROFILE, dict(original))
    profiles.setdefault(CUSTOM_PROFILE, dict(original))
    if len(profiles) > MAX_PROFILES:
        raise ValueError("Too many profiles")
    active = raw.get("profile", ROCKET_PROFILE)
    if active not in profiles:
        raise ValueError("Unknown profile")
    stored_features = raw.get('profile_features', {})
    profile_features = {name: feature_options(values) for name,values in stored_features.items()
                        if name in profiles and isinstance(values, Mapping)} if isinstance(stored_features, Mapping) else {}
    if active in profile_features:
        profile_features[active] = feature_options(raw.get('features'))
    return {**profiles[active], "profile": active, "profiles": profiles,
            "profile_features": profile_features,
            "advanced": bool(raw.get("advanced", False)),
            "rocket_refinement_version": 1 if raw.get("rocket_refinement_version") == 1 else 0,
            **({"maintenance_options_sha": raw["maintenance_options_sha"]}
               if isinstance(raw.get("maintenance_options_sha"), str) else {}),
            "features": feature_options(raw.get("features"))}

def refine_rocket_options(raw, data):
    """One-time refinement authorized for the existing, migrated Rocket machine.

    Never tune other machines or overwrite custom thresholds/the saved original.
    HA saves this migration through its config-entry API during setup.
    """
    result = normalise_options(raw)
    if (result["rocket_refinement_version"] or not data.get("migrate_legacy")
            or data.get("power_entity") != "sensor.kaffeemaschine_switch_0_power"
            or result["profile"] != ROCKET_PROFILE
            or result["profiles"][ROCKET_PROFILE]["milk_pump_min_s"] != 5
            or REFINED_ROCKET_PROFILE in result["profiles"]
            or len(result["profiles"]) >= MAX_PROFILES):
        return result
    values = {**result["profiles"][ROCKET_PROFILE], "milk_pump_min_s": 15}
    result = add_profile(result, REFINED_ROCKET_PROFILE, values, activate=True)
    result["rocket_refinement_version"] = 1
    return result

def validate_name(name):
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
        raise ValueError("Invalid profile name")
    if any(ord(char) < 32 for char in name):
        raise ValueError("Invalid profile name")
    return name.strip()

def select_profile(raw, name):
    result = normalise_options(raw)
    if name not in result["profiles"]:
        raise ValueError("Unknown profile")
    result.update(result["profiles"][name])
    result["profile"] = name
    if name in result['profile_features']:
        result['features'] = deepcopy(result['profile_features'][name])
    return result

def add_profile(raw, name, values, *, activate=False, features=None):
    result = deepcopy(normalise_options(raw))
    name = validate_name(name)
    if name == ROCKET_PROFILE:
        raise ValueError("The original Rocket Appartamento profile is protected")
    if name in result["profiles"]:
        raise ValueError("Profile already exists")
    if len(result["profiles"]) >= MAX_PROFILES:
        raise ValueError("Too many profiles")
    result["profiles"][name] = DetectionOptions.from_mapping(values).as_dict()
    if features is not None:
        # Freeze both current and new recipe so switching back restores it.
        result['profile_features'].setdefault(result['profile'], deepcopy(result['features']))
        result['profile_features'][name] = feature_options(features)
    return select_profile(result, name) if activate else result

def save_thresholds(raw, name, values):
    result = deepcopy(normalise_options(raw))
    values = DetectionOptions.from_mapping(values).as_dict()
    if name == ROCKET_PROFILE:
        if values == result["profiles"][name]:
            return select_profile(result, name)
        name = "Rocket Appartamento (angepasst)"
        suffix = 2
        while name in result["profiles"]:
            name = f"Rocket Appartamento (angepasst {suffix})"
            suffix += 1
        return add_profile(result, name, values, activate=True)
    if name not in result["profiles"]:
        raise ValueError("Unknown profile")
    result["profiles"][name] = values
    return select_profile(result, name)

def export_profile(raw):
    options = normalise_options(raw)
    return json.dumps({"format": "coffee_activity_profile", "version": 1,
                       "name": options["profile"],
                       "thresholds": options["profiles"][options["profile"]]},
                      ensure_ascii=False, indent=2, allow_nan=False)

def import_profile(raw, document, name):
    if not isinstance(document, str) or len(document) > 16384:
        raise ValueError("Profile JSON too large")
    def unique_pairs(pairs):
        values = {}
        for key, value in pairs:
            if key in values:
                raise ValueError("Duplicate JSON key")
            values[key] = value
        return values
    data = json.loads(document, object_pairs_hook=unique_pairs)
    if (not isinstance(data, dict) or set(data) != {"format", "version", "name", "thresholds"}
            or data["format"] != "coffee_activity_profile" or type(data["version"]) is not int
            or data["version"] != 1 or not isinstance(data["thresholds"], dict)
            or set(data["thresholds"]) != set(DEFAULT_OPTIONS)):
        raise ValueError("Unsupported profile document")
    validate_name(data["name"])
    # Import is always a named copy and never replaces/activates the original.
    return add_profile(raw, name, data["thresholds"])
