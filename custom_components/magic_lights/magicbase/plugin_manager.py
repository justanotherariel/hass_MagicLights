from custom_components.magic_lights.helpers.entity_id import (
    substitute_group_names,
)
from custom_components.magic_lights.data_structures.living_space import Pipe
from custom_components.magic_lights.magicbase.share import get_magic
import importlib
import inspect
import pkgutil

from custom_components.magic_lights import plugin_effects, plugin_modifiers
from custom_components.magic_lights.data_structures.plugin import Plugin
from custom_components.magic_lights.helpers.service_call import create_async_call


class PluginManager:
    effect_descriptions: list[plugin_effects.Effect] = []
    modifier_descriptions: list[plugin_modifiers.Modifier] = []

    def __init__(self) -> None:
        self.magic = get_magic()

        self._fetch_plugins()

    def init_scenes(self) -> None:
        """Initialize all plugins defined in living_space."""

        for zone in self.magic.living_space.values():
            for scene in zone.scenes.values():
                for pipe in scene.pipes:
                    self._init_pipe(pipe)

    def _init_pipe(self, pipe: Pipe):
        """Initialize the plugins for a single pipe."""

        #  Setup Modifiers
        if pipe.modifier_conf:
            modifier_conf = list.copy(pipe.modifier_conf).reverse()

            for conf in modifier_conf:
                modifier_cls = self._get_modifier(conf["name"])
                pipe.modifiers.insert(
                    0,
                    modifier_cls(
                        create_async_call(pipe),
                        substitute_group_names(pipe.entities, pipe.scene.zone),
                        modifier_conf["conf"],
                    ),
                )

        # Setup Effects
        effect_cls = self._get_effect(pipe.effect_conf["name"])
        pipe.effect = effect_cls(
            create_async_call(pipe),
            substitute_group_names(pipe.entities, pipe.scene.zone),
            pipe.effect_conf.get("conf", None),
        )

    def reinit_scene(self, zone_name: str, scene_name: str) -> None:
        """Reinitializes the plugins of a specified scene."""
        # TODO Reinit Scene
        raise NotImplementedError

    def reload_plugin(self, type: str, name: str):
        """Fully reload a plugin and reinit all pipes with this plugin."""
        # TODO Reload Plugin
        raise NotImplementedError

    def _get_effect(self, effect_name):
        for effect in self.effect_descriptions:
            if effect.domain == effect_name:
                return effect.init

    def _get_modifier(self, modifier_name):
        for modifier in self.modifier_descriptions:
            if modifier.domain == modifier_name:
                return modifier.init

    def _fetch_plugins(self):
        packages = [
            (self.effect_descriptions, plugin_effects, plugin_effects.Effect),
            (self.modifier_descriptions, plugin_modifiers, plugin_modifiers.Modifier),
        ]

        for plugin_list, package, subclass in packages:
            namespace = pkgutil.iter_modules(package.__path__, package.__name__ + ".")

            for _, name, _ in namespace:
                module = importlib.import_module(name)

                clsmembers = inspect.getmembers(module, inspect.isclass)
                for (_, c) in clsmembers:
                    # Only add classes that are a sub class of Plugin, but NOT Plugin itself
                    # TODO: Handle effect/modifier has not attribute 'PLUGIN_DOMAIN', etc.
                    if issubclass(c, subclass) & (c is not subclass):
                        plugin_list.append(
                            Plugin(
                                domain=module.PLUGIN_DOMAIN,
                                name=module.PLUGIN_NAME,
                                description=module.PLUGIN_DESCRIPTION,
                                init=c,
                            )
                        )
