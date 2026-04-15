"""Address-Event Representation helpers used by Refiner AI orchestration.

The AARNN runtime already exposes an AER binary format for spiking inputs and
outputs. Refiner uses the same lightweight encoding so workflow routing and
neuromorphic task support can interoperate with the sibling `aarnn_rust`
project without pulling Rust bindings into this repository.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple


AER_MAGIC = b"AER1"


class AerError(ValueError):
    """Raised when an AER payload cannot be decoded safely."""


@dataclass(frozen=True)
class AerEvent:
    """One decoded AER event."""

    ts_us: int
    addr: int
    value: int = 1


def _write_varint(value: int, out: bytearray) -> None:
    current = max(0, int(value))
    while current >= 0x80:
        out.append((current & 0x7F) | 0x80)
        current >>= 7
    out.append(current & 0x7F)


def _read_varint(payload: bytes, start: int) -> Tuple[int, int]:
    result = 0
    shift = 0
    index = start
    while index < len(payload):
        byte = payload[index]
        result |= (byte & 0x7F) << shift
        index += 1
        if byte & 0x80 == 0:
            return result, index
        shift += 7
        if shift >= 64:
            raise AerError("AER varint overflow")
    raise AerError("AER payload truncated")


def encode_events(events: Iterable[AerEvent]) -> bytes:
    """Encode events into the binary `AER1` payload used by `aarnn_rust`."""

    ordered = sorted(
        (AerEvent(int(event.ts_us), int(event.addr), int(event.value)) for event in events),
        key=lambda item: item.ts_us,
    )
    if not ordered:
        return b""
    base_ts = ordered[0].ts_us
    payload = bytearray()
    payload.extend(AER_MAGIC)
    payload.extend(int(base_ts).to_bytes(8, byteorder="little", signed=False))
    previous_ts = base_ts
    for event in ordered:
        delta = max(0, event.ts_us - previous_ts)
        previous_ts = event.ts_us
        _write_varint(delta, payload)
        _write_varint(event.addr, payload)
        _write_varint(event.value & 0xFF, payload)
    return bytes(payload)


def decode_events(payload: bytes) -> List[AerEvent]:
    """Decode an `AER1` payload into timestamped events."""

    if len(payload) < 12:
        raise AerError("AER payload truncated")
    if payload[:4] != AER_MAGIC:
        raise AerError("AER payload magic mismatch")
    index = 4
    base_ts = int.from_bytes(payload[index : index + 8], byteorder="little", signed=False)
    index += 8
    previous_ts = base_ts
    events: List[AerEvent] = []
    while index < len(payload):
        delta, index = _read_varint(payload, index)
        addr, index = _read_varint(payload, index)
        value, index = _read_varint(payload, index)
        previous_ts += delta
        events.append(AerEvent(ts_us=previous_ts, addr=addr, value=value & 0xFF))
    return events


def spikes_to_events(ts_us: int, base_addr: int, spikes: Sequence[int]) -> List[AerEvent]:
    """Convert a spike vector into address-events."""

    events: List[AerEvent] = []
    for index, spike in enumerate(spikes):
        if int(spike) == 0:
            continue
        events.append(
            AerEvent(
                ts_us=int(ts_us),
                addr=int(base_addr) + index,
                value=1 if int(spike) > 0 else 0,
            )
        )
    return events


def encode_spikes(ts_us: int, base_addr: int, spikes: Sequence[int]) -> bytes:
    """Encode a spike vector into the compact `AER1` representation."""

    return encode_events(spikes_to_events(ts_us, base_addr, spikes))


def apply_events_to_spikes(events: Iterable[AerEvent], base_addr: int, dst: List[int]) -> int:
    """Apply decoded events to an existing spike vector."""

    count = 0
    for event in events:
        if int(event.value) == 0:
            continue
        addr = int(event.addr)
        index = addr - int(base_addr) if addr >= int(base_addr) else addr
        if 0 <= index < len(dst):
            dst[index] = 1
            count += 1
    return count


def decode_spikes(payload: bytes, base_addr: int, length: int) -> List[int]:
    """Decode an `AER1` payload into a fixed-length spike vector."""

    spikes = [0 for _ in range(max(0, int(length)))]
    apply_events_to_spikes(decode_events(payload), base_addr, spikes)
    return spikes


def decode_spikes_auto(payload: bytes, base_addr: int) -> List[int]:
    """Decode an `AER1` payload to the smallest vector that can hold its events."""

    events = decode_events(payload)
    if not events:
        return []
    max_index = 0
    for event in events:
        addr = int(event.addr)
        index = addr - int(base_addr) if addr >= int(base_addr) else addr
        if index > max_index:
            max_index = index
    spikes = [0 for _ in range(max_index + 1)]
    apply_events_to_spikes(events, base_addr, spikes)
    return spikes


def spikes_from_floats(values: Sequence[float], threshold: float = 0.5) -> List[int]:
    """Threshold continuous values into binary spike flags."""

    cutoff = float(threshold)
    return [1 if float(value) >= cutoff else 0 for value in values]


def payload_hex(payload: bytes) -> str:
    """Return a lowercase hexadecimal form for logs and JSON payloads."""

    return payload.hex()
