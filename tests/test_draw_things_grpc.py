"""Draw Things over gRPC (draw_things_grpc): the FlatBuffer, the protobuf and the tensors, without a server."""

import io
import struct

import pytest
from PIL import Image

from app.services import draw_things_grpc as dt


def _read_table(buffer: bytes) -> dict[int, int]:
    """FlatBuffers' own reading: the root table, its vtable, and where each slot's field is (0: not set)."""
    table = struct.unpack_from("<I", buffer, 0)[0]
    vtable = table - struct.unpack_from("<i", buffer, table)[0]
    size = struct.unpack_from("<H", buffer, vtable)[0]
    slots = (size - 4) // 2
    return {slot: table + offset for slot, offset in enumerate(struct.unpack_from(f"<{slots}H", buffer, vtable + 4)) if offset}


def test_the_configuration_reads_back_as_a_flatbuffer():
    buffer = dt.configuration(start_width=16, start_height=13, seed=4_000_000_000, steps=8, strength=0.75, model="flux_3_klein_i8x.ckpt")
    at = _read_table(buffer)
    read = lambda name, kind: struct.unpack_from("<" + kind, buffer, at[dt._SLOTS[name][0]])[0]  # noqa: E731
    assert (read("start_width", "H"), read("start_height", "H"), read("seed", "I"), read("steps", "I")) == (16, 13, 4_000_000_000, 8)
    assert read("strength", "f") == pytest.approx(0.75) and read("shift", "f") == pytest.approx(2.01)
    assert read("sampler", "b") == dt.SAMPLER_DDIM_TRAILING and read("resolution_dependent_shift", "?") is False
    for slot, position in at.items():  # every field sits on its own size
        kind = next(k for s, k in dt._SLOTS.values() if s == slot)
        assert position % (4 if kind == "s" else struct.calcsize("<" + kind)) == 0
    model = at[dt._SLOTS["model"][0]]
    text = model + struct.unpack_from("<I", buffer, model)[0]
    length = struct.unpack_from("<I", buffer, text)[0]
    assert buffer[text + 4:text + 4 + length] == b"flux_3_klein_i8x.ckpt" and buffer[text + 4 + length] == 0


def test_the_request_carries_the_references_as_a_moodboard_and_no_canvas():
    message = dt.request("keep his face", b"config", references=[b"one", b"two"])
    fields = list(dt._fields(message))
    assert 1 not in dict(fields)  # no canvas: nothing of an earlier picture can stay
    hint = dict(fields)[4]
    inner = list(dt._fields(hint))
    assert inner[0] == (1, b"shuffle") and [dict(dt._fields(t))[1] for n, t in inner if n == 2] == [b"one", b"two"]
    assert dict(fields)[5] == b"keep his face" and dict(fields)[7] == b"config"


def test_a_picture_goes_through_a_tensor_and_back():
    picture = Image.new("RGB", (300, 200), (200, 30, 90))
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")
    tensor = dt.to_tensor(buffer.getvalue(), 128, 64)
    assert len(tensor) == dt.HEADER + 128 * 64 * 3 * 2 and struct.unpack_from("<9I", tensor)[6:9] == (64, 128, 3)
    back = Image.open(io.BytesIO(dt.from_tensor(tensor)))
    assert back.size == (128, 64) and all(abs(a - b) <= 1 for a, b in zip(back.getpixel((5, 5)), (200, 30, 90)))
    with pytest.raises(dt.DrawThingsError, match="compression"):
        dt.from_tensor(struct.pack("<9I", 1012247, 0, 0, 0, 0, 1, 1, 1, 3) + b"\0" * 100)


def test_the_answer_is_read_from_grpc_frames(monkeypatch):
    picture = Image.new("RGB", (64, 64), (10, 200, 10))
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")
    tensor = dt.to_tensor(buffer.getvalue(), 64, 64)
    answers = [dt._field(2, dt._field(3, dt._field(1, 4))), dt._field(1, tensor[:100]) + dt._field(8, 1), dt._field(1, tensor[100:])]
    sent = []
    monkeypatch.setattr(dt, "call", lambda url, method, message, timeout: sent.append(method) or answers)
    png = dt.generate("http://dt:7859", "a footballer", "flux_3_klein_i8x.ckpt", 7, 64, 64)
    assert sent == ["GenerateImage"] and Image.open(io.BytesIO(png)).getpixel((3, 3))[1] >= 199
    framed = dt._frame(b"abc") + dt._frame(b"")
    assert list(dt._messages(framed)) == [b"abc", b""]
