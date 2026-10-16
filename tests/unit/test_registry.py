"""The effect registry, its catalogue and late registration through api.py."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
import unittest

import voluptuous as vol

from custom_components.magic_lights import api
from custom_components.magic_lights.const import SIGNAL_EFFECTS
from custom_components.magic_lights.effects import (
    BUILTIN,
    EffectInfo,
    Registry,
    async_get_registry,
)
from custom_components.magic_lights.effects.base import Effect, RenderContext
from custom_components.magic_lights.effects.schema import PCT, serialize_schema
from custom_components.magic_lights.models import OFF, LightState
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .fakes import PARAM_TABLE

INFO_KEYS = {
    "key",
    "name",
    "description",
    "icon",
    "category",
    "dynamic",
    "source",
    "schema",
    "defaults",
}


class Strobe(Effect):
    key = "test_strobe"
    name = "Strobe"
    dynamic = True
    schema = vol.Schema({vol.Required("brightness", default=80): PCT})

    def render(self, ctx: RenderContext) -> dict[str, LightState]:
        return {light.entity_id: OFF for light in ctx.lights}


def fake_hass() -> Any:
    return SimpleNamespace(data={}, verify_event_loop_thread=lambda _name: None)


class RegistryTest(unittest.TestCase):
    def test_builtins(self) -> None:
        registry = Registry(BUILTIN)
        catalogue = registry.catalogue()
        self.assertEqual(
            [info.key for info in catalogue],
            ["off", "white", "static", "palette", "light_effect", "adaptive"],
        )
        for info in catalogue:
            with self.subTest(info.key):
                data = info.to_dict()
                self.assertEqual(set(data), INFO_KEYS)
                self.assertEqual(data["source"], "builtin")
                self.assertIsInstance(data["schema"], list)
                json.dumps(data)
                effect = registry.get(info.key)
                self.assertIs(registry.get(info.key), effect)
                self.assertEqual(effect.validate({}), effect.validate(data["defaults"]))
        self.assertEqual(
            {i.key: i.dynamic for i in catalogue},
            {
                "off": False,
                "white": False,
                "static": False,
                "palette": True,
                "light_effect": True,
                "adaptive": False,
            },
        )
        self.assertIsNone(registry.get("strobe"))

    def test_param_table(self) -> None:
        """The built-in schemas carry exactly the PARAM_TABLE keys and defaults."""
        for info in Registry(BUILTIN).catalogue():
            with self.subTest(info.key):
                table = serialize_schema(PARAM_TABLE[info.key])
                self.assertEqual(
                    [f["name"] for f in info.schema], [f["name"] for f in table]
                )
                for ours, frozen in zip(info.schema, table, strict=True):
                    self.assertEqual(
                        (ours["required"], ours.get("default"), list(ours["selector"])),
                        (
                            frozen["required"],
                            frozen.get("default"),
                            list(frozen["selector"]),
                        ),
                        ours["name"],
                    )
                    for limit in ("min", "max", "options", "custom_value"):
                        (config,) = ours["selector"].values()
                        (frozen_config,) = frozen["selector"].values()
                        self.assertEqual(
                            config.get(limit), frozen_config.get(limit), ours["name"]
                        )

    def test_labels_are_lifted(self) -> None:
        palette = {f["name"]: f for f in Registry(BUILTIN).catalogue()[3].schema}
        self.assertEqual(palette["palette"]["label"], "Colours")
        self.assertIn("▶", palette["speed"]["helper"])
        self.assertNotIn("description", palette["speed"])

    def test_to_dict_is_a_copy(self) -> None:
        info = Registry(BUILTIN).catalogue()[3]
        self.assertIsInstance(info, EffectInfo)
        data = info.to_dict()
        data["defaults"]["palette"].append({"kelvin": 2000})
        data["schema"][0]["selector"]["magic_lights_palette"]["max"] = 1
        again = info.to_dict()
        self.assertEqual(len(again["defaults"]["palette"]), 1)
        self.assertEqual(
            again["schema"][0]["selector"]["magic_lights_palette"]["max"], 9
        )

    def test_duplicate_key(self) -> None:
        registry = Registry(BUILTIN)
        with self.assertRaises(ValueError):
            registry.add(BUILTIN[1])
        registry.add(Strobe, source="other")
        with self.assertRaises(ValueError):
            registry.add(Strobe)
        self.assertEqual(registry.catalogue()[-1].source, "other")

    def test_rejects_non_selector_params(self) -> None:
        schemas = (
            vol.Schema({vol.Required("n", default=1): vol.Coerce(int)}),
            vol.Schema({vol.Required("n"): vol.All(PCT, vol.Coerce(int))}),
            vol.Schema({"plain": PCT}),
            vol.Schema({vol.Required("nested"): {vol.Required("x"): PCT}}),
            vol.Schema(vol.All({vol.Required("x"): PCT})),
            {vol.Required("x"): PCT},
        )
        for schema in schemas:
            with self.subTest(schema=schema):
                cls = type("Bad", (Strobe,), {"key": "bad", "schema": schema})
                registry = Registry()
                with self.assertRaises(ValueError):
                    registry.add(cls)
                self.assertIsNone(registry.get("bad"))

    def test_rejects_bad_classes(self) -> None:
        registry = Registry()
        for attrs in ({"key": "Bad-Key"}, {"key": ""}, {"name": ""}):
            with self.subTest(attrs), self.assertRaises(ValueError):
                registry.add(type("Bad", (Strobe,), attrs))
        with self.assertRaises(TypeError):
            registry.add(object)  # type: ignore[arg-type]
        self.assertEqual(registry.catalogue(), [])

    def test_unserialisable_schema_degrades_to_json(self) -> None:
        class Broken(selector.Selector):
            selector_type = "broken"
            CONFIG_SCHEMA = selector.make_selector_config_schema()

            def __call__(self, data: Any) -> Any:
                return data

            def serialize(self) -> dict[str, Any]:
                raise ValueError("cannot serialise")

        cls = type(
            "Odd",
            (Strobe,),
            {"key": "odd", "schema": vol.Schema({vol.Required("x"): Broken()})},
        )
        registry = Registry()
        with self.assertLogs(
            "custom_components.magic_lights.effects.schema", "WARNING"
        ):
            registry.add(cls)
        (info,) = registry.catalogue()
        self.assertIsNone(info.schema)
        self.assertEqual(info.defaults, {})


class RegisterEffectTest(unittest.TestCase):
    def test_late_registration_signals(self) -> None:
        hass = fake_hass()
        calls: list[tuple[Any, ...]] = []

        @callback
        def changed(*args: Any) -> None:
            calls.append(args)

        async_dispatcher_connect(hass, SIGNAL_EFFECTS, changed)
        registry = async_get_registry(hass)
        self.assertIs(async_get_registry(hass), registry)
        self.assertEqual(len(registry.catalogue()), len(BUILTIN))

        api.async_register_effect(hass, Strobe)
        self.assertEqual(calls, [()])
        self.assertIsInstance(registry.get("test_strobe"), Strobe)
        info = registry.catalogue()[-1]
        self.assertEqual(
            (info.key, info.source, info.dynamic), ("test_strobe", "tests", True)
        )

        with self.assertRaises(ValueError):
            api.async_register_effect(hass, Strobe)
        self.assertEqual(calls, [()])

    def test_registration_before_setup(self) -> None:
        hass = fake_hass()
        api.async_register_effect(hass, Strobe)
        keys = [info.key for info in async_get_registry(hass).catalogue()]
        self.assertEqual(keys, [cls.key for cls in BUILTIN] + ["test_strobe"])

    def test_source(self) -> None:
        for module, source in (
            ("custom_components.strobe.effects", "strobe"),
            ("homeassistant.components.demo.light", "demo"),
            ("my_plugin", "my_plugin"),
        ):
            with self.subTest(module):
                cls = type("Mod", (Strobe,), {"__module__": module})
                self.assertEqual(api._source(cls), source)

    def test_exports(self) -> None:
        for name in api.__all__:
            self.assertTrue(hasattr(api, name), name)


if __name__ == "__main__":
    unittest.main()
