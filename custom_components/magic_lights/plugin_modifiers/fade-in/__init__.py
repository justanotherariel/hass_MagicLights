from custom_components.magic_lights.plugin_modifiers import Modifier

CONFIG_SCHEMA = None
PLUGIN_DOMAIN = "fade_in"
PLUGIN_NAME = "Fade In"
PLUGIN_DESCRIPTION = "Adjusts the brightness of an effect continuously."


class FadeIn(Modifier):
    def __init__(self, async_call_service, entities, conf):
        super().__init__(async_call_service, entities, conf)

        self.log.info("Modifier Fade In Initialized")

    async def async_start(self):
        self.log.info(f"Fade-In Modifier started. \nConfig: {self.conf}")

    def update(self, domain: str, service: str, service_data: dict):
        self.log.info(f"Fade-In Mod: {domain}, {service}, {service_data}")

        return domain, service, service_data
