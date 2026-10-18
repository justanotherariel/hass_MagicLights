"""Effect parameter selectors and their serialisation for ha-form."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.helpers import config_validation as cv, selector

from ..models import Color

try:
    from voluptuous_serialize import convert as _to_field_list
except ImportError:  # HA >= 2026.10
    from probatio import to_field_list as _to_field_list

_LOGGER = logging.getLogger(__name__)


class ColorSelectorConfig(selector.BaseSelectorConfig, total=False):
    allow_white: bool
    allow_color: bool
    min_kelvin: int
    max_kelvin: int


class ColorSelector(selector.Selector[ColorSelectorConfig]):
    """One colour: {"kelvin"} or {"xy"}; also accepts {"rgb"} and "#rrggbb"."""

    selector_type = "magic_lights_color"
    CONFIG_SCHEMA = selector.make_selector_config_schema(
        {
            vol.Optional("allow_white", default=True): bool,
            vol.Optional("allow_color", default=True): bool,
            vol.Optional("min_kelvin", default=1000): int,
            vol.Optional("max_kelvin", default=10000): int,
        }
    )

    def __init__(self, config: ColorSelectorConfig | None = None) -> None:
        super().__init__(config)

    def __call__(self, data: Any) -> dict[str, Any]:
        try:
            color = Color.parse(data)
        except ValueError as err:
            raise vol.Invalid(str(err)) from err
        if color.kelvin is None:
            if not self.config["allow_color"]:
                raise vol.Invalid("only a colour temperature is allowed")
        elif not self.config["allow_white"]:
            raise vol.Invalid("a colour temperature is not allowed")
        elif not self.config["min_kelvin"] <= color.kelvin <= self.config["max_kelvin"]:
            raise vol.Invalid(
                f"{color.kelvin} K is outside {self.config['min_kelvin']}"
                f"..{self.config['max_kelvin']} K"
            )
        return color.to_dict()


class PaletteSelectorConfig(selector.BaseSelectorConfig, total=False):
    min: int
    max: int


class PaletteSelector(selector.Selector[PaletteSelectorConfig]):
    """An ordered list of colours."""

    selector_type = "magic_lights_palette"
    CONFIG_SCHEMA = selector.make_selector_config_schema(
        {vol.Optional("min", default=1): int, vol.Optional("max", default=9): int}
    )

    def __init__(self, config: PaletteSelectorConfig | None = None) -> None:
        super().__init__(config)

    def __call__(self, data: Any) -> list[dict[str, Any]]:
        if not isinstance(data, list | tuple):
            raise vol.Invalid("expected a list of colours")
        try:
            out = [Color.parse(c).to_dict() for c in data]
        except ValueError as err:
            raise vol.Invalid(str(err)) from err
        return vol.Length(min=self.config["min"], max=self.config["max"])(out)


PCT = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=1, max=100, step=1, unit_of_measurement="%", mode="slider"
    )
)


def KELVIN(lo: int = 1500, hi: int = 6500) -> selector.ColorTempSelector:  # noqa: N802
    return selector.ColorTempSelector(
        selector.ColorTempSelectorConfig(unit="kelvin", min=lo, max=hi)
    )


# Lifted from a key's description: ha-form's own keys, then layout hints the panel reads.
FIELD_KEYS = (
    "visible",
    "label",
    "helper",
    "hints",
    "advanced",
    "pair",
    "steps",
    "ends",
    "row",
)


def serialize_schema(schema: vol.Schema) -> list[dict[str, Any]] | None:
    """Return ha-form fields, or None when the schema cannot be serialised."""
    try:
        items = _to_field_list(schema, custom_serializer=cv.custom_serializer)
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Cannot serialise effect schema %s: %s", schema, err)
        return None
    for item in items:
        description = item.pop("description", None)
        if isinstance(description, dict):
            for key in FIELD_KEYS:
                if key in description:
                    item[key] = description[key]
    return items


def defaults_of(schema: vol.Schema) -> dict[str, Any]:
    """Return the defaults of the keys that declare one."""
    return {
        key.schema: key.default()
        for key in schema.schema
        if getattr(key, "default", vol.UNDEFINED) is not vol.UNDEFINED
    }
