"""Serve the frontend bundles; register the panel and the dashboard card."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from homeassistant.components import panel_custom
from homeassistant.components.frontend import (
    add_extra_js_url,
    async_panel_exists,
    async_remove_panel,
)
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN

URL_BASE: Final = "/magic_lights_static"
BUNDLE_DIR: Final = Path(__file__).parent / "panel"


async def async_register_static(hass: HomeAssistant, version: str) -> None:
    """Serve the bundles and load the card on every dashboard (once per run)."""
    await hass.http.async_register_static_paths(
        # The version query busts browser caches on upgrades.
        [StaticPathConfig(URL_BASE, str(BUNDLE_DIR), cache_headers=False)]
    )
    add_extra_js_url(hass, f"{URL_BASE}/magic-lights-card.js?v={version}")


async def async_register_panel(hass: HomeAssistant, version: str) -> None:
    if async_panel_exists(hass, DOMAIN):
        return
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=DOMAIN,
        webcomponent_name="magic-lights-panel",
        sidebar_title="Magic Lights",
        sidebar_icon="mdi:lightbulb-auto",
        module_url=f"{URL_BASE}/magic-lights-panel.js?v={version}",
        embed_iframe=False,
        handle_safe_area=True,
        require_admin=True,
        config_panel_domain=DOMAIN,
    )


@callback
def async_unregister_panel(hass: HomeAssistant) -> None:
    async_remove_panel(hass, DOMAIN, warn_if_unknown=False)
