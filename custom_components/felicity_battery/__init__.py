from __future__ import annotations
# -*- coding: utf-8 -*-

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import (
    BASIC_INFO_REFRESH_SECONDS,
    FelicityApiError,
    FelicityClient,
    async_get_data_bounded,
)
from .validation import has_hardware_alarm, sanitize_runtime, validate_runtime
from .const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PLATFORMS,
)
_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up via YAML (not used)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Felicity entry from config entry."""

    host: str = entry.data["host"]
    port: int = entry.data["port"]

    model = entry.options.get("rated_model") or entry.data.get("model")

    def _validate_runtime(data: dict) -> None:
        # Keep hardware alarm registers intact while individually masking
        # corrupted physical channels. The original parsed frame is detached,
        # so no data is modified during parsing or other clients' reads.
        cleaned = sanitize_runtime(data, configured_model=model)
        result = validate_runtime(cleaned, configured_model=model)
        # Validation remains strict even when alarm bits are raised. A
        # partial *sanitized* alarm frame may still be published so the
        # existing fault/warning sensors see the hardware alert.
        if not result.valid and not has_hardware_alarm(cleaned):
            raise FelicityApiError(
                f"Invalid {result.reason} in FLA48200-P realtime telemetry"
            )
        if cleaned is not data:
            data.clear()
            data.update(cleaned)

    # Only the verified FLA48200-P profile may reuse the static basic-info
    # response. Other models keep the exact original four-command cadence.
    cache_interval = (
        BASIC_INFO_REFRESH_SECONDS
        if isinstance(model, str) and model.strip().upper() == "FLA48200-P"
        else None
    )
    client = FelicityClient(
        host,
        port,
        runtime_validator=_validate_runtime,
        basic_info_refresh_seconds=cache_interval,
        preserve_alarm_frames=cache_interval is not None,
        enforce_optional_budget=cache_interval is not None,
    )

    async def _async_update_data():
        try:
            return await async_get_data_bounded(client)
        except FelicityApiError as err:
            raise UpdateFailed(str(err)) from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"{DOMAIN}_{host}",
        update_method=_async_update_data,
        update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
    )

    await coordinator.async_config_entry_first_refresh()
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
            }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok and DOMAIN in hass.data:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok

async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload only this battery when rated model/capacity is configured."""
    await hass.config_entries.async_reload(entry.entry_id)
