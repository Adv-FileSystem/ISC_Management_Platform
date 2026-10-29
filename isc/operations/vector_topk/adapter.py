"""ISC가 관리하는 벡터 Top-K 연산 호스트 로직.

커널은 한 번 호출에 데이터베이스의 한 청크만 훑어 상위 k개를 낸다. 청크 반복과 병합은
호스트의 몫이고 그것이 이 파일이다. 모든 청크가 같은 전순서 (점수 내림차순, id 오름차순)
로 순위를 매기므로 전역 top-k 는 청크별 top-k 의 합집합 안에 있다. 청크별 결과를 합쳐
다시 정렬해 앞의 k 개를 취하면 전체를 한 번에 훑은 것과 바이트까지 같다.

바이트 형식은 topk_format.py 에 있다.
"""
from __future__ import annotations

from topk_format import ENTRY, HEADER, MAX_K, check, decode, pack, pad_rows, padded_dim

# 커널 인자 인덱스 (operation.yaml의 kernel.arguments 순서와 같다)
ARG_QUERY, ARG_DB, ARG_OUT, ARG_ROWS, ARG_DIM, ARG_K, ARG_ID_BASE = range(7)


def check_input(data: bytes, parameters: dict) -> None:
    """플랫폼이 빌드를 시작하기 전에 부른다 (Manifest.validate_input)."""
    check(data)


def execute(session, data: bytes, parameters: dict) -> bytes:
    rows, dim = check(data)
    k = int(parameters["k"])
    if not 1 <= k <= MAX_K:
        raise ValueError(f"vector_topk k must be 1..{MAX_K}, got {k}")
    if rows == 0:
        return pack([], k)

    stride = padded_dim(dim)
    rows_per_call = max(1, int(parameters["chunk_bytes"]) // stride)

    bo_query = session.buffer(stride, arg=ARG_QUERY)
    bo_db = session.buffer(min(rows, rows_per_call) * stride, arg=ARG_DB)
    bo_out = session.buffer(MAX_K * ENTRY.size, arg=ARG_OUT)
    bo_query.write(data[HEADER.size:HEADER.size + dim].ljust(stride, b"\0"))

    database = HEADER.size + dim
    candidates: list[tuple[int, int]] = []
    calls = 0
    for first in range(0, rows, rows_per_call):
        count = min(rows_per_call, rows - first)
        block = data[database + first * dim:database + (first + count) * dim]
        bo_db.write(pad_rows(block, count, dim, stride))
        session.run(bo_query, bo_db, bo_out, count, stride, k, first)
        candidates += decode(bo_out.read(k * ENTRY.size))
        calls += 1

    session.metric("rows", rows)
    session.metric("dim", dim)
    session.metric("k", k)
    session.metric("chunks", calls)
    return pack(candidates, k)
