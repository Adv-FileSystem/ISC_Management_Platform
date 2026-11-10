# `graph_renumber` — 그래프 분석 전 ID를 0부터 연속 번호로 바꾸기 (연산 선택형)

| 개발 방식 | 사용하는 라이브러리 함수 | 검증 상태 |
|---|---|---|
| 연산 선택형 | Vitis Graph L2 `initRenumber` + `renumberCore` (`kernel/renumber_kernel.cpp`) | 플랫폼 자체 테스트 ✅ · 공식 라이브러리 테스트 ✅ (검증 장치 AMD Alveo U50 · [검증 기록](../../../verification/u50_vitis_libraries/results/graph_renumber.md)) |

## 1. 연산 예시

회원 관계 그래프를 분석하는 데이터 엔지니어가 분석 전에 듬성듬성한 그룹(또는 정점) 번호를 0부터 연속 번호로 바꿔, 배열 크기와 메모리를 줄입니다. 번호는 처음 등장한 순서대로 0, 1, 2, …로 매깁니다. 이 재번호화를 CPU 대신 FPGA에서 합니다.

### 1.1. 분석 전처리 코드 안에서 직접 번호를 매기는 기존 방식

```python
import pandas as pd

ids = pd.read_csv("members.csv")["group_id"]
new_ids, uniques = pd.factorize(ids)
print(len(uniques), "groups")
```

### 1.2. ISC 전주기 시스템 기반의 방식

```bash
python3 isc/operations/graph_renumber/examples/make_input.py -o members.bin      # 번호 목록 → 연산 입력 형식 (2.1에서 내 데이터를 읽도록 수정)
isc new member_renumber --operation graph_renumber                                # 처음 한 번: 모듈 만들기
isc run member_renumber --input members.bin --output members.renum --target hw    # FPGA가 번호를 다시 매김
```

- 입력: `u32 N` + `i32 id[N]` (little-endian). 2.1의 스크립트가 만들어 줍니다
- **원본 번호는 0 이상 N 미만이어야 합니다.** 커널 내부 대응표가 N칸이기 때문입니다. 실제 회원번호처럼 N보다 큰 ID는 그대로 넣을 수 없고, CPU에서 먼저 범위를 줄여야 합니다(그 단계에서 이미 번호가 정해지므로 이 연산의 이득이 줄어듭니다)
- 출력: `u32 clusters` + `i32 new_id[N]` — 입력 순서 그대로, 각 원본 번호의 새 번호(0 ~ clusters-1). 1.1의 `pd.factorize`와 같은 규칙(처음 등장한 순서)
- 실제로 따라 할 절차는 2절(개발 절차)과 3절(실행 방법)에 있습니다.

## 2. 개발 절차

모든 명령은 저장소 루트에서 실행합니다.

### 2.1. 입력 준비

**사용자가 할 일**: 번호 목록을 이 연산의 입력 형식으로 변환합니다. 번호가 0 이상 N 미만인지 확인합니다. 내 데이터를 쓰려면 [`examples/make_input.py`](examples/make_input.py)의 `make_sample()`이 내 데이터를 읽도록 고칩니다.

예시(회원 5,000명이 0~4999 범위의 성긴 그룹 번호 300종 중 하나를 가진 목록, 고정 시드)로 먼저 해 보려면:

```bash
python3 isc/operations/graph_renumber/examples/make_input.py         # → datasets/graph_renumber/members_5000.bin
```

- 저장 위치를 바꾸려면 `-o 경로`를 붙입니다.

[`examples/make_input.py`](examples/make_input.py) 전체 코드입니다(주석 제외). `make_sample()`만 고치고, `encode()`(입력 형식, 범위 검사 포함)와 `main()`은 그대로 둡니다. `make_sample()`은 번호 목록(list)을 돌려주면 됩니다.

```python
import argparse
import random
import struct
from pathlib import Path

DEFAULT_OUTPUT = "datasets/graph_renumber/members_5000.bin"


def make_sample() -> list:
    rng = random.Random(3); n = 5000
    groups = rng.sample(range(n), 300)
    return [rng.choice(groups) for _ in range(n)]


def encode(ids: list) -> bytes:
    n = len(ids)
    bad = [v for v in ids if not 0 <= v < n]
    if bad:
        raise ValueError(f"번호는 0 이상 {n} 미만이어야 합니다 (예: {bad[0]})")
    return struct.pack("<I", n) + struct.pack(f"<{n}i", *ids)


def main() -> None:
    parser = argparse.ArgumentParser(description="graph_renumber 입력 파일 만들기")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    ids = make_sample()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(encode(ids))
    print("ids", len(ids), "distinct", len(set(ids)))


if __name__ == "__main__":
    main()
```

수정 예 — 내 CSV의 그룹 번호 열 읽기 (위 전체 코드에서 `make_sample()`만 이것으로 바꿈):

```python
def make_sample() -> list:
    with open("/data/members/members.csv") as f:
        next(f)
        return [int(line.split(",")[1]) for line in f]
```

### 2.2. 모듈 만들기

**사용자가 할 일**: 모듈 이름을 정합니다. 이 연산은 파라미터가 없습니다.

```bash
isc new member_renumber --operation graph_renumber
```

`modules/member_renumber/module.yaml`이 만들어지며 고칠 것은 없습니다.

### 2.3. 로직 작성

**사용자가 할 일**: 없음. 번호를 다시 매기는 연산이라 사용자가 바꿀 값이 없으므로, 사용자 로직 자리를 두지 않습니다.

### 2.4. 검사

**사용자가 할 일**: 빌드하기 전에 모듈 설정을 검사합니다. 문제가 없으면 `PASS`가 표시됩니다.

```bash
isc check member_renumber
```

## 3. 실행 방법

**사용자가 할 일**: `--input`에 2.1의 파일, `--output`에 결과를 저장할 경로를 지정합니다. 결과 확인 명령에도 같은 입력 파일을 `--input`으로 지정하고, 대응표가 필요하면 `--map`에 저장할 CSV 경로를 지정합니다.

```bash
# 에뮬레이션 실행 (카드 없이, 처음 한 번 수 분 빌드)
isc run member_renumber --input datasets/graph_renumber/members_5000.bin --output datasets/graph_renumber/member_renumber.emu.bin

# 결과 확인: CPU 참조(baseline.py)와 바이트 비교 + 일대일 대응 확인 — MATCH면 정상
python3 isc/operations/graph_renumber/examples/check_output.py datasets/graph_renumber/member_renumber.emu.bin --input datasets/graph_renumber/members_5000.bin

# FPGA 카드 실행 (hw) (처음 한 번 hw 빌드 약 53분) — 카드 출력이 에뮬레이션 출력과 같은지 플랫폼이 자동 비교
isc run member_renumber --input datasets/graph_renumber/members_5000.bin --output datasets/graph_renumber/member_renumber.hw.bin --target hw
python3 isc/operations/graph_renumber/examples/check_output.py datasets/graph_renumber/member_renumber.hw.bin --input datasets/graph_renumber/members_5000.bin --map datasets/graph_renumber/id_map.csv

# 실행 기록
isc records --module member_renumber
isc show [실행 ID]
```

- `--map`으로 저장한 CSV(`old_id,new_id`)가 원본 번호 → 새 번호 대응표입니다.
- 다른 목록은 2.1처럼 변환한 뒤 `--input`·`--output`만 바꿔 다시 실행합니다. 모듈은 그대로 재사용하고 다시 빌드하지 않습니다.
- hw 빌드는 다른 hw 빌드와 동시에 돌리면 메모리가 부족해 멈출 수 있으므로, 한 번에 하나씩 빌드합니다.
- 터미널 출력을 파일로도 남기려면 명령 뒤에 `2>&1 | tee [로그 파일]`을 붙입니다.
