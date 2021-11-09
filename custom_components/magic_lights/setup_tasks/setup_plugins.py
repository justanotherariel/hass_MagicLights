from __future__ import annotations

from custom_components.magic_lights.magicbase.plugin_manager import PluginManager
from custom_components.magic_lights.magicbase.share import get_magic

from custom_components.magic_lights.setup_tasks.task import SetupTask


class Task(SetupTask):
    def __init__(self) -> None:
        self.magic = get_magic()

        self.stage = 2

    async def execute(self):
        if not self.magic.plugin_manager:
            self.magic.plugin_manager = PluginManager()

        self.magic.plugin_manager.init_scenes()
