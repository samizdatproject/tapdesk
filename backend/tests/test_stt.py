import struct

from tapdesk.stt import BYTES_PER_SECOND, MAX_SECONDS, Recorder, clean, wav


def test_wav_header():
    w = wav(b"\x01\x00" * 16000)
    assert w[:4] == b"RIFF" and w[8:16] == b"WAVEfmt "
    channels, rate = struct.unpack("<HI", w[22:28])
    assert channels == 1 and rate == 16000
    assert struct.unpack("<I", w[40:44])[0] == 32000 and len(w) == 44 + 32000


def test_recorder_caps_length_and_drops_too_short():
    r = Recorder()
    assert r.end() is None
    r.begin()
    r.add(b"\0" * 1000)
    assert r.end() is None  # under 0.3 s
    r.begin()
    for _ in range(MAX_SECONDS + 5):
        r.add(b"\0" * BYTES_PER_SECOND)
    assert len(r.end()) == MAX_SECONDS * BYTES_PER_SECOND
    r.begin()
    r.cancel()
    assert not r.active


def test_clean_drops_noise_markers():
    assert clean(" [BLANK_AUDIO] ") == ""
    assert clean("  Buy  0.01 bitcoin. ") == "Buy 0.01 bitcoin."
