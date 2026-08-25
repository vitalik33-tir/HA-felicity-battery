from __future__ import annotations
# -*- coding: utf-8 -*-

from typing import Any
import logging

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.data_entry_flow import FlowResult

from .const import (
    DEFAULT_PORT,
    DOMAIN,
)
_LOGGER = logging.getLogger(__name__)

CONF_MODEL = "model"


class FelicityConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Felicity Battery local device."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle the initial step where user selects host/port."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]

            # Don't allow duplicates for the same host:port
            for existing in self._async_current_entries():
                if existing.data.get(CONF_HOST) == host and existing.data.get(CONF_PORT) == port:
                    return self.async_abort(reason="already_configured")
            # Allow separate entries for the same host:port but different device types
            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=user_input[CONF_NAME],
                data={
                    CONF_NAME: user_input[CONF_NAME],
                    CONF_HOST: host,
                    CONF_PORT: port,
                    # The device's own local API never reports a
                    # human-readable model string (only numeric Type/
                    # SubType codes), so we can't detect e.g. "FLA48200"
                    # or "LUX-X-96050HG01" automatically. Let the user
                    # type theirs in; empty is fine, we fall back to a
                    # generic label in device_info.
                    CONF_MODEL: user_input.get(CONF_MODEL, "").strip(),
                },
            )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default="Felicity"): str,
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                vol.Optional(CONF_MODEL, default=""): str,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "model_hint": (
                    "Optional: the model printed on your battery/label "
                    "(e.g. LUX-X-96050HG01). The device itself doesn't "
                    "report this, so it's only used for display."
                )
            },
        )
