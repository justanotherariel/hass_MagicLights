from custom_components.magic_lights.magicbase.state_listener import StateListener
from custom_components.magic_lights.magicbase.share import get_magic
from custom_components.magic_lights.setup_tasks.task import SetupTask

class Task(SetupTask):
    def __init__(self) -> None:
        self.magic = get_magic()

        self.stage = 3

    async def execute(self):
        if not self.magic.state_listener:
            self.magic.state_listener = StateListener()
