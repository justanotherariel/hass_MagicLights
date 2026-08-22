# Magic Lights

[![GitHub Release][releases-shield]][releases]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]](LICENSE)

[![hacs][hacs-shield]][hacs]
[![Project Maintenance][maintenance-shield]][user_profile]

**This component will set up the following platforms.**

Platform | Description
-- | --
`sensor` | Create a sensor for every room and display current scene.

## Installation (manual)

1. Using the tool of choice open the directory (folder) for your HA configuration (where you find `configuration.yaml`).
2. If you do not have a `custom_components` directory (folder) there, you need to create it.
3. In the `custom_components` directory (folder) create a new folder called `magic_lights`.
4. Download _all_ the files from the `custom_components/magic_lights/` directory (folder) in this repository.
5. Place the files you downloaded in the new directory (folder) you created.
6. Restart Home Assistant
7. Configure Magic Lights in your configuration.yaml as described below.

## Installation (HACS)

This integration is not (yet) part of the HACS default store, so you need to add this repository as a custom repository first:

1. If you don't have HACS installed yet: [install HACS][install_hacs_doc]
2. Open HACS in your Home Assistant instance
3. Click the three-dot menu in the top right corner and select "Custom repositories"
4. Paste `https://github.com/justanotherariel/hass_MagicLights` as the repository, select type "Integration", and click "Add"
5. Now search for "Magic Lights" in HACS and open it
6. Click "Download"
7. Restart Home Assistant

## Configuration is done in the configuration.yaml file

Since this configuration can be quite complex, I recommend [splitting it up into multiple files][splitting_yaml].

An example configuration.yaml entry:

```yaml
TODO
```

## Contributions are welcome!

If you want to contribute to this please read the [Contribution guidelines](CONTRIBUTING.md)

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

<!-- External Links to Docs -->
[install_hacs_doc]: https://hacs.xyz/docs/use/download/download/
[splitting_yaml]: https://www.home-assistant.io/docs/configuration/splitting_configuration/