from custom_components.magic_lights.magicbase.context_manager import ContextManager
from custom_components.magic_lights.magicbase.share import get_magic
from custom_components.magic_lights.setup_tasks.task import SetupTask

class Task(SetupTask):
    def __init__(self) -> None:
        self.magic = get_magic()

        self.stage = 1

    async def execute(self):
        if not self.magic.context_manager:
            self.magic.context_manager = ContextManager()
