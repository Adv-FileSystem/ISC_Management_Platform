"""ISC가 관리하는 Monte Carlo 유럽형 옵션 가격 연산 호스트 로직.

옵션 묶음을 그대로 장치에 쓰고 커널을 한 번 호출한다. 커널이 옵션마다 엔진을 부른다.
"""
from __future__ import annotations

from mc_format import HEADER, check

ARG_OPTIONS, ARG_PRICES, ARG_COUNT, ARG_SAMPLES, ARG_STEPS = range(5)


def check_input(data: bytes, parameters: dict) -> None:
    check(data)


def execute(session, data: bytes, parameters: dict) -> bytes:
    count = check(data)
    body = data[HEADER.size:]
    bo_options = session.buffer(len(body), arg=ARG_OPTIONS)
    bo_prices = session.buffer(4 * count, arg=ARG_PRICES)
    bo_options.write(body)
    session.run(bo_options, bo_prices, count, int(parameters["samples"]), int(parameters["time_steps"]))
    session.metric("options", count)
    session.metric("samples", int(parameters["samples"]))
    return bo_prices.read(4 * count)
