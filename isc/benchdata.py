"""벤치마크 입력 — 크기별로 결정적으로 만들고 캐시한다.

성능 곡선은 데이터 성질에 좌우된다. 압축 연산은 특히 그렇다: 난수는 전혀
압축되지 않고 0 으로 채운 버퍼는 극단적으로 잘 압축된다. 그래서 채움 방식을
숨긴 기본값으로 두지 않고 실험 정의에서 명시적으로 고르게 한다.

같은 (채움 방식, 크기) 는 언제나 같은 바이트를 낸다 -- 어제 잰 수치와 오늘 잰
수치를 비교할 수 없다면 벤치마크가 아니다.
"""
from __future__ import annotations

import random
import re
from pathlib import Path

from isc.workspace import Workspace

FILL_MODES = ("text", "random", "zeros")
DEFAULT_FILL = "text"

_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([KMGT]?)(?:I?B)?\s*$", re.IGNORECASE)
_UNITS = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}

_WORDS = ("isc", "module", "kernel", "buffer", "device", "stream", "compress",
          "record", "validate", "platform", "lifecycle", "artifact", "operation",
          "throughput", "latency", "baseline", "fingerprint", "manifest")
_TEXT_CHUNK = 1 << 20  # 1 MiB 기본 블록을 만들어 되풀이한다


def parse_size(value: str | int) -> int:
    """"64KB" -> 65536. 접미사 없는 값은 바이트, K/M/G 는 1024 진법이다."""
    if isinstance(value, int):
        size = value
    else:
        match = _SIZE.match(str(value))
        if not match:
            raise ValueError(
                f"입력 크기 {value!r} 를 해석할 수 없습니다 (예: 4096, 64KB, 16MB)"
            )
        size = int(float(match.group(1)) * _UNITS[match.group(2).upper()])
    if size <= 0:
        raise ValueError(f"입력 크기는 1 바이트 이상이어야 합니다: {value!r}")
    return size


def format_size(size: int) -> str:
    for unit, scale in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if size >= scale and size % scale == 0:
            return f"{size // scale}{unit}"
    return f"{size}B"


def ensure(workspace: Workspace, size: int, fill: str = DEFAULT_FILL) -> Path:
    """(크기, 채움 방식) 에 해당하는 입력 파일을 만들거나 캐시에서 돌려준다."""
    if fill not in FILL_MODES:
        raise ValueError(f"input_fill 은 {list(FILL_MODES)} 중 하나여야 합니다: {fill!r}")
    path = workspace.benchdata_dir / f"{fill}-{size}.bin"
    if path.exists() and path.stat().st_size == size:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".partial")
    temporary.write_bytes(generate(size, fill))
    temporary.replace(path)
    return path


def generate(size: int, fill: str = DEFAULT_FILL) -> bytes:
    seed = f"{fill}:{size}"
    if fill == "zeros":
        return bytes(size)
    if fill == "random":
        return random.Random(seed).randbytes(size)
    return _text(size, seed)


def _text(size: int, seed: str) -> bytes:
    """압축 가능한 단어 스트림.

    1 MiB 기본 블록을 만들어 되풀이하되 되풀이마다 시작점을 옮긴다. 128 MB 를
    단어 단위로 생성하면 몇 분이 걸리고, 그렇다고 그대로 되풀이하면 블록 간
    완전 중복이 생겨 압축률이 부풀려진다.
    """
    rng = random.Random(seed)
    base = bytearray()
    while len(base) < min(size, _TEXT_CHUNK):
        base += " ".join(rng.choice(_WORDS) for _ in range(12)).encode() + b"\n"
    base = bytes(base)

    if size <= len(base):
        return base[:size]

    out = bytearray()
    shift = 0
    while len(out) < size:
        shift = (shift + 4093) % len(base)  # 4093: base 길이와 서로소인 소수
        out += base[shift:] + base[:shift]
    return bytes(out[:size])
