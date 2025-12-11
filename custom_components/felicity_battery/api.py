from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict

_LOGGER = logging.getLogger(__name__)


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
        # 1. Runtime data
        real_raw = await self._async_read_raw(b"wifilocalMonitor:get dev real infor")
        real = self._parse_real_payload(real_raw)
        data: Dict[str, Any] = dict(real)

        # 2. Basic info
        try:
            basic_raw = await self._async_read_raw(
                b"wifilocalMonitor:get dev basice infor"
            )
            basic_text = basic_raw.replace("'", '"').strip()
            basic = json.loads(basic_text)
            data["_basic"] = basic
        except Exception as err:
            _LOGGER.debug("Failed to read basic info: %s", err)

        
        # 3. Settings / limits (может быть в нескольких JSON-блоках)
        try:
            set_raw = await self._async_read_raw(
                b"wifilocalMonitor:get dev set infor"
            )
            set_text = set_raw.replace("'", '"').strip()
            merged: Dict[str, Any] = {}

            # Разбираем несколько JSON-объектов подряд:
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

            # На всякий случай fallback на простое регулярное выражение
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
                    "Merged Felicity settings (%d keys): %s",
                    len(merged),
                    merged,
                )
            else:
                _LOGGER.debug("No valid JSON found in settings payload: %r", set_text)

        except Exception as err:
            _LOGGER.debug("Failed to read settings info: %s", err)

        return data

    async def _async_read_raw(self, command: bytes) -> str:
        """Open TCP, send command, read response as text."""
        try:
            reader, writer = await asyncio.open_connection(self._host, self._port)
        except Exception as err:
            raise FelicityApiError(
                f"Error connecting to {self._host}:{self._port}: {err}"
            ) from err

        try:
            writer.write(command)
            await writer.drain()

            data = b""
            for _ in range(20):
                try:
                    chunk = await asyncio.wait_for(reader.read(1024), timeout=0.5)
                except asyncio.TimeoutError:
                    break
                if not chunk:
                    break
                data += chunk
                if b"}" in chunk:
                    try:
                        more = await asyncio.wait_for(reader.read(1024), timeout=0.2)
                        if more:
                            data += more
                    except asyncio.TimeoutError:
                        pass
                    break

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

        # BatcelList
        m = re.search(r'"BatcelList"\s*:\s*\[\s*\[([0-9,\s-]+)\]', norm)
        if m:
            cells_str = m.group(1)
            try:
                cells = [int(x) for x in cells_str.split(",") if x.strip() != ""]
                result["BatcelList"] = [cells]
            except Exception:
                _LOGGER.debug("Failed to parse BatcelList from %r", cells_str)

        _LOGGER.debug("Parsed Felicity real data dict: %s", result)

        if "Batsoc" not in result and "Batt" not in result:
            raise FelicityApiError(
                f"Unable to parse essential fields from payload: {text}"
            )

        return result