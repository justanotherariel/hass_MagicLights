import logging

from homeassistant.helpers.entity import Entity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# See cover.py for more details.
# Note how both entities for each roller sensor (battry and illuminance) are added at
# the same time to the same list. This way only a single async_add_devices call is
# required.
async def async_setup_platform(hass, config, add_entities, discovery_info=None):
    """Add sensors in HA."""

    if discovery_info is None:
        return

    living_space = hass.data[DOMAIN]["living_space"]

    new_devices = []
    for zone in living_space.zones:
        new_devices.append(ZoneSensor(zone))

    if new_devices:
        add_entities(new_devices)


class ZoneSensor(Entity):
    """Represenation of the active scene within a zone."""

    should_poll = False

    def __init__(self, zone):
        """Initialize the sensor."""
        self._zone = zone

        self._available_scenes = []
        for scene_name in zone.scenes_config:
            self._available_scenes.append(scene_name)

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._zone.name

    @property
    def unique_id(self):
        """Return Unique ID string."""
        return f"{self._zone.name}_scene"

    @property
    def device_info(self):
        pass

    @property
    def state(self):
        """Return the state of the sensor."""
        if self._zone.current_scene == None:
            return "Initializing"

        return self._zone.current_scene.name

    @property
    def device_state_attributes(self):
        """Return the state attributes of the device."""
        attr = {}
        attr["available scenes"] = self._available_scenes
        attr["entities"] = self._zone.entity_controller.entity_id_list
        attr["disabled entities"] = self._zone.entity_controller.disabled_entities

        return attr

    @property
    def available(self) -> bool:
        return True

    async def async_added_to_hass(self):
        """Run when this Entity has been added to HA."""
        self._zone.add_callback(self.async_write_ha_state)

    async def async_will_remove_from_hass(self):
        """Entity being removed from hass."""
        # The opposite of async_added_to_hass. Remove any registered call backs here.
        # self._roller.remove_callback(self.async_write_ha_state)
        pass
