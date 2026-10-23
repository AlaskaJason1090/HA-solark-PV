"""Exercise real PyModbus responses with a fake transport; no inverter I/O."""

from unittest.mock import Mock

import pytest
from pymodbus.exceptions import ConnectionException, ModbusIOException
from pymodbus.pdu import ExceptionResponse

try:
    from pymodbus.pdu.register_message import (
        ReadHoldingRegistersResponse,
        WriteMultipleRegistersResponse,
    )
except ImportError:  # PyModbus < 3.8
    from pymodbus.register_read_message import ReadHoldingRegistersResponse
    from pymodbus.register_write_message import WriteMultipleRegistersResponse

from custom_components.solark import pymodbus_wrapper as modbus


def read_response(registers):
    response = ReadHoldingRegistersResponse()
    response.registers = registers
    return response


@pytest.fixture
def client(monkeypatch):
    transport = Mock()
    transport.connect.return_value = True
    transport.read_holding_registers.return_value = read_response(registers=[42])
    transport.write_registers.return_value = WriteMultipleRegistersResponse(
        address=230, count=1
    )
    monkeypatch.setattr(modbus, "ModbusTcpClient", Mock(return_value=transport))
    wrapper = modbus.ModbusClientWrapper(host="test.invalid")
    return wrapper, transport


@pytest.mark.parametrize(
    "failure",
    [
        ConnectionResetError(),
        BrokenPipeError(),
        OSError(),
        ConnectionException(),
        ModbusIOException(),
    ],
)
def test_read_recovers_by_closing_dead_socket(client, failure):
    wrapper, transport = client
    # A still-present socket makes connect a no-op until close is called.
    events = []
    transport.connect.side_effect = lambda: events.append("connect") or True
    transport.close.side_effect = lambda: events.append("close")
    transport.read_holding_registers.side_effect = [
        failure,
        read_response(registers=[42]),
    ]
    assert wrapper.read_holding_registers(150, 1, 1).is_error
    assert not wrapper._connected
    assert wrapper.read_holding_registers(150, 1, 1).registers == [42]
    assert events == ["connect", "close", "connect"]


@pytest.mark.parametrize(
    "response",
    [None, ExceptionResponse(3, 4), read_response(registers=[])],
)
def test_returned_read_error_also_drops_connection(client, response):
    wrapper, transport = client
    transport.read_holding_registers.return_value = response
    assert wrapper.read_holding_registers(150, 1, 1).is_error
    transport.close.assert_called_once()


def test_failed_connect_and_close_exception_can_recover(client):
    wrapper, transport = client
    transport.connect.side_effect = [False, True]
    transport.close.side_effect = OSError("close failed")
    assert wrapper.read_holding_registers(150, 1, 1).is_error
    assert not wrapper._connected
    assert wrapper.read_holding_registers(150, 1, 1).registers == [42]


@pytest.mark.parametrize("old", [True, False])
def test_fc16_ack_and_device_keyword(client, monkeypatch, old):
    wrapper, transport = client
    monkeypatch.setattr(modbus, "IS_PRIOR_TO_V3_10_0", old)
    assert not wrapper.write_register(230, 40, 3).is_error
    transport.write_registers.assert_called_once_with(
        address=230, values=[40], **{"slave" if old else "device_id": 3}
    )


@pytest.mark.parametrize(
    "response",
    [
        None,
        ExceptionResponse(16, 3),
        WriteMultipleRegistersResponse(address=231, count=1),
        WriteMultipleRegistersResponse(address=230, count=2),
        read_response(registers=[40]),
    ],
)
def test_write_rejects_error_and_wrong_ack(client, response):
    wrapper, transport = client
    transport.write_registers.return_value = response
    assert wrapper.write_register(230, 40, 1).is_error
    transport.write_registers.assert_called_once()
    transport.close.assert_called_once()


def test_write_failure_is_not_replayed_and_next_read_recovers(client):
    wrapper, transport = client
    transport.write_registers.side_effect = ConnectionResetError()
    assert wrapper.write_register(230, 40, 1).is_error
    assert wrapper.read_holding_registers(230, 1, 1).registers == [42]
    assert transport.connect.call_count == 2
    transport.write_registers.assert_called_once()


@pytest.mark.parametrize(
    "address,value,device",
    [
        (-1, 0, 1),
        (65536, 0, 1),
        (230, -1, 1),
        (230, 65536, 1),
        (230, 1.5, 1),
        (230, True, 1),
        (230, 1, 0),
        (230, 1, 248),
    ],
)
def test_invalid_transport_write_does_not_connect(client, address, value, device):
    wrapper, transport = client
    assert wrapper.write_register(address, value, device).is_error
    transport.connect.assert_not_called()
    transport.write_registers.assert_not_called()


@pytest.mark.asyncio
async def test_async_helpers(client):
    wrapper, _ = client
    assert not (await wrapper.async_write_register(230, 40, 1)).is_error
    assert (await wrapper.async_read_holding_registers(230, 1, 1)).registers == [42]


def test_tcp_constructor_disables_protocol_replay(monkeypatch):
    constructor = Mock()
    monkeypatch.setattr(modbus, "ModbusTcpClient", constructor)
    modbus.ModbusClientWrapper(host="test.invalid")
    constructor.assert_called_once_with(
        host="test.invalid", port=502, timeout=5, retries=0
    )


@pytest.mark.parametrize("old", [True, False])
def test_serial_constructor_disables_protocol_replay(monkeypatch, old):
    constructor = Mock()
    monkeypatch.setattr(modbus, "IS_PRIOR_TO_V3_5_0", old)
    monkeypatch.setattr(
        modbus,
        "OldModbusSerialClient" if old else "ModbusSerialClient",
        constructor,
        raising=False,
    )
    modbus.ModbusClientWrapper(serial_port="/dev/not-a-device")
    expected = dict(port="/dev/not-a-device", baudrate=9600, timeout=5, retries=0)
    if old:
        expected["method"] = "rtu"
    constructor.assert_called_once_with(**expected)
