[![GitHub Release][releases-shield]][releases]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]][license]

[![hacs][hacs-shield]][hacs]
[![Project Maintenance][maintenance-shield]][user_profile]


# Magic Lights
This integration will make setting up complex light scenes a breeze.

**This component will set up the following platforms.**

Platform | Description
-- | --
`sensor` | Create a sensor for every room and display current scene.

{% if not installed %}
## Installation

1. Click install.
1. Configure via configuration.yaml
{% endif %}


## Configuration is done in the configuration.yaml file

Use the following as a template. I will make a list of available scenes and modifiers asap, for now though, you will have to look into the ```custom_components/magic_lights/plugin_effects``` and ```custom_components/magic_lights/plugin_modifiers``` folders (Sorry!).

```yaml
magic_lights:
  bedroom:
    entities:
      - light.entrance_color_white_lights
      - light.ceiling_lights
      - light.kitchen_lights
      - light.bed_light

    groups:
      main_lights:
        - light.entrance_color_white_lights
        - light.ceiling_lights
        - light.kitchen_lights

    scenes:
      bright:
        - entities:
            - main_lights

          effect:
            name: static
            conf:
              preset: Bright

      dimmed:
        - entities:
            - light.bed_light

          effect:
            name: static
            conf:
              preset: Dimmed

      tokyo:
        - entities:
            - light.all

          effect:
            name: transitions
            conf:

              preset: pink-glow

              transition_color_space: CIELab
              transition_time: 30
              update_rate_s: 5
```

<!---->

***

<!-- Shields -->
[releases-shield]: https://img.shields.io/github/release/justanotherariel/hass_MagicLights.svg?style=for-the-badge
[releases]: https://github.com/custom-justanotherariel/hass_MagicLights/releases

[commits-shield]: https://img.shields.io/github/commit-activity/y/justanotherariel/hass_MagicLights.svg?style=for-the-badge
[commits]: https://github.com/custom-justanotherariel/hass_MagicLights/commits/master

[license-shield]: https://img.shields.io/github/license/justanotherariel/hass_MagicLights.svg?style=for-the-badge

[hacs-shield]: https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge
[hacs]: https://github.com/custom-components/hacs

[maintenance-shield]: https://img.shields.io/badge/maintainer-Ariel%20Ebersberger%20%40justanotherariel-blue.svg?style=for-the-badge
[user_profile]: https://github.com/justanotherariel

[license]: https://github.com/justanotherariel/hass_MagicLights/blob/main/LICENSE