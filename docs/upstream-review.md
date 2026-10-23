# Upstream review and hardware approval boundary

This draft targets AlaskaJason1090/HA-solark-PV at
`b3440e73ede24d1e9e7ee4f0107ace18ac83078b` (manifest 1.4.0).
Upstream was inspected at `de3f9d32b8671e3a9675912baae3970cb97b712f`
(manifest 1.6.0). No live Home Assistant files, services, or inverter were accessed.
The fork update is identified as `1.6.0b1`; it is not a release or a deployment.

## Three-commit disposition

| Upstream commit | Change | Treatment |
| --- | --- | --- |
| [f93ee6a](https://github.com/pbix/HA-solark-PV/commit/f93ee6a7a31cf02011e1573d36645d32f16d01be) | Close failed TCP connection; skip remaining reads in a failed poll | Included, extended to returned errors and incomplete reads |
| [df0c52a](https://github.com/pbix/HA-solark-PV/commit/df0c52a7dccec4216a1169a93ec16378415601ac) | Manifest 1.5.0 | Reviewed; superseded by draft version |
| [de3f9d3](https://github.com/pbix/HA-solark-PV/commit/de3f9d32b8671e3a9675912baae3970cb97b712f) | Writable number/time/select and switch infrastructure; additional diagnostic registers | Staged and hardened; no unverified entities exposed or registers polled |

Unrelated options-flow changes, sensor refactoring, doubled timeouts, and fixed
200 ms delays were not adopted. Existing sensor IDs, polling ranges and 5-second
timeouts are preserved. Client protocol retries are explicitly zero to avoid
replaying a write whose acknowledgement was lost. Failed reads recover at the
next scheduled poll. Decoder debug logging now uses Python logging because
`pymodbus.logging.Log` does not exist in the declared minimum PyModbus 3.1.1.

## Register and encoding audit

Addresses below are the **zero-based protocol addresses used by upstream code**;
no 40001 offset or extra subtraction is applied. All listed raw values are UINT16.
This table verifies consistency with upstream implementation, not manufacturer
approval for Jason's hardware. None of these new registers is hardware verified.

| Setting | Address | Engineering value = raw × scale | Upstream configured values | Status |
| --- | --- | --- | --- | --- |
| Battery measurement mode | 213 | raw × 1 | 0 Voltage; 1 SOC; 2 No Battery | Staged read diagnostic |
| Battery charge current limit | 230 | raw × 1 A | 0–120 A, step 1 | Blocked |
| TOU days | 248 | bit field | bit 0 enable; bits 1–7 Mon–Sun | Staged read diagnostic, no whole-register switch write |
| TOU 1–6 start time | 250–255 | decimal HHMM | 0000–2359; minute 00–59 | Blocked |
| TOU 1–6 power | 256–261 | raw × 1 W | 100–8000 W, step 100 | Blocked |
| TOU 1–6 voltage | 262–267 | raw × 0.01 V | 40–60 V, step 0.01 | Blocked |
| TOU 1–6 SOC | 268–273 | raw × 1% | 5–95%, step 1 | Blocked |
| TOU 1–6 charge/sell | 274–279 | enumeration | 0 None; 1 Charge; 2 Sell; 3 Charge & Sell | Blocked |
| Switch | No upstream entries | Infrastructure encodes 0/1 | No mapped hardware setting | No switch created |

The 120 A, 8000 W, 40–60 V, and 5–95% ranges are **upstream UI limits**, not
established battery/inverter safety limits. The charge/sell enumeration and
voltage scale likewise remain upstream assertions. Correct register identity,
firmware behavior, supported function code, bit preservation, and physical safe
limits require a matching manufacturer write map and site-specific review.

Primary-source lookup did not establish a writable map for the actual installed
model/firmware. Sol-Ark's [document library](https://sol-ark.com/resources/product-document-library/)
and [5K installation guide](https://www.sol-ark.com/wp-content/uploads/2024/06/5K-1P-N_Manual.pdf)
were checked; a wiring/operating guide is not proof of these writable addresses.
Upstream [PR #95](https://github.com/pbix/HA-solark-PV/pull/95) reports contributor
use, but does not establish compatibility with this installation.

## Enforced isolation and write handling

Only the sensor platform is forwarded. `APPROVED_WRITE_KEYS` and
`APPROVED_READ_KEYS` in `write_policy.py` are empty, source-controlled sets.
The entity factories exclude unapproved controls and new diagnostics. Additional
registers are excluded from periodic polling so unsupported addresses cannot
invalidate existing telemetry. Direct hub write calls fail before transport I/O,
including calls to existing telemetry addresses. There is no UI/config option to
bypass this boundary. All new controls remain staged because no compatible
hardware profile has yet been established.

Staged code rejects non-finite, out-of-range, off-step and unrepresentable numbers
instead of rounding. Example: 54.01 V encodes as 5401; 54.005 V is rejected.
Times reject seconds and timezones; selects reject unknown/negative options.
A future bit-field switch needs a model-specific read/modify/write design; the
current boolean skeleton must not be applied to TOU days or other bit fields.

The transport uses upstream FC16 (`write_registers`, count 1), not FC6.
Per the [Modbus Application Protocol Specification, section 6.12](https://www.modbus.org/file/secure/modbusprotocolspecification.pdf),
a successful FC16 response echoes function code 16, starting address and count,
not register values. The wrapper checks all three and rejects exceptions, missing
responses, read responses, and wrong address/count. Transport errors close the
socket; the next request reconnects. Broadcast IDs and invalid UINT16 values are
rejected before connection. No automatic protocol retry is configured.

After an acknowledgement, the hub reads the same address/device and requires
exact raw readback before requesting telemetry refresh. Errors and mismatches
raise HomeAssistantError and do not optimistically overwrite state. A lost
response/readback failure means the physical outcome is unknown, not necessarily
that the inverter rejected the command. Writes are serialized through readback.
Transport's low-level method is internal; Home Assistant controls use the hub gate.

## Before activating any control

A follow-up change must bind approvals and narrowed limits to an explicit device
profile (model, firmware, serial/device ID), rather than globally approving a key.
It must document the manufacturer map revision, access mode, scale, valid enum or
bit mask, FC16 support and persistence semantics. Confirm battery/BMS charge
limits, inverter rating, parallel-system aggregate limits and battery operating
mode. Verify displayed values against a read-only bench capture. Establish
setting-specific bounds, then test one control on matching bench hardware with
before/after readback and an independent panel check. Failure or inconclusive
readback must leave further consequential writes blocked. Only then forward the
necessary control platforms and expose approved entries. No such activation is
included here.

## Reproducible validation

On Python 3.12:

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements.test.txt
.venv/bin/python -m pytest -q
```

Tests use Home Assistant classes and real PyModbus response objects with mocked
clients/coordinators. They never connect to an inverter. Coverage includes
connection reset/broken pipe, failed connect and failed close, error/short reads,
FC16 acknowledgements, no write replay, device keyword compatibility, boundary
and scale validation, entity encodings, readback failure, deny-by-default writes,
legacy polling ranges, and skipping the rest of a failed poll. These are software
tests; they do not certify inverter hardware or battery operating limits.

Validation result: **68 tests passed on each of PyModbus 3.1.1, 3.6.9,
3.9.2 and 3.11.2**, using Home Assistant 2024.12.5 on Python 3.12.14.
The same seven dependency deprecation warnings appeared on each run.
`ruff check --select F,E9 custom_components tests`, Python compilation and
`git diff --check` passed. This matrix does not cover every Home Assistant release.
