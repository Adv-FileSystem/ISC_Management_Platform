/**
 * @file lz4_kernel.cpp
 * @brief Simplified single-block LZ4 compression kernel (isc-framework vertical slice).
 *
 * This wraps the same L1 compression primitives Xilinx's own xilLz4Compress
 * uses (lzCompress -> lzBestMatchFilter -> lzBooster -> lz4Compress, see
 * Vitis_Libraries_2025.1/data_compression/L2/src/lz4_compress_mm.cpp), but
 * deliberately drops everything that primitive doesn't need to prove out:
 *
 *   - one block per kernel call (no block_size_in_kb / PARALLEL_BLOCK loop)
 *   - one scalar output_size (no per-block compressd_size array)
 *   - byte-wide (8-bit) AXI ports (no 512-bit gmem downsizer stages)
 *
 * Block iteration over inputs larger than one block, and LZ4 frame-header
 * framing, are the host's job (libraries/lz4/host/src/lz4_adapter.cpp) --
 * see docs/architecture-notes.md section 10 for why this split was chosen.
 */
#include <ap_int.h>
#include <stdint.h>

#include "hls_stream.h"
#include "lz4_compress.hpp"
#include "lz_compress.hpp"
#include "lz_optional.hpp"
#include "mm2s.hpp"
#include "s2mm.hpp"

namespace {

constexpr int kMatchLen = 6;
constexpr int kMinMatch = 4;
constexpr int kMaxOffsetLimit = 65536;
constexpr int kMaxMatchLen = 255;
constexpr int kOffsetWindow = 64 * 1024;
constexpr int kMaxLitCount = 4096;
constexpr int kParallelBlock = 1;  // single engine -- no parallel block splitting
constexpr int kBurstSize = 16;

typedef ap_uint<8> uintV_t;

void lz4CompressCore(hls::stream<uintV_t>& inStream,
                     hls::stream<uintV_t>& outStream,
                     hls::stream<bool>& outStreamEos,
                     hls::stream<uint32_t>& compressedSizeStream,
                     uint32_t max_lit_limit[kParallelBlock],
                     uint32_t input_size) {
    hls::stream<xf::compression::compressd_dt> compressdStream("compressdStream");
    hls::stream<xf::compression::compressd_dt> bestMatchStream("bestMatchStream");
    hls::stream<xf::compression::compressd_dt> boosterStream("boosterStream");
#pragma HLS STREAM variable = compressdStream depth = 8
#pragma HLS STREAM variable = bestMatchStream depth = 8
#pragma HLS STREAM variable = boosterStream depth = 8
#pragma HLS BIND_STORAGE variable = compressdStream type = FIFO impl = SRL
#pragma HLS BIND_STORAGE variable = boosterStream type = FIFO impl = SRL

#pragma HLS dataflow
    xf::compression::lzCompress<kMatchLen, kMinMatch, kMaxOffsetLimit>(inStream, compressdStream, input_size);
    xf::compression::lzBestMatchFilter<kMatchLen, kOffsetWindow>(compressdStream, bestMatchStream, input_size);
    xf::compression::lzBooster<kMaxMatchLen>(bestMatchStream, boosterStream, input_size);
    xf::compression::lz4Compress<kMaxLitCount, kParallelBlock>(boosterStream, outStream, max_lit_limit, input_size,
                                                                outStreamEos, compressedSizeStream, /*core_idx=*/0);
}

// Mirrors Xilinx's own two-level structure (lz4() dataflow function nested
// inside the sequential xilLz4Compress in lz4_compress_mm.cpp): the dataflow
// region is fully contained here, so the caller can safely read
// max_lit_limit afterward without it being pulled into the same dataflow
// scope as the streaming stages.
void lz4CompressBlock(const ap_uint<8>* in,
                      ap_uint<8>* out,
                      uint32_t input_size,
                      uint32_t* output_size,
                      uint32_t max_lit_limit[kParallelBlock]) {
    hls::stream<uintV_t> inStream("inStream");
    hls::stream<uintV_t> outStream("outStream");
    hls::stream<bool> outStreamEos("outStreamEos");
    hls::stream<uint32_t> compressedSizeStream("compressedSizeStream");

#pragma HLS STREAM variable = inStream depth = kBurstSize
#pragma HLS STREAM variable = outStream depth = kBurstSize
#pragma HLS STREAM variable = outStreamEos depth = 2
#pragma HLS BIND_STORAGE variable = inStream type = FIFO impl = SRL
#pragma HLS BIND_STORAGE variable = outStream type = FIFO impl = SRL

#pragma HLS dataflow
    xf::compression::details::mm2sSimple<8, kBurstSize>(in, inStream, input_size);
    lz4CompressCore(inStream, outStream, outStreamEos, compressedSizeStream, max_lit_limit, input_size);
    xf::compression::details::s2mmEosSimple<8, kBurstSize, uint32_t>(out, outStream, outStreamEos,
                                                                     compressedSizeStream, output_size);
}

}  // namespace

extern "C" {

/**
 * @brief LZ4 compression kernel, single block per call.
 *
 * @param in input raw data (exactly one block, <= manifest input.block_size_bytes)
 * @param out output compressed data buffer (must be pre-sized to an upper bound,
 *            e.g. input_size + input_size/255 + 16, the LZ4 worst-case expansion)
 * @param input_size size of this block in bytes
 * @param output_size compressed size actually written to `out` (single scalar,
 *                    written as output_size[0])
 * @param literal_overflow set to 1 if a literal run inside this block exceeded
 *        the internal MAX_LIT_COUNT buffer (4096 bytes) -- when this fires,
 *        lz4CompressCore silently drops the overflow bytes (see
 *        Vitis_Libraries_2025.1/.../lz4_compress.hpp's lz4CompressPart1), so
 *        `out`/`output_size` for this block are not trustworthy. The host
 *        (lz4_adapter.cpp) must fall back to storing the block's raw input
 *        bytes instead of using this kernel's output when this is set.
 */
void lz4_compress(const ap_uint<8>* in, ap_uint<8>* out, uint32_t input_size, uint32_t* output_size,
                  uint32_t* literal_overflow) {
#pragma HLS INTERFACE m_axi port = in offset = slave bundle = gmem0
#pragma HLS INTERFACE m_axi port = out offset = slave bundle = gmem1
#pragma HLS INTERFACE m_axi port = output_size offset = slave bundle = gmem1
#pragma HLS INTERFACE m_axi port = literal_overflow offset = slave bundle = gmem1
#pragma HLS INTERFACE s_axilite port = in bundle = control
#pragma HLS INTERFACE s_axilite port = out bundle = control
#pragma HLS INTERFACE s_axilite port = output_size bundle = control
#pragma HLS INTERFACE s_axilite port = literal_overflow bundle = control
#pragma HLS INTERFACE s_axilite port = input_size bundle = control
#pragma HLS INTERFACE s_axilite port = return bundle = control

    uint32_t max_lit_limit[kParallelBlock] = {0};
    lz4CompressBlock(in, out, input_size, output_size, max_lit_limit);
    literal_overflow[0] = max_lit_limit[0];
}

}  // extern "C"
