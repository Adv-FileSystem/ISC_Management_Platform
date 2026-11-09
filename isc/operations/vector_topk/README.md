# `vector_topk` — 문서 임베딩 상위 k개 검색 (연산 선택형)

| 개발 방식 | 사용자가 작성하는 것 | 커널 | 파라미터 | 플랫폼 자체 테스트 | 공식 라이브러리 테스트 |
|---|---|---|---|---|---|
| 연산 선택형 | 없음 (`isc new` 한 줄, 필요하면 `--set k=...`) | 플랫폼 자체 HLS 커널 (`kernel/vector_topk_kernel.cpp`, Vitis Libraries 미사용) | `k`, `chunk_bytes` | ✅ 통과 (2026-11-05) | — 해당 없음 |

## 1. 시나리오

| 항목 | 내용 |
|---|---|
| 사용자 | 사내 문서 검색(RAG) 서비스를 만드는 개발자 |
| 상황 | 사내 문서 검색(RAG)에서 질의 임베딩과 가장 가까운 문서 5개를 찾는다. 문서 임베딩은 int8로 양자화해 저장해 두었다 |
| 가속하는 단계 | int8 내적 상위 k |
| 입력 → 출력 | 질의 + 문서 임베딩 → (id, 점수) 5쌍 |
| 완료 기준 | numpy 내적·정렬과 일치 (id·점수·순서) |

이 연산은 결과가 하나로 정해지는 **검증용 연산**입니다(정수 내적과 전순서). 속도 가속을 목표로 만든 커널이 아닙니다.

## 2. 연산 요약

| 항목 | 내용 |
|---|---|
| 입력 | 이진 형식 `isc_vectors_i8` (모두 little-endian, 행 우선): `u32 rows`, `u32 dim`, `i8 query[dim]`, `i8 db[rows][dim]` |
| 입력 제약 | `1 ≤ dim ≤ 2048`, 파일 크기는 정확히 `8 + dim + rows×dim` 바이트. `rows = 0`도 허용 |
| 출력 | `k`개의 `(u32 id, i32 score)` = `8×k` 바이트. 점수 내림차순, 점수가 같으면 id 오름차순 |
| 점수 | `score = Σ query[j] × db[id][j]` (int8 곱의 int32 합). **코사인 유사도나 float 내적이 아님** |
| 빈 칸 | `rows < k`이면 남는 칸은 `id = 0xFFFFFFFF`, `score = -2147483648` |
| 파라미터 `k` | 돌려줄 개수, 1–16, 기본 10. 바꿔도 재빌드 없음 |
| 파라미터 `chunk_bytes` | 커널 한 번이 처리할 문서 행렬 바이트, 4096–134217728, 기본 16 MiB. 호스트가 청크별 결과를 합쳐 정렬하므로 **결과는 같고** 실행 방식만 바뀜. 재빌드 없음 |
| 지원하지 않는 것 | 질의 여러 개를 한 번에, float 벡터, 근사 최근접 탐색(ANN) 색인, 코사인 정규화 |

작은 예: `dim=2`, `query=[1, 2]`, `db=[[1,0], [0,1], [2,2]]`, `k=2` → 점수 `[1, 2, 6]` → 출력 `(2, 6), (1, 2)`

## 3. 준비

- 공통 설치와 환경 확인: [README 1절](../../../README.md#1-설치-및-환경-구성)
- 이 연산은 `VITIS_LIBRARIES`가 필요 없습니다. 예시의 결과 확인에 **numpy**가 필요합니다 (`python3 -m pip install numpy`).
- 모든 명령은 **저장소 루트**에서 실행합니다. 실습 파일은 `datasets/vector_topk/`(git에 올라가지 않음)에 만듭니다.

## 4. 개발 절차

### 4.1. 입력 만들기

문서 2,000개 × 64차원 int8 임베딩(고정 시드)을 만들고, 질의는 123번 문서와 같은 벡터로 둡니다(정답 1위가 123번이 되도록).

```bash
mkdir -p datasets/vector_topk
python3 - <<'EOF'
import struct, numpy as np
rng = np.random.default_rng(42)
rows, dim = 2000, 64
db = rng.integers(-128, 128, size=(rows, dim), dtype=np.int8)
query = db[123].copy()                                  # 123번 문서와 같은 질의
with open("datasets/vector_topk/docs.bin", "wb") as f:
    f.write(struct.pack("<II", rows, dim))
    f.write(query.tobytes())
    f.write(db.tobytes())
EOF
```

### 4.2. 모듈 만들기

```bash
isc describe vector_topk                    # 입력·출력 형식과 파라미터 확인 (선택)
isc new doc_search --operation vector_topk --set k=5
```

생성되는 `modules/doc_search/module.yaml`:

```yaml
module:
  id: doc_search          # 변경 금지
  name: doc_search        # 수정 가능
  version: 0.1.0          # 수정 가능
operation:
  id: vector_topk         # 변경 금지
  version: '1.0'          # 변경 금지
parameters:               # 수정 가능 (재빌드 없음)
  k: 5
  chunk_bytes: 16777216
```

### 4.3. 검사

```bash
isc check doc_search
```

```text
doc_search: PASS
```

### 4.4. 에뮬레이션 실행 (hw_emu)

입력 형식이 맞지 않으면 빌드 전에 실패합니다(6절).

```bash
isc run doc_search --input datasets/vector_topk/docs.bin --output datasets/vector_topk/result.bin
```

실제 출력 (발췌):

```text
doc_search [hw_emu] succeeded  run_id=20261105T110129-724876
  device     : [0] xilinx_u50_gen3x16_xdma_5_202210_1  (emulated)
  build      : cache hit  925b7908fe5599c30ea921c83207b5b3
  input      : 128072 bytes  /home/.../datasets/vector_topk/docs.bin
  output     : 40 bytes  /home/.../datasets/vector_topk/result.bin
  rows       : 2000.0
  dim        : 64.0
  k          : 5.0
  chunks     : 1.0
  validation : PASS  (cross_target) ran on the reference target itself; nothing to compare
```

hw_emu는 시뮬레이션이라 큰 입력은 오래 걸립니다. 수천 행 이하로 확인하고 큰 데이터는 hw로 실행합니다.

### 4.5. 결과 확인

출력을 `(u32 id, i32 score)` 쌍으로 풀고, numpy로 같은 정수 내적과 정렬 규칙(점수 내림차순 → id 오름차순)을 적용해 비교합니다.

```bash
python3 - <<'EOF'
import struct, numpy as np
data = open("datasets/vector_topk/docs.bin", "rb").read()
rows, dim = struct.unpack_from("<II", data)
query = np.frombuffer(data, np.int8, dim, 8).astype(np.int32)
db = np.frombuffer(data, np.int8, rows * dim, 8 + dim).reshape(rows, dim).astype(np.int32)
scores = db @ query
order = np.lexsort((np.arange(rows), -scores.astype(np.int64)))[:5]
expected = [(int(i), int(scores[i])) for i in order]
out = open("datasets/vector_topk/result.bin", "rb").read()
got = [struct.unpack_from("<Ii", out, o) for o in range(0, len(out), 8)]
for rank, (doc_id, score) in enumerate(got, 1):
    print(f"{rank}. id={doc_id} score={score}")
print("MATCH" if got == expected else "MISMATCH")
EOF
```

```text
1. id=123 score=339173
2. id=853 score=127618
3. id=79 score=123970
4. id=94 score=120678
5. id=1443 score=119919
MATCH
```

### 4.6. U50 실행 (hw)

```bash
isc run doc_search --input datasets/vector_topk/docs.bin --output datasets/vector_topk/result_hw.bin --target hw
```

플랫폼이 hw_emu 기준 출력을 먼저 만들고 → hw 빌드(처음 한 번 수십 분) → 카드 실행 → 두 출력을 바이트 비교합니다. 4.5의 코드에서 `result.bin`을 `result_hw.bin`으로 바꿔 한 번 더 확인합니다.

실제 출력 (발췌, 빌드 캐시 재사용):

```text
doc_search [hw] succeeded  run_id=20261105T111257-4c366e
  device     : [0] xilinx_u50_gen3x16_xdma_base_5
  build      : cache hit  9d596520ef4fa743f822c59baef481ee
  input      : 128072 bytes  /home/.../datasets/vector_topk/docs.bin
  output     : 40 bytes  /home/.../datasets/vector_topk/result_hw.bin
  kernel_ms  : 0.206
  chunks     : 1.0
  validation : PASS  (cross_target) matches reference target output (40 bytes)
```

`validation : PASS ... matches reference target output`는 카드 출력이 hw_emu 출력과 바이트까지 같다는 뜻입니다. 결과 확인 코드를 hw 출력에 적용한 결과:

```text
1. id=123 score=339173
2. id=853 score=127618
3. id=79 score=123970
4. id=94 score=120678
5. id=1443 score=119919
MATCH
```

### 4.7. 기록 확인

```bash
isc records --module doc_search
isc show [실행 ID]          # rows, dim, k, chunks 등 실행 지표 포함
```

## 5. 내 데이터에 적용하기

| 바꿀 것 | 방법 |
|---|---|
| 돌려받을 개수 | `modules/doc_search/module.yaml`의 `k` (1–16) 수정. 재빌드 없음 |
| 실제 임베딩 | float 임베딩은 직접 int8로 바꿔 4.1과 같은 형식으로 저장. 예: 행마다 L2 정규화 후 `np.clip(np.round(x * 127), -127, 127).astype(np.int8)`. 이 양자화는 근사이므로 float 코사인 검색과 순위가 다를 수 있음 |
| 문서 id | 출력 id는 입력 파일 안의 **행 번호**(0부터). 실제 문서 id와의 대응표는 사용자가 따로 보관 |
| 큰 문서 집합 | 그대로 hw로 실행. 문서 행렬이 `chunk_bytes`보다 크면 여러 청크로 나눠 처리하고 결과를 합침 (`chunks` 지표 확인) |
| 질의 여러 개 | 질의마다 입력 파일을 만들어 반복 실행 (한 실행에 질의 하나) |

## 6. 문제 해결

| 증상 | 원인 | 해결 |
|---|---|---|
| `vector_topk header says rows=..., dim=..., which is ... bytes; received ...` | 헤더의 `rows`·`dim`과 파일 크기가 다름 | `8 + dim + rows×dim` 바이트가 되도록 다시 저장 (질의 벡터 누락이 흔한 원인) |
| `vector_topk dim must be 1..2048, the header says ...` | 차원이 범위를 벗어남, 또는 헤더를 big-endian으로 씀 | `struct.pack("<II", rows, dim)`으로 little-endian 기록 |
| `vector_topk input needs at least 9 bytes ...` | 헤더나 질의가 없음 | 4.1 형식 확인 |
| id가 `4294967295` | `rows < k`라 남은 빈 칸 | 정상. 빈 칸은 버림 |
| numpy 결과와 순위가 다름 | int8 → int32로 넓히지 않고 곱함(넘침), 또는 동점 정렬 규칙 차이 | 4.5처럼 `astype(np.int32)` 후 곱하고, 동점은 id 오름차순 |

## 7. 검증 기록

### 7.1. 플랫폼 자체 테스트

| 항목 | 내용 |
|---|---|
| 확인일 | 2026-11-05 |
| 환경 | Ubuntu 22.04.5 LTS (커널 6.8.0-138), Python 3.10.12, Vitis 2025.1, XRT 2.19.194 (2025.1), Vitis Libraries `v2025.1_update2` (커밋 b2c657d), AMD Alveo U50 (`xilinx_u50_gen3x16_xdma_base_5`), 저장소 기준 커밋 `8742866` |
| 실행한 단계 | 이 문서의 명령을 저장소 루트에서 순서대로 그대로 실행 (입력 만들기 → 모듈 생성 → 검사 → hw_emu → 결과 확인 → hw → 결과 확인) |
| hw_emu | 통과 — 실행 `20261105T110129-724876`, numpy 정수 내적·정렬과 일치 (상위 5개 id·점수·순서) |
| hw (U50) | 통과 — 실행 `20261105T111257-4c366e`, hw 출력 = hw_emu 출력(플랫폼 자동 대조) = CPU 참조 (numpy 정수 내적·정렬과 일치) |
| 확인하지 않은 것 | 성능(가속 효과)은 측정하지 않음. 이 문서의 입력 한 가지로만 확인 |

### 7.2. 공식 라이브러리 테스트

**해당 없음** — 이 연산에 대응하는 Vitis Libraries 공식 테스트가 없습니다 (플랫폼 전용 연산).
