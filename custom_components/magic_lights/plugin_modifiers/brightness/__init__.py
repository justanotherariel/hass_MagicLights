from custom_components.magic_lights.plugin_modifiers import Modifier

CONFIG_SCHEMA = None
PLUGIN_DOMAIN = "brightness"
PLUGIN_NAME = "Brightness"
PLUGIN_DESCRIPTION = "Adjusts the brightness of an effect."


class Brightness(Modifier):
    def __init__(self, async_call_service, entities, conf):
        super().__init__(async_call_service, entities, conf)

        self.log.info("Modifier Brightness Initialized")

    async def async_start(self):
        self.log.info(f"Brightness Modifier started. \nConfig: {self.conf}")

    def update(self, domain: str, service: str, service_data: dict):
        self.log.info(f"Brightness Mod: {domain}, {service}, {service_data}")

        return domain, service, service_data
