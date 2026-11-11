"""Draw Things over gRPC: pictures and edits with reference pictures.

Draw Things' HTTP API takes one picture, the canvas, as the start of an img2img. FLUX.3 and FLUX.2 Klein keep a face
from *reference* pictures (the moodboard), which only the gRPC API can send: with HTTP an age-up drew a new man (11 Nov
2026). This client needs no gRPC library: the request is a hand-built protobuf and FlatBuffer, sent over HTTP/2 by
curl (macOS curl has nghttp2). The formats follow Draw Things' own sources (drawthingsai/draw-things-community:
imageService.proto, config.fbs, ImageGenerationServiceImpl.swift).

A picture travels as a Draw Things tensor: a 68-byte header (ccv_nnc_tensor_param_t: CPU memory, NHWC, Float16,
dims 1 x H x W x C) and Float16 values from -1 to 1 in RGB order.
"""

from __future__ import annotations

import io
import struct
import subprocess
import tempfile
from pathlib import Path

import numpy as np

SERVICE = "ImageGenerationService"
HEADER = 68
_CPU_MEMORY, _NHWC, _FLOAT16 = 0x1, 0x02, 0x20000
_COMPRESSED = 1012247  # the first word of a tensor packed with fpzip (the server's "response compression")

# GenerationConfiguration's fields (config.fbs), by their slot in the table: deprecated fields keep their slots.
_SLOTS = {"start_width": (1, "H"), "start_height": (2, "H"), "seed": (3, "I"), "steps": (4, "I"), "guidance_scale": (5, "f"),
          "strength": (6, "f"), "model": (7, "s"), "sampler": (8, "b"), "batch_count": (9, "I"), "batch_size": (10, "I"),
          "seed_mode": (17, "b"), "shift": (49, "f"), "speed_up_with_guidance_embed": (69, "?"), "guidance_embed": (70, "f"),
          "resolution_dependent_shift": (71, "?")}
SAMPLER_EULER_A_TRAILING, SAMPLER_DDIM_TRAILING, SEED_SCALE_ALIKE = 10, 16, 2
# Draw Things' own settings for FLUX.3 Klein (its app had them when the HTTP portraits were drawn); a field left out
# would take config.fbs's default (a Karras sampler, shift 1), not the app's.
DEFAULTS = {"sampler": SAMPLER_DDIM_TRAILING, "seed_mode": SEED_SCALE_ALIKE, "guidance_scale": 1.0, "shift": 2.01,
            "resolution_dependent_shift": False, "speed_up_with_guidance_embed": True, "guidance_embed": 3.5,
            "batch_count": 1, "batch_size": 1}


# Each family's own settings, by the start of the model's file name (the first that matches). Kontext keeps a face only
# with enough steps: at 8 Valera became an older man, at 20 he stayed himself (Mac Studio, 11 Nov 2026).
MODEL_SETTINGS = (
    ("flux_1_kontext", {"sampler": SAMPLER_EULER_A_TRAILING, "guidance_scale": 1.7, "steps": 20}),
    ("flux_3", {"steps": 8}),
    ("flux_2_klein", {"steps": 8}),
    ("z_image", {"steps": 8}),
)
DEFAULT_STEPS = 20


def settings_for(model: str) -> dict:
    """The settings for a model: its family's (MODEL_SETTINGS) over DEFAULTS, with its steps."""
    family = next((settings for start, settings in MODEL_SETTINGS if model.startswith(start)), {})
    return {"steps": DEFAULT_STEPS, **family}


def reads_the_canvas(model: str) -> bool:
    """FLUX.1 Kontext takes the face to keep from the canvas; the newer edit models (FLUX.3, FLUX.2 Klein, Qwen Image
    Edit) from reference (moodboard) pictures."""
    return model.startswith("flux_1_kontext")


class DrawThingsError(Exception):
    pass


# -- FlatBuffer: one table, scalars and one string ------------------------------------------------------------------
def configuration(**fields) -> bytes:
    """A GenerationConfiguration FlatBuffer with the given fields (see _SLOTS)."""
    values = {**DEFAULTS, **fields}
    scalars = [(_SLOTS[k][0], _SLOTS[k][1], v) for k, v in values.items() if _SLOTS[k][1] != "s" and v is not None]
    strings = [(_SLOTS[k][0], v) for k, v in values.items() if _SLOTS[k][1] == "s" and v is not None]
    # The table: its soffset to the vtable, then the fields, largest first so each is aligned to its size.
    order = sorted(scalars, key=lambda f: -struct.calcsize("<" + f[1]))
    layout, position = [], 4
    for slot, kind, value in order + [(slot, "I", None) for slot, _ in strings]:
        size = struct.calcsize("<" + kind)
        position += (-position) % size
        layout.append((slot, kind, value, position))
        position += size
    table_size = position + (-position) % 4
    slots = max(slot for slot, *_ in layout) + 1
    vtable = struct.pack(f"<HH{slots}H", 4 + 2 * slots, table_size,
                         *[next((pos for s, _, _, pos in layout if s == slot), 0) for slot in range(slots)])
    vtable += b"\0" * ((-len(vtable)) % 4)
    table_at = 4 + len(vtable)  # root offset, vtable, table, strings
    table = bytearray(table_size)
    struct.pack_into("<i", table, 0, table_at - 4)  # soffset: table minus vtable
    tail = bytearray()
    strings_at = table_at + table_size
    for slot, kind, value, pos in layout:
        if value is None:  # a string: an offset from this field to the string
            text = dict(strings)[slot].encode()
            where = strings_at + len(tail)
            struct.pack_into("<I", table, pos, where - (table_at + pos))
            tail += struct.pack("<I", len(text)) + text + b"\0"
            tail += b"\0" * ((-len(tail)) % 4)
        else:
            struct.pack_into("<" + kind, table, pos, value)
    return struct.pack("<I", table_at) + vtable + bytes(table) + bytes(tail)


# -- protobuf ---------------------------------------------------------------------------------------------------
def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        byte, n = n & 0x7F, n >> 7
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _field(number: int, value) -> bytes:
    if isinstance(value, bool) or isinstance(value, int):
        return _varint(number << 3) + _varint(int(value))
    if isinstance(value, float):
        return _varint(number << 3 | 5) + struct.pack("<f", value)
    data = value.encode() if isinstance(value, str) else value
    return _varint(number << 3 | 2) + _varint(len(data)) + data


def _fields(data: bytes):
    """(number, value) of a protobuf message: bytes for length-delimited fields, ints for varints."""
    i = 0
    while i < len(data):
        key, shift = 0, 0
        while True:
            byte = data[i]; i += 1
            key |= (byte & 0x7F) << shift; shift += 7
            if not byte & 0x80:
                break
        number, kind = key >> 3, key & 7
        if kind == 0:
            value, shift = 0, 0
            while True:
                byte = data[i]; i += 1
                value |= (byte & 0x7F) << shift; shift += 7
                if not byte & 0x80:
                    break
        elif kind == 2:
            length, shift = 0, 0
            while True:
                byte = data[i]; i += 1
                length |= (byte & 0x7F) << shift; shift += 7
                if not byte & 0x80:
                    break
            value, i = data[i:i + length], i + length
        elif kind == 5:
            value, i = data[i:i + 4], i + 4
        elif kind == 1:
            value, i = data[i:i + 8], i + 8
        else:
            raise DrawThingsError(f"Draw Things sent a message this app cannot read (wire type {kind}).")
        yield number, value


def request(prompt: str, config: bytes, negative: str = "", image: bytes | None = None,
            references: list[bytes] | None = None) -> bytes:
    """An ImageGenerationRequest: the canvas (image), and reference pictures as one "shuffle" (moodboard) hint."""
    body = b""
    if image is not None:
        body += _field(1, image)
    body += _field(2, 1)  # scale factor
    if references:
        hint = _field(1, "shuffle") + b"".join(_field(2, _field(1, tensor) + _field(2, 1.0)) for tensor in references)
        body += _field(4, hint)
    body += _field(5, prompt) + (_field(6, negative) if negative else b"") + _field(7, config) + _field(10, "FM Club Archive")
    body += _field(11, 2)  # a laptop
    return body


# -- pictures as tensors ------------------------------------------------------------------------------------------
def to_tensor(png: bytes, width: int, height: int) -> bytes:
    """A picture as a Draw Things tensor at width x height (cropped to that shape from the centre, then resized)."""
    from PIL import Image, ImageOps

    picture = ImageOps.fit(Image.open(io.BytesIO(png)).convert("RGB"), (width, height), Image.LANCZOS)
    values = (np.asarray(picture, dtype=np.float32) / 127.5 - 1.0).astype("<f2")
    header = struct.pack("<9I", 0, _CPU_MEMORY, _NHWC, _FLOAT16, 0, 1, height, width, 3) + b"\0" * (HEADER - 36)
    return header + values.tobytes()


def from_tensor(data: bytes) -> bytes:
    """A Draw Things tensor (a generated picture) as PNG bytes."""
    from PIL import Image

    words = struct.unpack_from("<9I", data)
    if words[0] == _COMPRESSED:
        raise DrawThingsError("Draw Things sends compressed pictures: turn off response compression in its API server settings.")
    height, width, channels = words[6], words[7], words[8]
    values = np.frombuffer(data, dtype="<f2", count=height * width * channels, offset=HEADER).astype(np.float32)
    pixels = np.clip((values.reshape(height, width, channels) + 1.0) * 127.5, 0, 255).astype(np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(pixels[:, :, :3] if channels >= 3 else pixels[:, :, 0], "RGB" if channels >= 3 else "L").save(buffer, format="PNG")
    return buffer.getvalue()


# -- the call ---------------------------------------------------------------------------------------------------
def _frame(message: bytes) -> bytes:
    return b"\0" + struct.pack(">I", len(message)) + message


def _messages(stream: bytes):
    i = 0
    while i + 5 <= len(stream):
        if stream[i]:
            raise DrawThingsError("Draw Things sent a compressed gRPC message.")
        length = struct.unpack_from(">I", stream, i + 1)[0]
        yield stream[i + 5:i + 5 + length]
        i += 5 + length


def call(url: str, method: str, message: bytes, timeout: int = 900) -> list[bytes]:
    """One gRPC call over HTTP/2 with curl; the server's messages. ``url`` is http://host:port (or https:// with TLS,
    whose self-signed certificate is accepted)."""
    url = url.rstrip("/")
    tls = url.startswith("https://")
    with tempfile.TemporaryDirectory() as folder:
        headers = Path(folder) / "headers"
        command = ["curl", "-s", "-S", "-X", "POST", "--max-time", str(timeout), "-D", str(headers),
                   "-H", "content-type: application/grpc", "-H", "te: trailers", "--data-binary", "@-", f"{url}/{SERVICE}/{method}"]
        command[1:1] = ["--http2", "-k"] if tls else ["--http2-prior-knowledge"]
        try:
            done = subprocess.run(command, input=_frame(message), capture_output=True, timeout=timeout + 30)
        except subprocess.TimeoutExpired as exc:
            raise DrawThingsError(f"Draw Things took too long at {url}.") from exc
        if done.returncode:
            raise DrawThingsError(f"Draw Things did not answer at {url} (gRPC): {done.stderr.decode(errors='replace').strip()}")
        status, why = "0", ""
        for line in headers.read_text(errors="replace").splitlines():
            name, _, value = line.partition(":")
            if name.strip().lower() == "grpc-status":
                status = value.strip()
            elif name.strip().lower() == "grpc-message":
                why = value.strip()
    if status != "0":
        raise DrawThingsError(f"Draw Things refused the picture (gRPC status {status}): {why or 'no reason given'}")
    return list(_messages(done.stdout))


def models(url: str, timeout: int = 5) -> list[str]:
    """The server's model files (its Echo answer), which also says it is there; raises DrawThingsError when it is not."""
    found = []
    for answer in call(url, "Echo", _field(1, "FM Club Archive"), timeout):
        found += [value.decode(errors="replace") for number, value in _fields(answer) if number == 2]
    return found


def online(url: str) -> bool:
    try:
        models(url, timeout=3)
        return True
    except DrawThingsError:
        return False


def generate(url: str, prompt: str, model: str, seed: int, width: int = 1024, height: int = 1024, steps: int | None = None,
             negative: str = "", image: bytes | None = None, references: list[bytes] | None = None, strength: float = 1.0,
             timeout: int = 900) -> bytes:
    """One picture (PNG): from the prompt, or with a canvas picture (image) and reference pictures (references) given
    as PNG bytes. Width and height are rounded down to Draw Things' 64-pixel steps. The model's family settings apply."""
    width, height = max(64, width // 64 * 64), max(64, height // 64 * 64)
    family = settings_for(model)
    config = configuration(**{**family, "start_width": width // 64, "start_height": height // 64, "seed": seed % 4_294_967_295,
                              "steps": steps or family["steps"], "strength": strength, "model": model})
    message = request(prompt, config, negative, to_tensor(image, width, height) if image else None,
                      [to_tensor(r, width, height) for r in references or []])
    pictures, chunks = [], b""
    for answer in call(url, "GenerateImage", message, timeout):
        fields = list(_fields(answer))
        if any(number == 1 for number, _ in fields):  # (signposts and previews come in other messages)
            chunks += b"".join(value for number, value in fields if number == 1)
            if dict(fields).get(8, 0) == 0:  # chunk state: 0 is the last chunk (proto3 leaves a 0 out)
                pictures.append(chunks)
                chunks = b""
    if not pictures:
        raise DrawThingsError("Draw Things finished but sent no picture.")
    return from_tensor(pictures[0])


def edit(url: str, prompt: str, model: str, reference: bytes, seed: int, width: int = 1024, height: int = 1024,
         negative: str = "", timeout: int = 1800) -> bytes:
    """A new picture from a reference picture (its face kept): on the canvas for Kontext, as a moodboard picture with no
    canvas for the newer edit models, so nothing of an earlier picture stays."""
    if reads_the_canvas(model):
        return generate(url, prompt, model, seed, width, height, negative=negative, image=reference, timeout=timeout)
    return generate(url, prompt, model, seed, width, height, negative=negative, references=[reference], timeout=timeout)
