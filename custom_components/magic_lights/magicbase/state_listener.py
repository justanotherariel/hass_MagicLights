import logging
from homeassistant.core import Event, State, callback
from homeassistant.const import EVENT_CALL_SERVICE

from custom_components.magic_lights.magicbase.share import get_magic

_LOGGER = logging.getLogger(__name__)


class StateListener:
    def __init__(self):
        self.magic = get_magic()
        self.log = logging.getLogger(__name__)

        self.accepted_domains = ["light"]

        # Check for non-MagicLights changes.
        self.magic.hass.bus.async_listen(
            EVENT_CALL_SERVICE, self.call_listener, self.call_event_filter
        )

        # Check if all entities in a zone are off.
        self.magic.hass.bus.async_listen("state_changed", self.state_listener)

    async def call_listener(self, event: Event):
        if self.magic.context_manager.context_ours(
            event.context
        ):
            return

        event_entity_id = event.data["service_data"]["entity_id"]

        for zone in self.magic.living_space.values():
            if event_entity_id in zone.entities:
                if zone.current_scene == "custom":
                    return
                if event_entity_id not in zone.disabled_entities:
                    self.log.debug("Disabling %s", event_entity_id)
                    zone.disabled_entities.append(event_entity_id)
                    zone.zone_sensor_callback()  # Update Sensor
                break

    @callback
    def call_event_filter(self, event: dict) -> bool:
        return event[
            "domain"
        ] in self.accepted_domains

    async def state_listener(self, event: Event):
        # Find Zone for entity
        for zone in self.magic.living_space.values():
            if event.data["entity_id"] in zone.entities:
                # Check if all entities in zone are off
                if event.data["new_state"].state == "off":
                    zone_on = False
                    for entity in zone.entities:
                        state: State = self.magic.hass.states.get(entity)
                        if state.state == "on":
                            zone_on = True
                            break

                    # Start "off" scene for zone
                    if not zone_on:
                        self.log.debug(
                            "All Entities off, changing scene to off. %s", zone.name
                        )
                        self.magic.scene_manager.init_scene(zone.name, "off")

                # Check if zone is not "off" anymore and switch to custom
                if (
                    event.data["new_state"].state == "on"
                    and zone.current_scene == "off"
                ):
                    self.log.debug(
                        "Entity turned on, changing scene to custom. %s", zone.name
                    )
                    self.magic.scene_manager.init_scene(zone.name, "custom")

                break
