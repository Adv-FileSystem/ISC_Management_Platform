# U50 검증 결과 요약

갱신 2026-11-04 17:00 (진행 중) · 시나리오 정의는 `../README.md` 3절, 원자료는 `facts.md`, `builds.jsonl`, `runs.jsonl`, 플랫폼 `artifacts/records.jsonl`

판정: **PASS** 정답 일치 · **FAIL** 실행했으나 불일치 · **BUILD-FAIL** hw 빌드 실패 · **진행 중** · **X** 미실시

## 1. 연산별 결과

| ID | 라이브러리 | 연산 | 경로 | 결과 | 근거 (입력 → 판정) | 빌드·클럭 |
|---|---|---|---|---|---|---|
| DB-1 | Database | 해시 조인 (GQE 3-in-1) | V | 진행 중 | 1차 빌드가 메모리 부족으로 HLS 중 강제 종료, 재빌드 대기 | |
| DB-2 | Database | 블룸 필터 | V | 진행 중 | | |
| DB-3 | Database | 해시 파티셔닝 | V | 진행 중 | | |
| DB-4 | Database | 3-in-1 조합 실행 | V | 진행 중 | | |
| DB-ref | Database | L1 해시 조인 v3 (참고) | V | **FAIL** | lineitem 6,001,215행 ⋈ orders 227,597행. FPGA 수익 합계 644,527,871,019.56 / 644,530,915,795.42 (2회, 매번 다름) vs CPU 644,618,294,596.98 | 2026-08 빌드(220 MHz 지정). 커널 클럭 220→106.8 MHz, HBM 클럭 450→440.5 MHz 자동 축소, WNS −0.048 ns |
| GR-1 | Graph | PageRank (단일 채널) | V | **PASS** | 공식 데이터, 호스트 골든 비교 "Result is correct" (11.7 s) | 2026-08 빌드, 타이밍 충족(WNS +0.016) |
| GR-1 | Graph | 다중 채널 PageRank | V | 진행 중 | | |
| GR-2 | Graph | 코사인 유사도 (General) | V | 진행 중 | | |
| GR-3 | Graph | 코사인 유사도 (Dense) | V | **PASS** | 호스트 골든 비교 "Test passed", 상위 유사도 목록 일치 | 2026-08 빌드. 커널 300→219.3 MHz, HBM 450→320.8 MHz 자동 축소 |
| GR-3 | Graph | 코사인 유사도 (Sparse) | V | 진행 중 | | |
| GR-4 | Graph | 2-Hop 경로 수 | V | **PASS** | 공식 CSR 그래프와 정점 쌍 목록으로 2-hop 경로 수 계산, 호스트 골든 비교 "Test passed" (9.7 s) | 2026-11-04 무수정 빌드 67분, 타이밍 충족(자동 축소 없음) |
| GR-5 | Graph | Louvain | V 시도 | 진행 중 | | |
| GR-6 | Graph | 정점 번호 재할당 | V | 진행 중 | | |
| GR-7 | Graph | 그래프 병합 | V | 진행 중 | | |
| CP-1 | Data Compression | GZIP 압축·해제 | V | 진행 중 | | |
| CP-2 | Data Compression | ZLIB 압축·해제 | V | 진행 중 | | |
| CP-3 | Data Compression | LIBZ API | V 시도 | 진행 중 | | |
| CP-4 | Data Compression | LZ4 압축 | V | **PASS** | 공식 `lz4_compress` 호스트: 압축 → 해제 → 원본 비교 "No errors found" | 2026-07 빌드, 타이밍 충족 |
| CP-4 | Data Compression | LZ4 (플랫폼 `lz4`) | P | PASS (기존 기록) | 2026-08 hw 검증 기록 (records.jsonl 기존 행) | |
| CP-5 | Data Compression | Snappy | V 시도 | 진행 중 | | |
| DA-1 | Data Analytics | 중복 레코드 매칭 | V | 진행 중 | 1차 빌드가 메모리 부족으로 HLS 중 강제 종료, 재빌드 대기 | |
| CO-1 | Codec | JPEG 디코딩 | V | 진행 중 | | |
| CO-2 | Codec | Bicubic resize | V | **PASS** | 512×512 → 64×64(공식 인자)·256×256·300×300 모두 hw 출력 = 같은 커널 소스의 CPU C 시뮬레이션 결과(바이트 일치). 참고: OpenCV `INTER_CUBIC`과는 평균 8~22 단계 차이. 2배 축소에서는 모서리 정렬 bicubic(원본 = 2×출력 좌표)과 정확히 같아, 차이는 좌표 정렬·가중치 정의 차이로 본다. 커널 0.23 ms, 전송 29.7 ms(64×64) | 2026-11-04 무수정 빌드, exit 0 |
| HP-1 | HPC | CG solver (GEMV + Jacobi) | V | **PASS** | N=2048 SPD(조건수 128), 85회 반복에 수렴, 정답 x와 불일치 0개 "Solution verified" (17.3 ms) | 2026-08 빌드. 커널 333→102.9 MHz 자동 축소 |
| HP-2 | HPC | FCN | V 시도 | 진행 중 | | |
| SE-1 | Security | Adler-32 (플랫폼 `adler32`) | P | **PASS** | 1 B·3 B·256 KiB: hw = hw_emu(플랫폼 자동 비교) = zlib.adler32. 64 MiB: hw = zlib.adler32 (호스트→장치 20.6 ms, 커널 14.2 ms) | 플랫폼 hw 빌드 ac55fdff (2026-11-04) |
| SE-1 | Security | Adler-32 (공식) | V | 진행 중 | | |
| SE-2 | Security | CRC32 (플랫폼 `crc32`) | P | 진행 중 | hw_emu 기준 실행 완료(256 KiB), hw 빌드 대기 | |
| SE-2 | Security | CRC32 (공식) | V 시도 | 진행 중 | | |
| VI-1 | Vision | Gaussian 3×3 (플랫폼 `image_gaussian`) | P | 진행 중 | hw_emu: 128×128에서 OpenCV와 차이 0 (허용 2) | |
| VI-2 | Vision | Bilinear resize (플랫폼 `image_resize`) | P | 진행 중 | hw_emu: 128→64에서 OpenCV와 최대 차이 1 (허용 5) | |
| VI-3 | Vision | Sobel 3×3 (플랫폼 `image_sobel`) | P | 진행 중 | C 시뮬레이션: 128·512·1080p에서 OpenCV와 바이트 일치 | |
| VI-4 | Vision | ISP 파이프라인 | X | X | 공식 호스트가 OpenCV C++ 필요(미설치, 관리자 권한 없음) | |
| QF-1 | Quantitative Finance | MCEuropeanEngine | V 시도 | 진행 중 | 1차 빌드가 메모리 부족으로 구현 중 강제 종료, 재빌드 대기 | |
| BL-1 | BLAS | GEMV int32 (플랫폼 `gemv_i32`) | P | 진행 중 | hw_emu: 64×130(α=3, β=−2)에서 numpy와 바이트 일치 | |

## 2. 제약사항과 발견 사항

(작성 중)
