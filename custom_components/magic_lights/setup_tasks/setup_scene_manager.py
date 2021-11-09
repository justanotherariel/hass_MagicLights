from custom_components.magic_lights.magicbase.scene_manager import SceneManager
from custom_components.magic_lights.magicbase.share import get_magic
from custom_components.magic_lights.setup_tasks.task import SetupTask

# TODO
class Task(SetupTask):
    def __init__(self) -> None:
        self.magic = get_magic()

        self.stage = 1

    async def execute(self):
        if not self.magic.scene_manager:
            self.magic.scene_manager = SceneManager()
