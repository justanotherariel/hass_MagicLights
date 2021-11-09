from custom_components.magic_lights.setup_tasks.task import SetupTask
from custom_components.magic_lights.setup_tasks.create_living_space import (
    Scene,
    Zone,
    Pipe,
)
from custom_components.magic_lights.magicbase.share import get_magic


def create_off(zone: Zone) -> Scene:
    scene = Scene()

    scene.name = "off"
    scene.zone = zone

    pipe = Pipe()
    pipe.entities = ["light.all"]
    pipe.modifier_conf = {}
    pipe.effect_conf = {"name": "off"}
    pipe.scene = scene

    scene.pipes.append(pipe)

    return scene


def create_custom(zone: Zone) -> Scene:
    scene = Scene()

    scene.name = "off"
    scene.zone = zone

    pipe = Pipe()
    pipe.entities = ["light.all"]
    pipe.modifier_conf = {}
    pipe.effect_conf = {"name": "custom"}
    pipe.scene = scene

    scene.pipes.append(pipe)

    return scene


class Task(SetupTask):
    def __init__(self) -> None:
        self.magic = get_magic()

        self.stage = 1

    async def execute(self):

        # TODO Check if "off" and "custom" already exists
        for _, zone in self.magic.living_space.items():
            zone.scenes.update({"off": create_off(zone)})
            zone.scenes.update({"custom": create_custom(zone)})
