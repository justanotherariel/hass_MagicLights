# Installation (manual)

1. Using the tool of choice open the directory (folder) for your HA configuration (where you find `configuration.yaml`).
2. If you do not have a `custom_components` directory (folder) there, you need to create it.
3. In the `custom_components` directory (folder) create a new folder called `magic_lights`.
4. Download _all_ the files from the `custom_components/magic_lights/` directory (folder) in this repository.
5. Place the files you downloaded in the new directory (folder) you created.
6. Restart Home Assistant
7. Configure Magic Lights in your configuration.yaml.

# Installation (HACS)

This integration is not (yet) part of the HACS default store, so you need to add this repository as a custom repository first:

1. If you don't have HACS installed yet: [install HACS][install_hacs_doc]
2. Open HACS in your Home Assistant instance
3. Click the three-dot menu in the top right corner and select "Custom repositories"
4. Paste `https://github.com/justanotherariel/hass_MagicLights` as the repository, select type "Integration", and click "Add"
5. Now search for "Magic Lights" in HACS and open it
6. Click "Download"
7. Restart Home Assistant


<!-- External Links to Docs -->
[install_hacs_doc]: https://hacs.xyz/docs/use/download/download/