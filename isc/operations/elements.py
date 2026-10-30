"""Element types a user function can take or return, and how they travel as bytes.

Values cross the host/device boundary as a flat little-endian array of one element
type. The user spells that type in C++ (`ap_uint<32>`, `uint32_t`, `float`); this
module maps every accepted spelling to one canonical element.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass


@dataclass(frozen=True)
class Element:
    name: str       # canonical: u8 u16 u32 u64 i8 i16 i32 i64 f32
    size: int       # bytes
    fmt: str        # struct code
    signed: bool
    is_float: bool
    standard: str   # C++ spelling used in generated starter files

    def pack(self, values) -> bytes:
        return struct.pack(f"<{len(values)}{self.fmt}", *values)

    def unpack(self, data: bytes) -> tuple:
        if len(data) % self.size:
            raise ValueError(
                f"{len(data)} bytes is not a whole number of {self.name} values "
                f"({self.size} bytes each)"
            )
        return struct.unpack(f"<{len(data) // self.size}{self.fmt}", data)


@dataclass(frozen=True)
class TypeUse:
    spelling: str   # as the user wrote it, whitespace removed
    element: Element


_INTEGER_FORMATS = {8: ("B", "b"), 16: ("H", "h"), 32: ("I", "i"), 64: ("Q", "q")}


def _build() -> tuple[dict[str, Element], dict[str, str]]:
    elements: dict[str, Element] = {}
    spellings: dict[str, str] = {}
    for bits, (unsigned_fmt, signed_fmt) in _INTEGER_FORMATS.items():
        size = bits // 8
        elements[f"u{bits}"] = Element(f"u{bits}", size, unsigned_fmt, False, False, f"uint{bits}_t")
        elements[f"i{bits}"] = Element(f"i{bits}", size, signed_fmt, True, False, f"int{bits}_t")
        spellings.update({
            f"ap_uint<{bits}>": f"u{bits}", f"uint{bits}_t": f"u{bits}",
            f"ap_int<{bits}>": f"i{bits}", f"int{bits}_t": f"i{bits}",
        })
    elements["f32"] = Element("f32", 4, "f", True, True, "float")
    spellings["float"] = "f32"
    return elements, spellings


ELEMENTS, _SPELLINGS = _build()


def lookup(text: str) -> TypeUse | None:
    spelling = re.sub(r"\s+", "", text)
    name = _SPELLINGS.get(spelling)
    return TypeUse(spelling, ELEMENTS[name]) if name else None


def spellings_for(names) -> list[str]:
    wanted = set(names)
    return sorted(spelling for spelling, name in _SPELLINGS.items() if name in wanted)
