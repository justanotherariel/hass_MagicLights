import logging
from custom_components.magic_lights.magicbase.share import get_magic
from custom_components.magic_lights.const import DOMAIN
from homeassistant.components.http.view import HomeAssistantView
from aiohttp import web
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from custom_components.magic_lights.setup_tasks.task import SetupTask


_LOGGER = logging.getLogger(__name__)

URL_BASE = "/magiclights_files"


class MagicLigtsFrontend(HomeAssistantView):
    """Dev View Class for MagicLights."""

    requires_auth = False
    name = "magicligths_files:frontend"
    url = r"/magiclights_files/frontend/{requested_file:.+}"

    async def get(self, request, requested_file):
        """Handle MagicLights Web requests."""
        magic = get_magic()

        requested = requested_file.split("/")[-1]
        request = await magic.session.get(f"{URL_BASE}/{requested}")
        if request.status == 200:
            result = await request.read()
            response = web.Response(body=result)
            response.headers["Content-Type"] = "application/javascript"

            return response


def setup_frontend():
    magic = get_magic()

    magic.session = async_create_clientsession(magic.hass)

    dev_mode = True

    if dev_mode:
        _LOGGER.warning("Frontend development mode enabled. Do not run in production!")
        magic.hass.http.register_view(MagicLigtsFrontend())
    else:
        magic.hass.http.register_static_path(
            f"{URL_BASE}/frontend", None, cache_headers=False
        )

    magic.hass.components.frontend.async_register_built_in_panel(
        component_name="custom",
        sidebar_title="Magic Lights",
        sidebar_icon="mdi:lightbulb-on-outline",
        frontend_url_path=DOMAIN,
        config={
            "_panel_custom": {
                "name": "hacs-frontend",
                "embed_iframe": True,
                "trust_external": False,
                "js_url": "/magiclights_files/frontend/entrypoint.js",
            }
        },
        require_admin=True,
    )


# TODO
class Task(SetupTask):
    def __init__(self) -> None:
        self.magic = get_magic()

        self.stage = 2

    async def execute(self):
        pass
