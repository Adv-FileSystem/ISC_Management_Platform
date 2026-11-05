# sha256 operation: deadlock fix and verification (2026-11-05)

Branch `fix/sha256-dataflow-deadlock`: commits `0494a79` (message order, input padding) and `625d3e1` (sha256_top block count).

## Symptom (before)

Module `release_sha`, input `datasets/sha256/release.bin` (100,000 bytes):

- hw_emu: host prints `[libprotobuf ERROR ...] Can't parse message of type "xclReadAddrKernelCtrl_response"`, then `SIMULATION EXITED`; no output, no record.
- hw (U50): the `execute` stage never finished (>30 min) and the process had to be SIGKILLed.

The protobuf error is only how the host sees the simulator exit. The simulator log
(`.../behav_waveform/xsim/simulate.log`) holds the cause: HLS deadlock detection.

## Root causes

1. **Message order (our kernel).** `xf::security::sha256` reads `end_length`, then `length`, then the
   message words. `memoryToStream` wrote all message words first, so after 64 words (256 bytes) the
   `message` FIFO was full while `preProcessing` still waited for `end_length`.
   Evidence: [before_fix_hw_emu_deadlock.txt](before_fix_hw_emu_deadlock.txt).
2. **Block count inside Vitis Security L1 `sha256_top` (library).** `dup_strm` forwards `preProcessing`'s
   block count to `generateMsgSchedule` and `sha256Digest`, then reads the next end flag. HLS keeps access
   order per stream but not across streams, so in hardware that read can happen before the count is
   forwarded. The next end flag only comes after `preProcessing` has sent every block, and `blk_strm` holds
   32 blocks. So a single message of more than 32 blocks (2,048 bytes and up) deadlocks.
   With only fix 1 applied, hw_emu passed 1,984 bytes (32 blocks) and stopped at 2,048 (33), 2,112, 2,240,
   4,096 and 100,000 bytes. Evidence: [fix1_hw_emu_deadlock.txt](fix1_hw_emu_deadlock.txt),
   [hw_emu_fix1_probe/summary.txt](hw_emu_fix1_probe/summary.txt).
   A copy of `dup_strm` with the pipeline turned off still deadlocked
   ([hw_emu_fix2/summary.txt](hw_emu_fix2/summary.txt), [hw_emu_fix2_probe/summary.txt](hw_emu_fix2_probe/summary.txt)).

The `uint64_t input_size` scalar and the adapter's argument passing were correct (0–1,984 bytes hashed
correctly on hw_emu even before fix 2). Reading `(n+3)/4` words could read up to 3 bytes past an `n`-byte buffer.

## Fix

- `kernel/sha256_kernel.cpp`: send `end_length(false)` and `length` before the message words. Wire the
  library stages (`preProcessing`, `generateMsgSchedule`, `sha256Digest`) in the kernel instead of calling
  `xf::security::sha256`. `memoryToStream` computes the block count with `preProcessing`'s formula and sends it
  and both end flags to the schedule and digest stages before any data. A read-only process drains
  `preProcessing`'s own count stream.
- `adapter.py`: pad the input buffer to whole 32-bit words (4 bytes for the empty input), and pass the
  unpadded length.

## Verification (after)

Expected value: `hashlib.sha256(data).hexdigest()`. Inputs: 0 bytes, `a`, `abc`, and `random.Random(n).randbytes(n)`
for 55, 56, 64, 1,984, 2,048 and 4,096 bytes; 100,000 bytes = `random.Random(3).randbytes(100_000)` (`release.bin`).
Each run was wrapped in `timeout -s INT -k 30` (hw_emu 900–1,500 s, hw 600 s), with `--device-timeout 900`.

| Target | Build (fingerprint) | Sizes | Result |
|---|---|---|---|
| C simulation (g++, HLS headers) | — | 0, 1, 3, 55, 56, 64, 2048, 4096, 100000 | all match |
| hw_emu | `fbd3b57f3daf34674ee6bbf4cde894b7` | 0, 1, 3, 55, 56, 64, 100000 ([summary](hw_emu/summary.txt)) + 1984, 2048, 4096 ([summary](hw_emu_probe/summary.txt)) | 10/10 match, no deadlock report in any simulate.log |
| hw (U50) | `e7ba7e442d457afcc45a2d4192a09284` (timing met, 300 MHz) | same 10 sizes ([summary](hw/summary.txt), [probe](hw_probe/summary.txt)) | 10/10 match `hashlib`; platform cross_target validation PASS on all 10 |

- U50 100,000 bytes: run `20261105T134948-81ee51`, `kernel_ms` 0.44, `total_ms` 236.
- hw_emu 100,000 bytes: run `20261105T130443-97bf51`, `kernel_ms` 17,008.
- Card after the hw runs: `xrt-smi examine` shows `0000:03:00.1` Device Ready `Yes`.

Environment: Ubuntu 22.04.5 (kernel 6.8.0-138), Python 3.10.12, Vitis 2025.1, XRT 2.19.194,
Vitis Libraries `v2025.1_update2` (b2c657d), U50 `xilinx_u50_gen3x16_xdma_base_5`.
