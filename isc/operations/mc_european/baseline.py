"""CPU reference for mc_european: the Black-Scholes closed form, independent of Monte Carlo.

Monte Carlo approximates this price, so the two never match byte for byte (equivalence: none).
The official L2 test judges |price - closed form| / S <= 0.01; verification uses the same rule.
"""
from __future__ import annotations

import math
import struct

from mc_format import HEADER, RECORD, options

PROBE_INPUT = HEADER.pack(1, 0) + RECORD.pack(100.0, 75.0, 0.11, 0.0, 0.01, 1.0, 0.0, 0.0)


def black_scholes(s, k, sigma, q, r, t, put):
    n = lambda x: 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
    d1 = (math.log(s / k) + (r - q + 0.5 * sigma * sigma) * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)
    if put:
        return k * math.exp(-r * t) * n(-d2) - s * math.exp(-q * t) * n(-d1)
    return s * math.exp(-q * t) * n(d1) - k * math.exp(-r * t) * n(d2)


def compute(data: bytes, parameters: dict) -> bytes:
    prices = [black_scholes(s, k, sig, q, r, t, kind == 1.0) for s, k, sig, q, r, t, kind, _ in options(data)]
    return struct.pack(f"<{len(prices)}f", *prices)
