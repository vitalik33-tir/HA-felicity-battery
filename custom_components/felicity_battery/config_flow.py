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
    CONF_DEVICE_TYPE,
    DEVICE_TYPE_BATTERY,
    DEVICE_TYPE_INVERTER,
)

_LOGGER = logging.getLogger(__name__)


class FelicityConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Felicity local devices (battery / inverter)."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle the initial step where user selects host/port and device type."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]
            device_type = user_input[CONF_DEVICE_TYPE]

            # Allow separate entries for the same host:port but different device types
            await self.async_set_unique_id(f"{host}:{port}:{device_type}")
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=user_input[CONF_NAME],
                data={
                    CONF_NAME: user_input[CONF_NAME],
                    CONF_HOST: host,
                    CONF_PORT: port,
                    CONF_DEVICE_TYPE: device_type,
                },
            )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default="Felicity"): str,
                vol.Required(
                    CONF_DEVICE_TYPE, default=DEVICE_TYPE_BATTERY
                ): vol.In(
                    {
                        DEVICE_TYPE_BATTERY: "battery",
                        DEVICE_TYPE_INVERTER: "inverter",
                    }
                ),
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )
