[![GitHub Release][releases-shield]][releases]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]][license]

[![hacs][hacs-shield]][hacs]
[![Project Maintenance][maintenance-shield]][user_profile]

# Magic Lights

Scenes for whole rooms, the way the Hue app does them, for every light in Home Assistant.

- Every area with lights becomes a room with a scene select: Off, curated whites and colours, candle, fireplace, adaptive, and Manual.
- Zones combine rooms; 48 built-in scenes can be customised per room, or build your own from layers.
- Lamps you change by hand or switch off at the wall become manual and are left alone until you reclaim them.
- Adaptive lighting that follows the sun, optionally started when a light is switched on in a room that is off.
- Dynamic colour palettes paced to your Zigbee network; native candle, fireplace and gradients for Hue lights on Zigbee2MQTT.
- A sidebar panel with a scene editor and live preview, and a dashboard card. No YAML.

Requires Home Assistant 2026.8 or newer.

{% if not installed %}
## Installation

1. Click **Download** and restart Home Assistant.
1. **Settings → Devices & services → Add integration → Magic Lights.**
1. Open **Magic Lights** in the sidebar.
{% endif %}

Upgrading from version 1? Version 2 does not convert the old configuration; see the [README][readme].

***

<!-- Shields -->
[releases-shield]: https://img.shields.io/github/release/justanotherariel/hass_MagicLights.svg?style=for-the-badge
[releases]: https://github.com/justanotherariel/hass_MagicLights/releases

[commits-shield]: https://img.shields.io/github/commit-activity/y/justanotherariel/hass_MagicLights.svg?style=for-the-badge
[commits]: https://github.com/justanotherariel/hass_MagicLights/commits/main

[license-shield]: https://img.shields.io/github/license/justanotherariel/hass_MagicLights.svg?style=for-the-badge

[hacs-shield]: https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge
[hacs]: https://github.com/hacs/integration

[maintenance-shield]: https://img.shields.io/badge/maintainer-Ariel%20Ebersberger%20%40justanotherariel-blue.svg?style=for-the-badge
[user_profile]: https://github.com/justanotherariel

[license]: https://github.com/justanotherariel/hass_MagicLights/blob/main/LICENSE
[readme]: https://github.com/justanotherariel/hass_MagicLights#upgrading-from-version-1
