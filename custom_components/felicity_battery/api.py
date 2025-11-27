from __future__ import annotations

import asyncio
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
        """Send command and get parsed data dict."""
        raw = await self._async_read_raw()
        return self._parse_payload(raw)

    async def _async_read_raw(self) -> str:
        """Open TCP, send command, read response as text."""
        try:
            reader, writer = await asyncio.open_connection(self._host, self._port)
        except Exception as err:
            raise FelicityApiError(
                f"Error connecting to {self._host}:{self._port}: {err}"
            ) from err

        try:
            writer.write(b"wifilocalMonitor:get dev real infor")
            await writer.drain()

            data = b""
            # читаем несколько кусков, чтобы не отрезать хвост
            for _ in range(10):
                try:
                    chunk = await asyncio.wait_for(reader.read(1024), timeout=0.5)
                except asyncio.TimeoutError:
                    break
                if not chunk:
                    break
                data += chunk
                # если увидели закрывающую фигурную скобку – скорее всего, конец объекта
                if b"}" in chunk:
                    # чуть грубовато, но для нашего формата достаточно
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
        _LOGGER.debug("Raw Felicity response: %r", text)
        return text

    # --------------------------------------------------------------------- #
    #                    ПАРСЕР НАШЕЙ СТРОКИ ОТ АКБ                         #
    # --------------------------------------------------------------------- #

    def _parse_payload(self, text: str) -> Dict[str, Any]:
        """Parse Felicity custom JSON-like payload into a dict we use."""
        # Приводим все одинарные кавычки к двойным, чтобы regex был проще
        norm = text.replace("'", '"')
        # На всякий случай уберём мусор после последней фигурной скобки
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

        # Простые поля
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

        # BTemp – пытаемся сначала найти BTemp,
        # если его нет – берём первую пару из Templist
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
            # резервный вариант – Templist:[[140,130],[0,2],...]
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

        # BatcelList – не обязательно, но если есть, забираем весь первый массив
        m = re.search(
            r'"BatcelList"\s*:\s*\[\s*\[([0-9,\s-]+)\]',
            norm,
        )
        if m:
            cells_str = m.group(1)
            try:
                cells = [int(x) for x in cells_str.split(",")]
                result["BatcelList"] = [cells]
            except Exception:
                pass

        _LOGGER.debug("Parsed Felicity data dict: %s", result)

        # Минимальная валидация: без SOC и Batt смысла нет
        if "Batsoc" not in result and "Batt" not in result:
            raise FelicityApiError(
                f"Unable to parse essential fields from payload: {text}"
            )

        return result
