"""Validate staged encodings and the production deny-by-default boundary."""

import asyncio
from datetime import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.solark import PLATFORMS, write_policy
from custom_components.solark.hub import SolArkModbusHub
from custom_components.solark.number import SolArkNumber
from custom_components.solark.select import SolArkSelect
from custom_components.solark.switch import SolArkSwitch
from custom_components.solark.time import SolArkTime
from custom_components.solark.solark_register_map import SolArkRegisterMap
from custom_components.solark.pymodbus_wrapper import (
    ModbusResponse,
    ModbusResponseError,
)

try:
    from pymodbus.pdu.register_message import ReadHoldingRegistersResponse
except ImportError:
    from pymodbus.register_read_message import ReadHoldingRegistersResponse


def read_response(registers):
    response = ReadHoldingRegistersResponse()
    response.registers = registers
    return response


def hub():
    return SimpleNamespace(
        register_map=SolArkRegisterMap(),
        device_id=3,
        _write_lock=asyncio.Lock(),
        _client=SimpleNamespace(
            async_write_register=AsyncMock(
                return_value=ModbusResponse(SimpleNamespace(isError=lambda: False))
            ),
            async_read_holding_registers=AsyncMock(
                return_value=ModbusResponse(read_response(registers=[40]))
            ),
        ),
        async_request_refresh=AsyncMock(),
    )


def test_no_controls_exposed_or_forwarded():
    regmap = SolArkRegisterMap()
    assert [str(p) for p in PLATFORMS] == ["sensor"]
    assert not write_policy.APPROVED_WRITE_KEYS
    assert not write_policy.APPROVED_READ_KEYS
    assert (
        regmap.number_types()
        == regmap.time_types()
        == regmap.select_types()
        == regmap.switch_types()
        == {}
    )
    assert not write_policy.STAGED_SENSOR_KEYS.intersection(regmap.sensor_types())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address,value", [(230, 40), (250, 1200), (274, 3), (248, 255), (150, 1)]
)
async def test_direct_hub_calls_cannot_bypass_gate(address, value):
    instance = hub()
    with pytest.raises(HomeAssistantError, match="blocked"):
        await SolArkModbusHub.async_write_register(instance, address, value)
    instance._client.async_write_register.assert_not_called()


@pytest.mark.parametrize(
    "value,low,high,step,scale,expected",
    [
        (0, 0, 120, 1, 1, 0),
        (120, 0, 120, 1, 1, 120),
        (54.01, 40, 60, 0.01, 0.01, 5401),
        (8000, 100, 8000, 100, 1, 8000),
        (95, 5, 95, 1, 1, 95),
    ],
)
def test_exact_number_encoding(value, low, high, step, scale, expected):
    assert write_policy.encode_number(value, low, high, step, scale) == expected


@pytest.mark.parametrize(
    "value,low,high,step,scale",
    [
        (-1, 0, 120, 1, 1),
        (121, 0, 120, 1, 1),
        (1.5, 0, 120, 1, 1),
        (float("nan"), 0, 120, 1, 1),
        (float("inf"), 0, 120, 1, 1),
        (54.005, 40, 60, 0.01, 0.01),
        (150, 100, 8000, 100, 1),
        (1, 0, 2, 1, 0),
        (True, 0, 120, 1, 1),
    ],
)
def test_number_rejects_invalid_instead_of_rounding(value, low, high, step, scale):
    with pytest.raises(HomeAssistantError):
        write_policy.encode_number(value, low, high, step, scale)


@pytest.mark.parametrize(
    "address,value",
    [(230, 121), (250, 1260), (250, 2400), (274, 4), (262, 3999), (256, 150)],
)
def test_hub_revalidates_raw_values(monkeypatch, address, value):
    regmap = SolArkRegisterMap()
    monkeypatch.setattr(
        write_policy, "APPROVED_WRITE_KEYS", frozenset(e.key for e in regmap)
    )
    with pytest.raises(HomeAssistantError):
        write_policy.validate_raw_write(regmap, address, value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "readback",
    [
        ModbusResponse(read_response(registers=[41])),
        ModbusResponseError(OSError()),
    ],
)
async def test_unconfirmed_write_does_not_refresh_or_retry(monkeypatch, readback):
    monkeypatch.setattr(
        write_policy, "APPROVED_WRITE_KEYS", frozenset({"battery_charge_current_limit"})
    )
    instance = hub()
    instance._client.async_read_holding_registers.return_value = readback
    with pytest.raises(HomeAssistantError, match="unconfirmed"):
        await SolArkModbusHub.async_write_register(instance, 230, 40)
    instance._client.async_write_register.assert_awaited_once_with(230, 40, 3)
    instance.async_request_refresh.assert_not_called()


@pytest.mark.asyncio
async def test_confirmed_write_refreshes(monkeypatch):
    monkeypatch.setattr(
        write_policy, "APPROVED_WRITE_KEYS", frozenset({"battery_charge_current_limit"})
    )
    instance = hub()
    assert await SolArkModbusHub.async_write_register(instance, 230, 40)
    instance._client.async_read_holding_registers.assert_awaited_once_with(230, 1, 3)
    instance.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_write_error_never_reads_back_or_refreshes(monkeypatch):
    monkeypatch.setattr(
        write_policy, "APPROVED_WRITE_KEYS", frozenset({"battery_charge_current_limit"})
    )
    instance = hub()
    instance._client.async_write_register.return_value = ModbusResponseError(OSError())
    with pytest.raises(HomeAssistantError, match="unknown"):
        await SolArkModbusHub.async_write_register(instance, 230, 40)
    instance._client.async_read_holding_registers.assert_not_called()
    instance.async_request_refresh.assert_not_called()


def entity(cls, description):
    obj = object.__new__(cls)
    obj.entity_description = SimpleNamespace(**description)
    obj.coordinator = SimpleNamespace(
        data={"setting": 17}, async_write_register=AsyncMock(return_value=True)
    )
    obj.async_write_ha_state = Mock()
    return obj


@pytest.mark.asyncio
async def test_entity_number_scale_and_no_optimistic_state():
    obj = entity(
        SolArkNumber,
        dict(
            key="setting",
            address=262,
            native_min_value=40,
            native_max_value=60,
            native_step=0.01,
            scale=0.01,
        ),
    )
    await obj.async_set_native_value(54.01)
    obj.coordinator.async_write_register.assert_awaited_once_with(262, 5401)
    assert obj.coordinator.data == {"setting": 17}
    obj.async_write_ha_state.assert_not_called()


@pytest.mark.asyncio
async def test_time_encoding_and_precision():
    obj = entity(SolArkTime, dict(key="setting", address=250))
    await obj.async_set_value(time(23, 59))
    obj.coordinator.async_write_register.assert_awaited_once_with(250, 2359)
    with pytest.raises(HomeAssistantError):
        await obj.async_set_value(time(12, 0, 1))
    obj.coordinator.data["setting"] = 1260
    assert obj.native_value is None


@pytest.mark.asyncio
async def test_select_encoding_and_invalid_read():
    obj = entity(SolArkSelect, dict(key="setting", address=274))
    obj._attr_options = ["None", "Charge", "Sell", "Charge & Sell"]
    await obj.async_select_option("Charge & Sell")
    obj.coordinator.async_write_register.assert_awaited_once_with(274, 3)
    with pytest.raises(HomeAssistantError):
        await obj.async_select_option("invalid")
    for value in (-1, 4, None):
        obj.coordinator.data["setting"] = value
        assert obj.current_option is None


@pytest.mark.asyncio
async def test_switch_encoding_and_unknown_state():
    obj = entity(SolArkSwitch, dict(key="setting", address=999))
    await obj.async_turn_on()
    await obj.async_turn_off()
    assert [c.args for c in obj.coordinator.async_write_register.await_args_list] == [
        (999, 1),
        (999, 0),
    ]
    assert obj.is_on is None


def test_polling_preserves_legacy_ranges():
    instance = SimpleNamespace(
        register_map=SolArkRegisterMap(), _process_register_range=Mock()
    )
    SolArkModbusHub._read_modbus_realtime_data(instance)
    assert [
        (c.args[0].address, c.args[-1].address)
        for c in instance._process_register_range.call_args_list
    ] == [(60, 79), (84, 91), (96, 108), (109, 114), (150, 170), (171, 188), (190, 196)]


def test_failed_poll_skips_remaining_ranges():
    regmap = SolArkRegisterMap()
    regmap.set_error()
    instance = SimpleNamespace(register_map=regmap, _read_holding_registers=Mock())
    SolArkModbusHub._process_register_range(instance, regmap.BATT_P)
    instance._read_holding_registers.assert_not_called()


def test_upstream_register_layout_is_explicit():
    regmap = SolArkRegisterMap()
    assert (
        regmap.BATT_CHARGE_CURRENT_LIMIT.address,
        regmap.BATT_CHARGE_CURRENT_LIMIT.scale,
    ) == (230, 1)
    for slot in range(1, 7):
        for suffix, base in [
            ("TIME", 249),
            ("POWER", 255),
            ("VOLT", 261),
            ("SOC", 267),
            ("CHARGE_SELL", 273),
        ]:
            assert getattr(regmap, f"TOU{slot}_{suffix}").address == base + slot
    assert not any(e.ha_platform == "switch" for e in regmap)


@pytest.mark.asyncio
async def test_overlapping_writes_keep_ack_readback_pairs_serialized(monkeypatch):
    monkeypatch.setattr(
        write_policy, "APPROVED_WRITE_KEYS", frozenset({"battery_charge_current_limit"})
    )
    instance = hub()
    events = []
    applied = None

    async def write(address, value, device):
        nonlocal applied
        applied = value
        events.append(("write", value))
        await asyncio.sleep(0)
        return ModbusResponse(SimpleNamespace(isError=lambda: False))

    async def read(address, count, device):
        events.append(("read", applied))
        return ModbusResponse(read_response([applied]))

    instance._client.async_write_register.side_effect = write
    instance._client.async_read_holding_registers.side_effect = read
    await asyncio.gather(
        SolArkModbusHub.async_write_register(instance, 230, 40),
        SolArkModbusHub.async_write_register(instance, 230, 41),
    )
    assert events == [("write", 40), ("read", 40), ("write", 41), ("read", 41)]
