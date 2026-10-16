# Magic Lights

[![GitHub Release][releases-shield]][releases]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]](LICENSE)

[![hacs][hacs-shield]][hacs]
[![Project Maintenance][maintenance-shield]][user_profile]

Scenes for whole rooms, the way the Hue app does them, for every light in Home Assistant.

- **Every area with lights is a room.** It gets a scene select (`select.living_room_scene`) on its area page, with Off, curated white, colour, candle and adaptive scenes, and Manual.
- **Zones** combine rooms ("Downstairs" = living room + kitchen), and each zone gets a select too.
- **48 built-in scenes**: Energize, Read, Relax, Nightlight, Savanna sunset, Tokyo, Cozy candle, Fireplace, Adaptive and more. Customise one for one room, or build your own from layers.
- **Hands off when you take over.** A lamp you dim in another app or switch off at the wall becomes *manual*: the scene stops sending to it until you reclaim it.
- **Adaptive lighting**: brightness and colour temperature follow the sun, and a room can start it when a light is switched on.
- **Dynamic colours**: palette scenes can drift through their colours, paced so your Zigbee network keeps up. Hue lights on Zigbee2MQTT run candle, fireplace and sunrise in firmware, and gradient strips show the whole palette.
- **A panel and a dashboard card** to run, customise and preview everything. No YAML.

Magic Lights only ever sends what changed. It restores its scenes after a restart without sending a single command.

## Requirements

Home Assistant 2026.8 or newer. Magic Lights has no Python requirements of its own.

## Installation

### HACS

Magic Lights is not in the HACS default store yet, so add it as a custom repository:

1. Open HACS, then the three-dot menu → **Custom repositories**.
2. Add `https://github.com/justanotherariel/hass_MagicLights` with the type **Integration**.
3. Search for **Magic Lights**, open it and click **Download**.
4. Restart Home Assistant.

### Manual

1. Download `magic_lights.zip` from the [latest release][releases]. It includes the built panel and card.
2. Unpack it into `<config>/custom_components/magic_lights/`.
3. Restart Home Assistant.

The repository itself has no built frontend; see [Development](#development) for building it.

## Quick start

1. **Settings → Devices & services → Add integration → Magic Lights.** There is nothing to configure.
2. Every area with lights now has a select named after it, for example `select.living_room_scene`. Pick **Relax**: the room fades to warm white in a second.
3. Open **Magic Lights** in the sidebar. The home screen lists your rooms and zones with what is running. Open a room to pick scenes, see its lights, and change its settings.
4. Add the card to a dashboard: **Edit dashboard → Add card → Magic Lights**.

## Concepts

### Rooms and zones

A **room** is a Home Assistant area with at least one light (the light's area, else its device's area). Light groups (HA groups, Hue rooms, Zigbee2MQTT and ZHA groups) are left out, so each bulb counts once. Hidden lights are included; disabled lights and lights with an entity category are not. Per room you can exclude lights and add lights that have no area.

A **zone** is a set of rooms plus extra lights, created in the panel (**New zone**). A light can be in several zones. A scene started on a zone runs on all its lights. The rooms then show it too, for example "Savanna sunset · via Downstairs".

Each light belongs to at most one running scene. Starting a scene on a room takes that room's lights from the zone scene, and the zone shows the parts of it that still run.

### Scenes

A scene is an ordered list of **layers**. Each layer runs one **effect** on some lights (all of them, chosen lights, or one room of a zone), and later layers win. A scene also has an overall brightness, a fade time, and what happens to lights no layer covers: they turn off, or they are left alone.

The effects are:

| Effect | What it does |
|---|---|
| Off | Turns its lights off |
| White | One colour temperature and brightness |
| Colour | One colour or white |
| Colour palette | Deals up to nine colours over the lights; static or moving |
| Light effect | A firmware effect (Hue: candle, fireplace, sunrise, …); candle and fireplace are emulated on other lights |
| Adaptive | Brightness and colour temperature follow the sun |

The **built-in scenes** cover whites, 35 colour palettes, two candle scenes and three adaptive ones. A room's menu shows a curated subset; the rest are one switch away in the room's settings, where you can also reorder the menu.

**Customise** a built-in scene in the editor and save it for this room or everywhere. The copy replaces the original under the same name and id, so automations keep working. **Reset** deletes the copy. **New scene** starts from scratch or from a copy. The editor previews the scene and can **Try** it on the room; **Stop trying** brings back what ran before.

### Manual lights and overrides

A light in a running scene becomes **manual** when you take over:

- a `light.turn_off`, a toggle, or a `light.turn_on` with brightness, colour or effect targets it, directly or through an area, device, label or group;
- it is switched off at the wall or in another app;
- it changes colour or brightness clearly on its own (**Detect changes**, per room; on by default);
- it is switched on while the scene wants it off.

The scene stops sending to manual lights. The select keeps showing the scene, and the card says "1 light manual" with a **Reclaim** button. Lights come back when you reclaim them, activate the scene again, or switch a manual light back on with a plain `light.turn_on` (**Reclaim on turn-on**, per room). When the last light of a scene is switched off, the scene ends and the select shows Off.

Magic Lights tells its own commands apart from yours by what it last sent, not by timing, so a wall switch wins even during a fast candle flicker.

### Adaptive

The **Adaptive** scenes follow the sun like [Adaptive Lighting](https://github.com/basnijholt/adaptive-lighting): bright and cool at noon, dim and warm in the evening, with optional sunrise and sunset clamps. They update every 90 seconds with a 45-second fade, at low priority.

To get adaptive light without touching anything, set **When a light is switched on** in the panel settings to Adaptive. A light switched on in a room that is off then gets Adaptive. Only that light: the others join as they are switched on. Each room can override this or turn it off.

### Dynamic scenes

Colour palettes play static by default. Press ▶ (panel or card), or call `set_dynamic` with `dynamic: true` on the running scene, and every light drifts through the palette on its own: a random phase, never through white, one fade per step. **Speed** sets the step length, from about 100 seconds down to 2.

### Buses and pacing

Lights on one radio network or bridge share its message budget. Magic Lights groups lights into **buses** (each Zigbee coordinator, Hue bridge, Z-Wave network, Thread network, Wi-Fi/IP device, …). Each bus has a message rate and a minimum interval per light. Scene changes always go first. Dynamic scenes share what is left and stretch their steps when a bus is busy, so a palette over 30 Zigbee bulbs slows down instead of flooding the network. The panel settings list the buses and let you raise or lower their limits, or move a light to another bus.

## Services

All services target a room or zone select. Scenes are given by id (`savanna_sunset`) or name (`Savanna sunset`).

| Service | Fields | What it does |
|---|---|---|
| `magic_lights.activate_scene` | `scene`, `brightness` 1–100, `dynamic`, `transition` 0–300 s | Runs a scene. Activating the running scene again reclaims manual lights and resends it. |
| `magic_lights.turn_off` | `transition` | Turns off every light of the room or zone. |
| `magic_lights.toggle_scene` | `scene`, `brightness`, `dynamic` | Off if this scene runs here, else activates it. |
| `magic_lights.next_scene` / `previous_scene` | `cycle` (default true) | Steps through the menu; Off comes after the last scene. |
| `magic_lights.adjust_brightness` | `step` −100–100 or `brightness` 1–100 | Changes the running scene's brightness. |
| `magic_lights.set_dynamic` | `dynamic` | Plays (true) or holds (false) the running scene's colours; manual lights stay manual. |
| `magic_lights.release` | | Stops the scene without changing the lights (Manual). |
| `magic_lights.reclaim` | `light` (optional) | Brings manual lights back into the scene. |

Brightness is that of the scene's brightest light, and the others keep their levels relative to it: 100 puts the brightest light at full brightness, even in a dim scene.

The select itself works too: `select.select_option` with a scene name, `Off` or `Manual`.

A remote, for example:

```yaml
- trigger: ...   # short press
  actions:
    - action: magic_lights.next_scene
      target:
        entity_id: select.living_room_scene
- trigger: ...   # dial
  actions:
    - action: magic_lights.adjust_brightness
      target:
        entity_id: select.living_room_scene
      data:
        step: -10
```

Each select's attributes carry the status: `status` (off, scene, manual, mixed), `scene_id`, `scene_name`, `via`, `manual` (lights you can reclaim), `brightness`, `dynamic`, and the state of each light (scene, manual, elsewhere, on, off, unavailable).

### Events

`magic_lights_event` fires with `type` `activated`, `ended`, `off`, `released`, `override` or `reclaimed`, plus `target_id`, `scene_id`, `entity_id` and `reason` where they apply. Use it to notify you when a lamp went manual, for example.

## Dashboard card

```yaml
type: custom:magic-lights-card
entity: select.living_room_scene   # required
name: Living room                  # optional
show_scenes: true
scenes: [relax, honolulu]          # optional: ids or names, hidden scenes too; default is the room's menu
columns: 3
show_lights: false
show_controls: true                # Off, Stop scene, ▶ and brightness −/+
```

The card is also in the card picker, with a visual editor. It reads everything from the select, so it works for non-admin users too.

## Upgrading from version 1

Version 2 is a rewrite and does not convert the v1 configuration. Remove the `magic_lights:` block from `configuration.yaml`, then set up your scenes in the panel. The v1 per-zone sensors are gone; use the selects instead.

## Limitations

- **Hue dimmers bound directly to bulbs** (Zigbee2MQTT binding) change the bulbs without Home Assistant noticing, because Zigbee2MQTT sets up no reporting for Hue lights. Magic Lights cannot see those changes. Bind remotes through Zigbee2MQTT or Home Assistant actions instead, or enable reporting.
- **Zigbee2MQTT Hue payloads** (native effects, gradients) are built from the Zigbee2MQTT sources and have not been checked on every model. Turn off **Native effects** in the settings if a light misbehaves; it then gets plain colour commands.
- **Light groups are not used for sending.** Each bulb gets its own commands; group calls by you are still recognised as overrides.
- **Animated gradients** in dynamic scenes need a newer Zigbee2MQTT and come in a later version. Gradient lights show the palette statically.
- **Bus limits are estimates.** Only the Zigbee figure has a source; the others are conservative. Raise them in the settings if your network keeps up.
- **The power-on flash** when a switched-on light is adopted by a default scene is the bulb's own power-on behaviour. Set the bulb's power-on behaviour to "previous" (Zigbee2MQTT, Hue app) if it bothers you.
- **Manual lights stay manual** until you reclaim them; there is no timeout yet.
- **Emulated candles and fireplaces** write a new light state every 1.5–3 s per light. Exclude those lights from the recorder if the history database grows too fast.

## Writing your own effects

Other integrations can add effects; they appear in the scene editor with a generated form. See [docs/plugins.md](docs/plugins.md).

## Development

The repository has a [devenv](https://devenv.sh) environment (`devenv shell`), or use any Python 3.14 environment with `pip install -r requirements.txt -r requirements_test.txt` (the second file has Home Assistant's frontend, which the end-to-end tests need) and Node.js 22.

```bash
scripts/build-frontend         # build the panel and card into custom_components/magic_lights/panel/
scripts/build-frontend --test  # frontend unit tests
scripts/test                   # Python unit tests, then the end-to-end tests on a real Home Assistant
scripts/lint                   # ruff
scripts/develop                # run Home Assistant with config/ (builds the frontend if needed)
```

The end-to-end tests boot Home Assistant with fake lights (`tests/harness.py`). `tests/screenshot.py OUT /magic_lights /lovelace/home` takes screenshots of the panel and the card with headless Chromium.

## Credits

The built-in scenes' colours come from [Hypfer/hass-scene_presets](https://github.com/Hypfer/hass-scene_presets) (Apache-2.0, see `custom_components/magic_lights/presets/NOTICE`). The adaptive curve follows [Adaptive Lighting](https://github.com/basnijholt/adaptive-lighting) (Apache-2.0).

## Contributions are welcome!

If you want to contribute, please read the [contribution guidelines](CONTRIBUTING.md).

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
