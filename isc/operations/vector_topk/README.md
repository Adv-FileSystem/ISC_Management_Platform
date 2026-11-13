# `vector_topk` — 사내 문서 검색: 열람 권한을 반영한 상위 k개 문서 찾기 (연산 선택형 + 사용자 로직)

| 개발 방식 | 사용하는 라이브러리 함수 | 검증 상태 |
|---|---|---|
| 연산 선택형 + 사용자 로직 `isc_score` | 없음 — 플랫폼 자체 HLS 커널 (`kernel/vector_topk_kernel.cpp`). 내적 계산 뒤 문서마다 사용자 함수를 실행 | 플랫폼 자체 테스트 ✅ (1.0 사용자 로직 없음: hw_emu·hw / 1.1 사용자 로직: hw_emu, hw 미실행) · 공식 라이브러리 테스트 해당 없음 (플랫폼 전용) (검증 장치 AMD Alveo U50 · [검증 기록](../../../verification/u50_vitis_libraries/results/vector_topk.md)) |

## 1. 연산 예시

사내 문서 검색(RAG) 서비스를 만드는 개발자가 질의 임베딩과 가장 가까운 문서 5개를 찾습니다. 문서 임베딩은 int8로 양자화해 저장해 두었습니다. 사용자가 열람 권한이 없는 문서는 아무리 가까워도 결과에 나오면 안 되므로, 순위를 매기기 전에 그런 문서를 뺍니다.

내적과 상위 k 계산은 플랫폼 커널이 하고, 문서별 점수 규칙(제외·가산점)은 사용자가 C++ 함수 하나로 작성합니다. 두 단계 모두 FPGA에서 실행됩니다.

### 1.1. 검색 서비스 코드 안에서 직접 계산하는 기존 방식

```python
import numpy as np

db = np.load("doc_embeddings_i8.npy").astype(np.int32)
query = np.load("query_i8.npy").astype(np.int32)
scores = db @ query
scores[:200] = np.iinfo(np.int32).min
top5 = np.argsort(-scores, kind="stable")[:5]
```

### 1.2. ISC 전주기 시스템 기반의 방식

```bash
python3 isc/operations/vector_topk/examples/make_input.py -o docs.bin               # 질의 + 문서 임베딩 → 연산 입력 형식 (2.1에서 내 데이터를 읽도록 수정)
isc new doc_search --operation vector_topk --set k=5 --logic                        # 처음 한 번: 모듈과 사용자 로직 파일(logic.hpp) 만들기
# modules/doc_search/logic.hpp 의 return 줄을 점수 규칙으로 수정
isc check doc_search                                                                # 사용자 로직 검사
isc run doc_search --input docs.bin --output top5.bin --target hw                   # FPGA가 내적·상위 5개 계산
```

- 입력: 이진 형식 `isc_vectors_i8` (모두 little-endian, 행 우선): `u32 rows`, `u32 dim`, `i8 query[dim]`, `i8 db[rows][dim]`. 2.1의 스크립트가 만들어 줍니다. `1 ≤ dim ≤ 2048`, 파일 크기는 정확히 `8 + dim + rows×dim` 바이트
- 출력: `k`개의 `(u32 id, i32 score)` = `8×k` 바이트. 점수는 사용자 함수 `isc_score`가 돌려준 값이고, 점수 내림차순, 같으면 id 오름차순입니다. 1.1의 `top5`와 같은 순서
- 사용자 로직 없이(`--logic` 생략) 만들면 내적 그대로 순위를 매깁니다
- 점수는 `Σ query[j] × db[id][j]`(int8 곱의 int32 합)이며, **코사인 유사도나 float 내적이 아닙니다**. float 임베딩은 int8로 양자화해 넣어야 하고, 이 양자화는 근사라서 float 코사인 검색과 순위가 다를 수 있습니다
- `rows < k`이면 남는 칸은 `id = 0xFFFFFFFF`, `score = -2147483648`입니다
- 출력 id는 입력 파일 안의 **행 번호**(0부터)입니다. 실제 문서 id와의 대응표는 사용자가 따로 보관합니다
- 질의 여러 개를 한 번에, 근사 최근접 탐색(ANN) 색인은 지원하지 않습니다
- 실제로 따라 할 절차는 2절(개발 절차)과 3절(실행 방법)에 있습니다.

## 2. 개발 절차

모든 명령은 저장소 루트에서 실행합니다. 이 연산은 `VITIS_LIBRARIES`가 필요 없습니다. 입력 변환과 결과 확인에 Python `numpy`가 필요합니다.

### 2.1. 입력 준비

**사용자가 할 일**: 질의와 문서 임베딩을 int8로 준비해 이 연산의 입력 형식으로 변환합니다. 내 데이터를 쓰려면 [`examples/make_input.py`](examples/make_input.py)의 `make_sample()`이 내 데이터를 읽도록 고칩니다.

예시(문서 2,000개 × 64차원 int8 임베딩, 고정 시드. 질의는 123번 문서와 같은 벡터라 정답 1위가 123번)로 먼저 해 보려면:

```bash
python3 isc/operations/vector_topk/examples/make_input.py            # → datasets/vector_topk/docs.bin
```

- 저장 위치를 바꾸려면 `-o 경로`를 붙입니다.

[`examples/make_input.py`](examples/make_input.py) 전체 코드입니다(주석 제외). `make_sample()`만 고치고, `encode()`(입력 형식, 길이 검사 포함)와 `main()`은 그대로 둡니다. `make_sample()`은 `query`(길이 `dim`)와 `db`(`rows × dim`)를 int8로 돌려주면 됩니다.

```python
import argparse
import struct
from pathlib import Path

import numpy as np

DEFAULT_OUTPUT = "datasets/vector_topk/docs.bin"


def make_sample():
    rng = np.random.default_rng(42)
    rows, dim = 2000, 64
    db = rng.integers(-128, 128, size=(rows, dim), dtype=np.int8)
    query = db[123].copy()
    return query, db


def encode(query, db) -> bytes:
    query, db = np.asarray(query, dtype=np.int8), np.asarray(db, dtype=np.int8)
    rows, dim = db.shape
    if query.shape != (dim,):
        raise ValueError(f"질의 길이({query.shape})가 문서 차원({dim})과 다릅니다")
    return struct.pack("<II", rows, dim) + query.tobytes() + db.tobytes()


def main() -> None:
    parser = argparse.ArgumentParser(description="vector_topk 입력 파일 만들기")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    query, db = make_sample()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(encode(query, db))


if __name__ == "__main__":
    main()
```

수정 예 — float 임베딩을 읽어 int8로 양자화하기 (행마다 L2 정규화 후 ×127. 근사이므로 float 코사인 검색과 순위가 다를 수 있음. 위 전체 코드에서 `make_sample()`만 이것으로 바꿈):

```python
def make_sample():
    def to_i8(x):
        x = x / np.linalg.norm(x, axis=-1, keepdims=True)
        return np.clip(np.round(x * 127), -127, 127).astype(np.int8)
    db = to_i8(np.load("/data/rag/doc_embeddings.npy"))
    query = to_i8(np.load("/data/rag/query.npy"))
    return query, db
```

### 2.2. 모듈 만들기

**사용자가 할 일**: 모듈 이름과 돌려받을 개수(`k`, 1–16, 기본 10)를 정합니다. 필요하면 커널 한 번이 처리할 문서 행렬 크기(`chunk_bytes`, 4096–134217728, 기본 16 MiB)도 정합니다. 점수 규칙을 넣으려면 `--logic`을 붙입니다.

```bash
isc new doc_search --operation vector_topk --set k=5 --logic
```

- `modules/doc_search/` 폴더에 `module.yaml`과 사용자 로직 파일 `logic.hpp`가 만들어집니다.
- 로직 파일을 먼저 작성한 뒤 모듈을 만들어도 됩니다. `isc template vector_topk -o access.hpp`로 시작 파일을 받아 2.3처럼 고친 다음, `--logic access.hpp`로 모듈을 만듭니다.
- 내적 그대로 순위만 필요하면 `--logic`을 빼고 만들고, 2.3은 건너뜁니다.

- 두 값은 나중에 `modules/doc_search/module.yaml`로도 바꿀 수 있고, 바꿔도 다시 빌드하지 않습니다.
- `chunk_bytes`를 바꿔도 결과는 같습니다. 호스트가 청크별 결과를 합쳐 다시 정렬하기 때문입니다.

### 2.3. 로직 작성

**사용자가 할 일**: `modules/doc_search/logic.hpp`를 열어 함수 `isc_score`의 `return` 줄을 점수 규칙으로 바꾸고, 그 줄의 `ISC-TODO` 주석을 지웁니다.

아래는 "0~199번 문서는 열람 권한이 없으므로 결과에서 제외" 규칙으로 고친 파일 전체입니다.

```cpp
#pragma once

#include <stdint.h>

inline int32_t isc_score(uint32_t id, int32_t dot) {
    return id < 200 ? INT32_MIN : dot;
}
```

- **호출 시점**: 질의와 문서 하나의 내적을 계산할 때마다, 순위를 매기기 전에 한 번씩 호출됩니다. 함수가 돌려준 값으로 순위를 매깁니다.
- **입력값**: `id`는 문서의 행 번호(0부터), `dot`은 질의와 그 문서의 정수 내적입니다.
- **제외하는 방법**: `INT32_MIN`을 돌려주면 그 문서는 결과에 나오지 않습니다.
- **바꾸면 안 되는 것**: 함수 이름(`isc_score`), 인자 타입(`uint32_t`, `int32_t`)과 반환 타입(`int32_t`). 인자 이름은 바꿔도 됩니다.
- **쓸 수 있는 것**: 비교·사칙·비트 연산, 상수, 조건식. 보조 함수나 상수를 더 정의해도 됩니다. 예:
  - `return id >= 1500 ? dot + 5000 : dot;` — 1500번 이후 문서(최신 문서)에 가산점
  - `return dot < 50000 ? INT32_MIN : dot;` — 내적이 기준보다 낮은 문서는 제외
- **쓸 수 없는 것**: 동적 메모리 할당(`new`, `malloc`), 파일·네트워크 입출력(`fopen`, `open`, `socket`), `#pragma HLS INTERFACE`, 다른 문서의 값 참조.
- 파일을 고치지 않고 두면(`return dot;`) 원래 연산과 같은 결과, 즉 내적 그대로의 순위가 나옵니다.

### 2.4. 검사

**사용자가 할 일**: 빌드하기 전에 모듈 설정과 로직 파일을 검사합니다. 문제가 없으면 `PASS`가 표시됩니다.

```bash
isc check doc_search
```

- 함수 이름·인자·반환 타입이 다르거나 쓸 수 없는 문법이 있으면 `problem`이 표시되고, 고칠 때까지 빌드와 실행이 진행되지 않습니다.
- `ISC-TODO` 주석이 남아 있으면 `warning`이 표시됩니다. 빌드는 진행됩니다.

## 3. 실행 방법

**사용자가 할 일**:

- `--input`에 2.1의 파일, `--output`에 결과를 저장할 경로를 지정합니다. 결과 확인 명령에도 같은 입력 파일(`--input`)과 2.2의 `k`(`--k`)를 지정합니다(예시 파일이면 생략).
- 결과 확인 스크립트 [`examples/check_output.py`](examples/check_output.py)의 `[바꾸는 곳]`에 있는 `score()`를 2.3과 같은 규칙으로 맞춥니다. 2.3의 예시 규칙을 그대로 썼다면 고칠 것이 없습니다.
- 2.2에서 `--logic` 없이 모듈을 만들었다면, 결과 확인 명령에 `--original`을 붙입니다.

```bash
# 에뮬레이션 실행 (카드 없이, 처음 한 번 수 분 빌드). 입력 형식이 맞지 않으면 빌드 전에 실패함
isc run doc_search --input datasets/vector_topk/docs.bin --output datasets/vector_topk/result.emu.bin

# 결과 확인: numpy로 계산한 내적에 같은 규칙을 적용하고 같은 정렬 규칙으로 고른 결과와 비교, MATCH가 나오면 정상
python3 isc/operations/vector_topk/examples/check_output.py datasets/vector_topk/result.emu.bin --input datasets/vector_topk/docs.bin --k 5

# FPGA 카드 실행 (hw) (처음 한 번 hw 빌드 수십 분) — 카드 출력이 에뮬레이션 출력과 같은지 플랫폼이 자동 비교
isc run doc_search --input datasets/vector_topk/docs.bin --output datasets/vector_topk/result.hw.bin --target hw
python3 isc/operations/vector_topk/examples/check_output.py datasets/vector_topk/result.hw.bin --input datasets/vector_topk/docs.bin --k 5

# 실행 기록 (rows, dim, k, chunks 등 실행 지표 포함)
isc records --module doc_search
isc show [실행 ID]
```

결과 확인 스크립트는 numpy로 내적을 계산한 뒤, 아래 `score()` 함수를 문서마다 적용해 FPGA 출력과 비교합니다. `score()`는 2.3의 규칙을 Python으로 옮긴 것이므로, 2.3의 규칙을 바꾸면 이 함수도 함께 바꿉니다.

```python
INT32_MIN = -2147483648


def score(doc_id: int, dot: int) -> int:
    return INT32_MIN if doc_id < 200 else dot
```

- 예시 데이터에서 규칙 없이 검색하면 1·3·4위가 0~199번 문서(123, 79, 94번)입니다. 규칙을 넣으면 이 문서들이 빠지고 다음 문서가 올라옵니다.
- **규칙을 바꿀 때**: `logic.hpp`를 고치고 `isc run`을 다시 실행하면, 바뀐 로직으로 자동으로 다시 빌드합니다. 에뮬레이션으로 먼저 확인한 다음 FPGA 카드에서 실행합니다.
- **성능 측정**: 사용자 로직이 들어간 모듈은 `isc bench`에서 CPU 기준선과 비교하지 않습니다. 가속 효과를 측정하려면 `--logic` 없이 만든 모듈을 씁니다.
- hw_emu는 시뮬레이션이라 큰 입력은 오래 걸립니다. 수천 행 이하로 확인하고 큰 문서 집합은 hw로 실행합니다. 문서 행렬이 `chunk_bytes`보다 크면 여러 청크로 나눠 처리하고 결과를 합칩니다(`chunks` 지표).
- 질의가 여러 개면 질의마다 입력 파일을 만들어 반복 실행합니다. 모듈은 그대로 재사용하고 다시 빌드하지 않습니다.
- 터미널 출력을 파일로도 남기려면 명령 뒤에 `2>&1 | tee [로그 파일]`을 붙입니다.
