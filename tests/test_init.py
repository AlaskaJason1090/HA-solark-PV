"""Test config entry setup without connecting to hardware or starting HA."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_SCAN_INTERVAL, Platform

from custom_components import solark


@pytest.mark.asyncio
async def test_setup_only_forwards_sensors(monkeypatch):
    hub = SimpleNamespace(async_config_entry_first_refresh=AsyncMock())
    factory = Mock(return_value=hub)
    monkeypatch.setattr(solark, "SolArkModbusHub", factory)
    hass = SimpleNamespace(
        data={}, config_entries=SimpleNamespace(async_forward_entry_setups=AsyncMock())
    )
    entry = SimpleNamespace(
        data={CONF_NAME: "bench", CONF_HOST: "test.invalid", CONF_SCAN_INTERVAL: 20}
    )
    assert await solark.async_setup_entry(hass, entry)
    factory.assert_called_once_with(hass, "bench", "test.invalid", 20)
    hub.async_config_entry_first_refresh.assert_awaited_once()
    hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(
        entry, [Platform.SENSOR]
    )
