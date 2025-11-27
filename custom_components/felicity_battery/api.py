from __future__ import annotations

import asyncio
import json
import logging
import re

_LOGGER = logging.getLogger(__name__)


class FelicityApiError(Exception):
    """Error while communicating with Felicity battery."""


class FelicityClient:
    """TCP client for Felicity battery local API."""

    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port

    async def async_get_data(self) -> dict:
        """Send command and get JSON response."""
        try:
            reader, writer = await asyncio.open_connection(self._host, self._port)

            
            writer.write(b"wifilocalMonitor:get dev real infor")
            await writer.drain()

            try:
                data = await asyncio.wait_for(reader.read(8192), timeout=3.0)
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass

        except Exception as err:
            raise FelicityApiError(
                f"Error talking to {self._host}:{self._port}: {err}"
            ) from err

        if not data:
            raise FelicityApiError("No data received from battery")

        text = data.decode("ascii", errors="ignore").strip()
        _LOGGER.debug("Raw Felicity response (before patch): %r", text)

        if text.startswith("{'") and '"' not in text:
            patched = text.replace("'", '"')
            _LOGGER.debug("Patched single quotes -> double quotes")
            text = patched
        if '"BTemp":' not in text and '"Bfault":' in text:
            patched = text.replace(
                '"Bfault":0[[', '"Bfault":0,"BTemp":[['
            )
            patched, n = re.subn(
                r'"Bfault"\s*:\s*([-0-9]+)\s*\[\[',
                r'"Bfault":\1,"BTemp":[[',
                patched,
            )
            if patched != text:
                _LOGGER.debug("Patched BTemp after Bfault (replacements=%s)", n)
                text = patched

        _LOGGER.debug("Felicity response (after patch): %r", text)
        try:
            parsed = json.loads(text)
        except Exception as err:
            raise FelicityApiError(f"Invalid JSON from battery: {text}") from err

        return parsed
