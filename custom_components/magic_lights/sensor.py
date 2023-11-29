from custom_components.magic_lights.data_structures.living_space import Zone
from custom_components.magic_lights.data_structures.magic import Magic
import logging

from homeassistant.helpers.entity import Entity
from custom_components.magic_lights.helpers.entity_id import substitute_group_names

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

    _magic: Magic = hass.data[DOMAIN]

    new_devices = []
    for zone in _magic.living_space.values():
        new_devices.append(ZoneSensor(zone))

    if new_devices:
        add_entities(new_devices)


class ZoneSensor(Entity):
    """Represenation of the active scene within a zone."""

    should_poll = False

    def __init__(self, zone: Zone):
        """Initialize the sensor."""
        self._zone = zone

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
        if self._zone.current_scene is None:
            return "Initializing"

        return self._zone.current_scene

    @property
    def state_attributes(self):
        """Return the state attributes of the device."""
        return {
            "available scenes": list(self._zone.scenes.keys()),
            "entities": substitute_group_names(self._zone.entities, self._zone),
            "disabled entities": self._zone.disabled_entities,
        }

    @property
    def available(self) -> bool:
        return True

    async def async_added_to_hass(self):
        """Run when this Entity has been added to HA."""

        def callback_wrapper():
            # Hack for Updating state attributes in HASS Frontend.
            # TODO Check if a proper solution is available.
            current_state = self.state
            self._zone.current_scene = "Updating Attributes"
            self.async_write_ha_state()
            self._zone.current_scene = current_state
            self.async_write_ha_state()

        self._zone.zone_sensor_callback = callback_wrapper

    async def async_will_remove_from_hass(self):
        """Entity being removed from hass."""
        # The opposite of async_added_to_hass. Remove any registered call backs here.
        # self._roller.remove_callback(self.async_write_ha_state)
