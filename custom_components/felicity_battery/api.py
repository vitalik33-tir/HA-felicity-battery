from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict

_LOGGER = logging.getLogger(__name__)

# --- Tuning knobs for the TCP dialog with the Felicity Wi-Fi module -------
# The module answers some commands (e.g. "get dev set infor") with several
# concatenated JSON objects sent across multiple TCP segments. The original
# implementation stopped reading as soon as it saw the first "}", which
# works for single-object replies but silently truncates multi-object
# replies (dropping fields like wCVP80/wCVP20/cVolHi/cVolLo/bCCHi2/bDCHi2).
# It also only waited 0.5s for the very first byte with no retry, which
# made the whole update fail ("No data received from battery") whenever
# the module was a few hundred ms slower than usual (e.g. busy talking to
# the FSOLAR cloud at the same moment).
CONNECT_TIMEOUT = 5.0
FIRST_BYTE_TIMEOUT = 3.0
IDLE_TIMEOUT = 0.4
READ_RETRIES = 2
READ_RETRY_BACKOFF = 0.5


class FelicityApiError(Exception):
    """Error while communicating with Felicity battery."""


class FelicityClient:
    """TCP client for Felicity battery local API."""

    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port

    async def async_get_data(self) -> dict:
        """Send commands and combine all data into one dict.

        - wifilocalMonitor:get dev real infor   -> runtime telemetry
        - wifilocalMonitor:get dev basice infor -> versions / type
        - wifilocalMonitor:get dev set infor    -> config / limits (multi-json)
        """
        # 1. Runtime data (critical - if this fails, the whole update fails)
        real_raw = await self._async_read_raw_with_retry(
            b"wifilocalMonitor:get dev real infor"
        )
        real = self._parse_real_payload(real_raw)
        data: Dict[str, Any] = dict(real)

        # 2. Basic info (best effort)
        try:
            basic_raw = await self._async_read_raw_with_retry(
                b"wifilocalMonitor:get dev basice infor"
            )
            basic_text = basic_raw.replace("'", '"').strip()
            basic = json.loads(basic_text)
            data["_basic"] = basic
        except Exception as err:
            _LOGGER.debug("Failed to read basic info: %s", err)

        # 3. Settings / limits (best effort, multiple JSON objects)
        try:
            set_raw = await self._async_read_raw_with_retry(
                b"wifilocalMonitor:get dev set infor"
            )
            set_text = set_raw.replace("'", '"').strip()
            merged: Dict[str, Any] = {}

            # Parse several consecutive JSON objects:
            depth = 0
            start = None
            json_objects: list[str] = []

            for i, ch in enumerate(set_text):
                if ch == "{":
                    if depth == 0:
                        start = i
                    depth += 1
                elif ch == "}":
                    if depth > 0:
                        depth -= 1
                        if depth == 0 and start is not None:
                            json_objects.append(set_text[start : i + 1])
                            start = None

            # Fallback to a simple regex, just in case
            if not json_objects:
                json_objects = re.findall(r"\{.*?\}", set_text)

            for obj in json_objects:
                try:
                    part = json.loads(obj)
                    merged.update(part)
                except Exception as e:
                    _LOGGER.debug("Skip invalid part in settings: %s", e)
                    continue

            if merged:
                data["_settings"] = merged
                _LOGGER.debug(
                    "Merged Felicity settings (%d keys from %d objects): %s",
                    len(merged),
                    len(json_objects),
                    merged,
                )
            else:
                _LOGGER.debug("No valid JSON found in settings payload: %r", set_text)

        except Exception as err:
            _LOGGER.debug("Failed to read settings info: %s", err)

        # 4. Date/status info (best effort) - carries the Wi-Fi module's
        # own signal strength (rssi), uptime (tick) and last reset reason
        # (rstRS), none of which were queried before. One extra TCP
        # round-trip per poll cycle (every DEFAULT_SCAN_INTERVAL seconds);
        # if that ever becomes a problem for the module, this block can be
        # removed without affecting anything else.
        try:
            date_raw = await self._async_read_raw_with_retry(
                b"wifilocalMonitor:get Date"
            )
            date_text = date_raw.replace("'", '"').strip()
            data["_date"] = json.loads(date_text)
        except Exception as err:
            _LOGGER.debug("Failed to read date/status info: %s", err)

        return data

    async def _async_read_raw_with_retry(
        self,
        command: bytes,
        retries: int = READ_RETRIES,
        backoff: float = READ_RETRY_BACKOFF,
    ) -> str:
        """Retry _async_read_raw a couple of times before giving up.

        The Wi-Fi module occasionally needs a bit longer to answer (e.g.
        while it's also pushing data to the FSOLAR cloud). A single
        transient failure here used to bubble straight up as UpdateFailed,
        flipping every entity to 'unavailable' until the next 30s poll.
        """
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                return await self._async_read_raw(command)
            except FelicityApiError as err:
                last_err = err
                _LOGGER.debug(
                    "Attempt %d/%d for %r failed: %s",
                    attempt + 1,
                    retries + 1,
                    command,
                    err,
                )
                if attempt < retries:
                    await asyncio.sleep(backoff)
        assert last_err is not None
        raise last_err

    async def _async_read_raw(self, command: bytes) -> str:
        """Open TCP, send command, read the *full* response as text.

        Some replies (notably 'get dev set infor') consist of several
        concatenated JSON objects and can arrive across multiple TCP
        reads. We keep reading until the socket has been idle for
        IDLE_TIMEOUT seconds instead of bailing out after the first
        '}' we see, so multi-fragment replies aren't truncated.
        """
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port),
                timeout=CONNECT_TIMEOUT,
            )
        except Exception as err:
            raise FelicityApiError(
                f"Error connecting to {self._host}:{self._port}: {err}"
            ) from err

        data = b""
        try:
            writer.write(command)
            await writer.drain()

            # Wait (generously) for the first byte of the response.
            try:
                data = await asyncio.wait_for(
                    reader.read(4096), timeout=FIRST_BYTE_TIMEOUT
                )
            except asyncio.TimeoutError:
                data = b""

            # Keep draining the socket while data keeps trickling in.
            while data:
                try:
                    chunk = await asyncio.wait_for(
                        reader.read(4096), timeout=IDLE_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    break
                if not chunk:
                    break
                data += chunk

        except Exception as err:
            raise FelicityApiError(
                f"Error talking to {self._host}:{self._port}: {err}"
            ) from err
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

        if not data:
            raise FelicityApiError("No data received from battery")

        text = data.decode("ascii", errors="ignore").strip()
        _LOGGER.debug("Raw Felicity response for %r: %r", command, text)
        return text

    # --------------------------------------------------------------------- #
    #                         PARSER 'dev real infor'                       #
    # --------------------------------------------------------------------- #

    def _parse_real_payload(self, text: str) -> Dict[str, Any]:
        """Parse Felicity 'dev real infor' payload into dict we use."""
        norm = text.replace("'", '"')
        last_brace = norm.rfind("}")
        if last_brace != -1:
            norm = norm[: last_brace + 1]

        result: Dict[str, Any] = {}

        def _find_str(key: str) -> str | None:
            m = re.search(rf'"{key}"\s*:\s*"([^"]*)"', norm)
            return m.group(1) if m else None

        def _find_int(key: str) -> int | None:
            m = re.search(rf'"{key}"\s*:\s*([-0-9]+)', norm)
            return int(m.group(1)) if m else None

        # Simple fields
        result["CommVer"] = _find_int("CommVer")
        result["wifiSN"] = _find_str("wifiSN")
        result["DevSN"] = _find_str("DevSN")
        result["Estate"] = _find_int("Estate")
        result["Bfault"] = _find_int("Bfault")
        result["Bwarn"] = _find_int("Bwarn") or 0

        # Batt: [[53300],[1],[null]]
        m = re.search(
            r'"Batt"\s*:\s*\[\s*\[\s*([-0-9]+)\s*\]\s*,\s*\[\s*([-0-9]+)\s*\]\s*,\s*\[\s*(null|None|[-0-9]+)?\s*\]\s*\]',
            norm,
        )
        if m:
            v = int(m.group(1))
            i = int(m.group(2))
            third_raw = m.group(3)
            third = None
            if third_raw not in (None, "null", "None", ""):
                third = int(third_raw)
            result["Batt"] = [[v], [i], [third]]

        # Batsoc: [[9900,1000,250000]]
        m = re.search(
            r'"Batsoc"\s*:\s*\[\s*\[\s*([-0-9]+)\s*,\s*([-0-9]+)\s*,\s*([-0-9]+)\s*\]\s*\]',
            norm,
        )
        if m:
            soc = int(m.group(1))
            scale = int(m.group(2))
            cap = int(m.group(3))
            result["Batsoc"] = [[soc, scale, cap]]

        # BMaxMin: [[3345,3338],[6,7]]
        m = re.search(
            r'"BMaxMin"\s*:\s*\[\s*\[\s*([-0-9]+)\s*,\s*([-0-9]+)\s*\]\s*,\s*\[\s*([-0-9]+)\s*,\s*([-0-9]+)\s*\]\s*\]',
            norm,
        )
        if m:
            max_v = int(m.group(1))
            min_v = int(m.group(2))
            max_i = int(m.group(3))
            min_i = int(m.group(4))
            result["BMaxMin"] = [[max_v, min_v], [max_i, min_i]]

        # LVolCur: [[576,480],[100,1500]]
        m = re.search(
            r'"LVolCur"\s*:\s*\[\s*\[\s*([-0-9]+)\s*,\s*([-0-9]+)\s*\]\s*,\s*\[\s*([-0-9]+)\s*,\s*([-0-9]+)\s*\]\s*\]',
            norm,
        )
        if m:
            v1 = int(m.group(1))
            v2 = int(m.group(2))
            c1 = int(m.group(3))
            c2 = int(m.group(4))
            result["LVolCur"] = [[v1, v2], [c1, c2]]

        # BTemp
        btemp = None
        m = re.search(
            r'"BTemp"\s*:\s*\[\s*\[\s*([-0-9]+)\s*,\s*([-0-9]+)\s*\]'
            r'(?:\s*,\s*\[\s*([-0-9]+)\s*,\s*([-0-9]+)\s*\])?\s*\]',
            norm,
        )
        if m:
            t1 = int(m.group(1))
            t2 = int(m.group(2))
            if m.group(3) is not None and m.group(4) is not None:
                t3 = int(m.group(3))
                t4 = int(m.group(4))
                btemp = [[t1, t2], [t3, t4]]
            else:
                btemp = [[t1, t2]]
        else:
            m = re.search(
                r'"Templist"\s*:\s*\[\s*\[\s*([-0-9]+)\s*,\s*([-0-9]+)\s*\]',
                norm,
            )
            if m:
                t1 = int(m.group(1))
                t2 = int(m.group(2))
                btemp = [[t1, t2]]
        if btemp is not None:
            result["BTemp"] = btemp

        # BatcelList: [0] = pack/string voltages (~53V, confirmed against
        # the FSOLAR app's "Batteriespannung" table), [1] = real individual
        # cell voltages (~3.3V, confirmed against "Vol. der Zelle"). The
        # second sub-array used to be dropped entirely - now captured too.
        m = re.search(
            r'"BatcelList"\s*:\s*\[\s*\[([0-9,\s-]+)\]\s*,\s*\[([0-9,\s-]+)\]\s*\]',
            norm,
        )
        if m:
            try:
                cells0 = [int(x) for x in m.group(1).split(",") if x.strip() != ""]
                cells1 = [int(x) for x in m.group(2).split(",") if x.strip() != ""]
                result["BatcelList"] = [cells0, cells1]
            except Exception:
                _LOGGER.debug("Failed to parse BatcelList from %r", m.group(0))
        else:
            # Fallback: only the first sub-array present/parseable.
            m = re.search(r'"BatcelList"\s*:\s*\[\s*\[([0-9,\s-]+)\]', norm)
            if m:
                cells_str = m.group(1)
                try:
                    cells0 = [int(x) for x in cells_str.split(",") if x.strip() != ""]
                    result["BatcelList"] = [cells0]
                except Exception:
                    _LOGGER.debug("Failed to parse BatcelList from %r", cells_str)

        _LOGGER.debug("Parsed Felicity real data dict: %s", result)

        if "Batsoc" not in result and "Batt" not in result:
            raise FelicityApiError(
                f"Unable to parse essential fields from payload: {text}"
            )

        return result
