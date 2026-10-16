"""Config flow for Magic Lights: one entry, all settings live in the panel."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DOMAIN

TITLE = "Magic Lights"


class MagicLightsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create the single config entry; HA aborts a second one (single_config_entry)."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title=TITLE, data={})
        return self.async_show_form(step_id="user")
