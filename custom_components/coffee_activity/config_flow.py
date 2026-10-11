"""Set up and tune coffee detection from an existing power sensor."""

from __future__ import annotations

from math import isfinite
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import DEFAULT_NAME, DEFAULT_OPTIONS, DOMAIN
from .engine import DetectionOptions
from .profiles import normalise_options, select_profile, add_profile, save_thresholds, ROCKET_PROFILE, CUSTOM_PROFILE
from .profiles import import_profile, export_profile
from .analytics import feature_options

CONF_POWER_ENTITY = "power_entity"
CONF_NAME = "name"
_DEFAULT_POWER_ENTITY = "sensor.kaffeemaschine_switch_0_power"

_ESPRESSO_OPTIONS = (
    "off_w",
    "phase_start_w",
    "espresso_pump_min_w",
    "espresso_pump_max_w",
    "espresso_min_s",
    "espresso_max_s",
    "live_pump_min_w",
    "live_pump_max_w",
    "tick_seconds",
)
_MILK_OPTIONS = (
    "milk_pump_min_w",
    "milk_pump_max_w",
    "milk_pump_min_s",
    "milk_pump_max_s",
    "milk_heating_min_w",
    "milk_heating_min_s",
    "milk_heating_max_s",
    "milk_after_espresso_s",
)


def _options_schema(keys: tuple[str, ...], values: dict[str, Any]) -> vol.Schema:
    """Use number boxes with units for the detection thresholds."""
    return vol.Schema(
        {
            vol.Required(key, default=values[key]): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.1,
                    step=0.1,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="W" if key.endswith("_w") else "s",
                )
            )
            for key in keys
        }
    )


class CoffeeActivityConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create one detector per power sensor."""

    VERSION = 1

    def _source_error(
        self, entity_id: str, *, allow_unavailable: bool = False
    ) -> str | None:
        """Validate the source without making any device or network requests."""
        if not isinstance(entity_id, str) or not entity_id.startswith("sensor."):
            return "invalid_power_sensor"
        source = self.hass.states.get(entity_id)
        if source is None:
            # YAML imports may run before the power integration has initialized.
            return None if allow_unavailable else "sensor_not_found"
        unit = source.attributes.get("unit_of_measurement")
        if unit not in ("W", "kW"):
            if not (allow_unavailable and unit is None):
                return "invalid_power_unit"
        if not allow_unavailable:
            try:
                value = float(source.state)
            except (TypeError, ValueError):
                return "sensor_not_numeric"
            if not isfinite(value) or value < 0:
                return "sensor_not_numeric"
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select the appliance name and an existing sensor in W or kW."""
        errors: dict[str, str] = {}
        values = user_input or {CONF_NAME: DEFAULT_NAME}
        if user_input is not None:
            name = str(user_input[CONF_NAME]).strip()
            power_entity = user_input[CONF_POWER_ENTITY]
            if not name:
                errors[CONF_NAME] = "invalid_name"
            if error := self._source_error(power_entity):
                errors[CONF_POWER_ENTITY] = error
            if not errors:
                await self.async_set_unique_id(power_entity)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=name,
                    data={CONF_NAME: name, CONF_POWER_ENTITY: power_entity},
                    options=select_profile(normalise_options(DEFAULT_OPTIONS), user_input.get("profile", ROCKET_PROFILE)),
                )

        source_key = vol.Required(CONF_POWER_ENTITY)
        default_source = values.get(CONF_POWER_ENTITY)
        if default_source is None and self._source_error(_DEFAULT_POWER_ENTITY) is None:
            default_source = _DEFAULT_POWER_ENTITY
        if default_source is not None:
            source_key = vol.Required(CONF_POWER_ENTITY, default=default_source)
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default=values[CONF_NAME]): str,
                    source_key: selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Required("profile", default=values.get("profile", ROCKET_PROFILE)): selector.SelectSelector(selector.SelectSelectorConfig(options=[ROCKET_PROFILE, CUSTOM_PROFILE], mode=selector.SelectSelectorMode.DROPDOWN)),
                }
            ),
            errors=errors,
        )

    async def async_step_import(
        self, import_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Import the prepared local migration without reading private files."""
        name = str(import_data.get(CONF_NAME, DEFAULT_NAME)).strip()
        if import_data.get("migrate_legacy") and any(entry.data.get("migrate_legacy") for entry in self._async_current_entries()):
            return self.async_abort(reason="already_configured")
        power_entity = import_data.get(CONF_POWER_ENTITY)
        if not name or not isinstance(power_entity, str):
            return self.async_abort(reason="invalid_import")
        if self._source_error(power_entity, allow_unavailable=True):
            return self.async_abort(reason="invalid_power_sensor")
        try:
            options = DetectionOptions.from_mapping(
                {**DEFAULT_OPTIONS, **import_data.get("options", {})}
            )
            options.validate()
        except (TypeError, ValueError):
            return self.async_abort(reason="invalid_options")

        await self.async_set_unique_id(power_entity)
        self._abort_if_unique_id_configured()
        data: dict[str, Any] = {CONF_NAME: name, CONF_POWER_ENTITY: power_entity}
        if import_data.get("migrate_legacy"):
            snapshot = import_data.get("migration_snapshot", {})
            if not isinstance(snapshot, dict):
                return self.async_abort(reason="invalid_import")
            data["migrate_legacy"] = True
            data["migration_snapshot"] = snapshot
        return self.async_create_entry(title=name, data=data, options=normalise_options(options.as_dict()))

    async def async_step_reconfigure(self, user_input=None):
        entry = self._get_reconfigure_entry()
        values = user_input or entry.data
        errors = {}
        if user_input is not None:
            name = str(user_input[CONF_NAME]).strip()
            source = user_input[CONF_POWER_ENTITY]
            error = self._source_error(source)
            coordinator = self.hass.data.get(DOMAIN, {}).get(entry.entry_id)
            if not name:
                errors[CONF_NAME] = "invalid_name"
            if error:
                errors[CONF_POWER_ENTITY] = error
            if coordinator and coordinator.busy:
                errors["base"] = "busy"
            if any(other.entry_id != entry.entry_id and other.data.get(CONF_POWER_ENTITY) == source for other in self._async_current_entries()):
                errors[CONF_POWER_ENTITY] = "source_already_configured"
            if not errors:
                # The registered update listener reloads exactly once and retains storage/IDs.
                return self.async_update_and_abort(entry, unique_id=source, title=name,
                    data_updates={CONF_NAME: name, CONF_POWER_ENTITY: source})
        return self.async_show_form(step_id="reconfigure", errors=errors, data_schema=vol.Schema({
            vol.Required(CONF_NAME, default=values[CONF_NAME]): str,
            vol.Required(CONF_POWER_ENTITY, default=values[CONF_POWER_ENTITY]): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> CoffeeActivityOptionsFlow:
        """Let the framework supply the options flow's config_entry property."""
        return CoffeeActivityOptionsFlow()


class CoffeeActivityOptionsFlow(OptionsFlow):
    """Edit detection thresholds while retaining the source and counter history."""

    def __init__(self) -> None:
        """Keep submitted settings between the two form steps."""
        self._values: dict[str, Any] | None = None

    def _current_values(self) -> dict[str, Any]:
        """Merge defaults so older entries also receive new options."""
        if self._values is None:
            self._values = normalise_options(self.config_entry.options)
        return self._values

    def _validate_values(self) -> bool:
        """Apply the same validation as the live detection engine."""
        try:
            options = DetectionOptions.from_mapping(self._current_values())
            options.validate()
        except (TypeError, ValueError):
            return False
        self._values.update(options.as_dict())
        return True

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Tune the phase boundaries and espresso detection."""
        errors: dict[str, str] = {}
        values = self._current_values()
        if user_input is not None:
            coordinator = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
            try:
                if coordinator and coordinator.busy:
                    errors["base"] = "busy"
                else:
                    action = user_input.get("action", "configure")
                    if action == "features":
                        return await self.async_step_features()
                    if action == "import_profile":
                        return await self.async_step_import_profile()
                    if action == "export_profile":
                        return await self.async_step_export_profile()
                    if action != "configure":
                        raise ValueError("Unknown options action")
                    values = select_profile(values, user_input["profile"])
                    new_name = str(user_input.get("new_profile_name", "")).strip()
                    if new_name:
                        values = add_profile(values, new_name, values, activate=True)
                    values["advanced"] = user_input.get("advanced", False)
                    self._values = values
                    if values["advanced"]:
                        return await self.async_step_espresso()
                    return self.async_create_entry(title="", data=values)
            except (TypeError, ValueError):
                errors["base"] = "invalid_profile"
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required("profile", default=values["profile"]): selector.SelectSelector(selector.SelectSelectorConfig(options=list(values["profiles"]), mode=selector.SelectSelectorMode.DROPDOWN)),
                vol.Optional("new_profile_name", default=""): str,
                vol.Required("action", default="configure"): selector.SelectSelector(selector.SelectSelectorConfig(
                    options=["configure", "features", "import_profile", "export_profile"],
                    translation_key="options_action", mode=selector.SelectSelectorMode.DROPDOWN)),
                vol.Required("advanced", default=False): bool}),
            errors=errors,
            description_placeholders={
                "power_entity": self.config_entry.data[CONF_POWER_ENTITY]
            },
        )

    def _busy(self):
        coordinator = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
        return coordinator is not None and coordinator.busy

    async def async_step_features(self, user_input=None):
        values = self._current_values()["features"]
        errors = {}
        if user_input is not None:
            try:
                candidate = feature_options(user_input)
                source_id = candidate["energy_entity"]
                if source_id and candidate["energy_enabled"]:
                    source = self.hass.states.get(source_id)
                    if source is None or source.attributes.get("unit_of_measurement") not in ("Wh", "kWh"):
                        errors["energy_entity"] = "invalid_energy_sensor"
                    else:
                        reading = float(source.state)
                        if not isfinite(reading) or reading < 0:
                            errors["energy_entity"] = "invalid_energy_sensor"
                if self._busy():
                    errors["base"] = "busy"
                if not errors:
                    options = self._current_values()
                    options["features"] = candidate
                    return self.async_create_entry(title="", data=options)
            except (ValueError, TypeError, OverflowError):
                errors["base"] = "invalid_features"
        schema = {vol.Required(key, default=values[key]): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=3600, step=1,
                        mode=selector.NumberSelectorMode.BOX, unit_of_measurement="s"))
                  for key in ("stale_after_s", "max_gap_s", "session_gap_s")}
        schema.update({vol.Required("standalone_milk", default=values["standalone_milk"]): bool,
                       vol.Required("cappuccino_enabled", default=values["cappuccino_enabled"]): bool,
                       vol.Required("cappuccino_match_mode", default=values["cappuccino_match_mode"]):
                           selector.SelectSelector(selector.SelectSelectorConfig(
                               options=["exact", "at_least"], mode=selector.SelectSelectorMode.DROPDOWN,
                               translation_key="cappuccino_match_mode")),
                       vol.Required("energy_enabled", default=values["energy_enabled"]): bool,
                       vol.Optional("energy_entity", default=values["energy_entity"]): str})
        schema.update({vol.Required(key, default=values[key]): selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=10, step=1, mode=selector.NumberSelectorMode.BOX))
            for key in ("cappuccino_milk_phases", "cappuccino_cups")})
        return self.async_show_form(step_id="features", data_schema=vol.Schema(schema), errors=errors)

    async def async_step_import_profile(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                if self._busy():
                    errors["base"] = "busy"
                else:
                    options = import_profile(self._current_values(), user_input["profile_json"], user_input["import_name"])
                    return self.async_create_entry(title="", data=options)
            except (ValueError, TypeError):
                errors["base"] = "invalid_profile_import"
        return self.async_show_form(step_id="import_profile", errors=errors, data_schema=vol.Schema({
            vol.Required("import_name"): str,
            vol.Required("profile_json"): selector.TextSelector(selector.TextSelectorConfig(multiline=True))}))

    async def async_step_export_profile(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=self._current_values())
        return self.async_show_form(step_id="export_profile", data_schema=vol.Schema({}),
            description_placeholders={"profile_json": export_profile(self._current_values())})

    async def async_step_espresso(self, user_input=None):
        errors = {}
        if user_input is not None:
            self._current_values().update(user_input)
            if self._validate_values():
                return await self.async_step_milk()
            errors["base"] = "invalid_options"
        return self.async_show_form(step_id="espresso", data_schema=_options_schema(_ESPRESSO_OPTIONS, self._current_values()), errors=errors)

    async def async_step_milk(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Tune estimated milk/steam phases and save both steps together."""
        errors: dict[str, str] = {}
        values = self._current_values()
        if user_input is not None:
            values.update(user_input)
            if self._validate_values():
                coordinator = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
                if coordinator and coordinator.busy:
                    errors["base"] = "busy"
                else:
                    try:
                        values = self._current_values()
                        return self.async_create_entry(title="", data=save_thresholds(values, values["profile"], values))
                    except ValueError:
                        errors["base"] = "invalid_profile"
            else:
                errors["base"] = "invalid_options"
        return self.async_show_form(
            step_id="milk",
            data_schema=_options_schema(_MILK_OPTIONS, self._current_values()),
            errors=errors,
        )
