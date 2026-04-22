from refiner.refiner_ai_aer import AerEvent, decode_events, decode_spikes, decode_spikes_auto, encode_events, encode_spikes


def test_aer_round_trip_spike_vector():
    spikes = [0, 1, 0, 1, 1]
    payload = encode_spikes(123456, 4096, spikes)

    assert payload.startswith(b"AER1")
    assert decode_spikes(payload, 4096, len(spikes)) == spikes

    events = decode_events(payload)
    assert [event.addr for event in events] == [4097, 4099, 4100]
    assert all(event.ts_us == 123456 for event in events)


def test_decode_spikes_auto_handles_sparse_addresses():
    payload = encode_events(
        [
            AerEvent(ts_us=1, addr=16384 + 2, value=1),
            AerEvent(ts_us=1, addr=16384 + 5, value=1),
        ]
    )

    assert decode_spikes_auto(payload, 16384) == [0, 0, 1, 0, 0, 1]
