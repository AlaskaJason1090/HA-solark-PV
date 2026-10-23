"""Hardware approval boundary. Empty until model/firmware and site limits are verified."""

from decimal import Decimal, InvalidOperation

from homeassistant.exceptions import HomeAssistantError

# Do not populate from config entry options. Each approval needs a reviewed profile.
APPROVED_WRITE_KEYS: frozenset[str] = frozenset()
APPROVED_READ_KEYS: frozenset[str] = frozenset()
STAGED_SENSOR_KEYS = frozenset(
    {"batt_measure_mode_raw", "batt_measure_mode", "tou_days"}
)


def encode_number(value, minimum, maximum, step, scale):
    """Encode an exact, finite, in-range number without silently rounding."""
    try:
        val, low, high, increment, factor = map(
            lambda x: Decimal(str(x)), (value, minimum, maximum, step, scale)
        )
        if not all(x.is_finite() for x in (val, low, high, increment, factor)):
            raise ValueError("Non-finite number")
        if increment <= 0 or factor <= 0 or not low <= val <= high:
            raise ValueError("Number outside permitted range")
        raw = val / factor
        if (
            (val - low) % increment
            or raw != raw.to_integral_value()
            or not 0 <= raw <= 65535
        ):
            raise ValueError("Number is not representable at this step/scale")
        return int(raw)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise HomeAssistantError("Invalid inverter setting") from exc


def validate_raw_write(register_map, address, value):
    """Enforce approval and encoding again at the hub boundary, before I/O."""
    if type(address) is not int or type(value) is not int or not 0 <= value <= 65535:
        raise HomeAssistantError("Invalid register write")
    entry = next(
        (
            e
            for e in register_map
            if e.address == address
            and e.ha_platform in ("number", "time", "select", "switch")
        ),
        None,
    )
    if entry is None or entry.key not in APPROVED_WRITE_KEYS:
        raise HomeAssistantError(
            "Inverter control blocked: hardware compatibility and site limits are unverified"
        )
    if entry.ha_platform == "number":
        encode_number(
            Decimal(value) * Decimal(str(entry.scale)),
            entry.min_value,
            entry.max_value,
            entry.step,
            entry.scale,
        )
    elif entry.ha_platform == "time" and (value // 100 > 23 or value % 100 > 59):
        raise HomeAssistantError("Invalid HHMM value")
    elif entry.ha_platform == "select" and value not in (0, 1, 2, 3):
        raise HomeAssistantError("Invalid TOU option")
    elif entry.ha_platform == "switch" and value not in (0, 1):
        raise HomeAssistantError("Invalid switch value")
