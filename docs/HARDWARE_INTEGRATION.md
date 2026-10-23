# Hardware integration

## Current status: simulator only

**No physical protocol adapter is implemented or verified.** Nothing in this repository has communicated with real
machinery, sensors, PLCs or controllers. The simulator exercises the same adapter contract, pipeline, storage and UI that a
physical adapter would use, so it validates the *software* design, **not** any hardware behaviour.

## Why Modbus TCP is deferred

Modbus TCP is the planned first physical protocol, but an adapter was intentionally **not** shipped because it cannot be
implemented *and tested meaningfully* yet:

1. There is no identified target device or documented register map (addresses, data types, scaling, word order, units, access rules).
   Inventing one would be fabricated hardware knowledge.
2. The adapter's most important guarantee - that it can **never issue a write function** - must be verified against a protocol
   test server, which was not available in the build environment.
3. The client library's API differs between releases, so the adapter must be built and tested against a pinned version.

Configuring `adapter_type = "modbus_tcp"` is therefore rejected at validation time with an explanatory message
(`adapters/registry.py`), rather than failing later or pretending to work.

## Adapter contract

`adapters/base.py` defines `DeviceAdapter`: `validate_settings` (classmethod, no I/O), `open`, `connect`, `read`, `close`,
`connection_state`. There is deliberately **no write/command operation**; `tests/unit/test_security_readonly.py` fails the build if one appears.
Rules: raise `AdapterFailure(ErrorInfo)` for connect/read failures; make `close()` idempotent and able to interrupt a blocked call;
return `AdapterReadout(device_id, results, operating_report)`; never fabricate values; set measurement `unit` only from configuration
(a differing unit is rejected, never converted).

## Steps to add a read-only Modbus TCP adapter

1. Obtain the device's Modbus documentation and write the register map into `[devices.measurements.source_mapping]`
   (function code, address, count, data type, byte/word order, scale, offset, unit). Record the document and revision.
2. Implement `adapters/modbus_tcp.py` using only **read** function codes (`01/02/03/04`). Add to `ADAPTERS`; remove it from `DEFERRED_ADAPTERS`.
3. Validate the mapping in `validate_settings` (address ranges, types, overlap, supported function codes) - fail at configuration time.
4. Test against a Modbus test server (not a real machine first): decoding, timeouts, exception responses, disconnects, malformed frames,
   and a test that records every request and asserts **only read codes** were sent.
5. Only then test against real equipment, in a safe supervised setting, with the checklist below. Keep `source_kind = PHYSICAL`.

## Physical validation checklist (to be completed by a person with the hardware)

- [ ] Device model, firmware, Modbus documentation revision recorded
- [ ] Network path agreed with the plant (read-only, isolated segment, no internet exposure); static addressing documented
- [ ] Register map verified against the device (known values read back and compared with the device display)
- [ ] Scaling, units and word order verified at two or more points
- [ ] Timeout/disconnect behaviour observed by unplugging the network cable (state goes DEGRADED → RECOVERING → CONNECTED)
- [ ] Confirmed with a protocol capture that only read requests are sent
- [ ] Machine-safety review: monitoring never affects control; the app is not part of any safety function
- [ ] Results recorded in `docs/VERIFICATION.md`

## Interface and wiring notes (informational, not validated)

Typical Ethernet monitoring: monitoring PC → managed switch/isolated VLAN → device Ethernet port (Modbus TCP, default port 502),
device and PC on the same subnet with static addresses. Use a read-only account or a gateway if the device supports it. Do not expose
port 502 or this app to the internet. Serial (RS-485) or other fieldbuses need their own adapter and electrical review by a qualified person.
