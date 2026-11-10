/**
 * Snappy block compression: the official Vitis Data Compression L2 kernel source
 * (data_compression/L2/src/snappy_compress_mm.cpp, xilSnappyCompress) with PARALLEL_BLOCK=8,
 * called from a wrapper that first passes every input byte through the user function isc_pre
 * (isc_user_logic.hpp, staged by ISC from the module's logic file or the operation's starter).
 * The converted bytes go to `staged` in a plain sequential pass, outside any dataflow region, and
 * the official kernel compresses `staged`; the host stores `staged` for blocks that do not shrink.
 */
#define PARALLEL_BLOCK 8
#include "snappy_compress_mm.cpp"
#include "isc_user_logic.hpp"

namespace {

constexpr int kBytesPerWord = GMEM_DWIDTH / 8;

void userPre(const xf::compression::uintMemWidth_t* in, xf::compression::uintMemWidth_t* staged,
             uint32_t input_size) {
    uint32_t words = (input_size + kBytesPerWord - 1) / kBytesPerWord;
ISC_USER_PRE:
    for (uint32_t i = 0; i < words; i++) {
#pragma HLS PIPELINE II = 1
#pragma HLS LOOP_TRIPCOUNT min = 1 max = 32768
        xf::compression::uintMemWidth_t w = in[i];
        for (int k = 0; k < kBytesPerWord; k++) {
#pragma HLS UNROLL
            uint8_t b = w.range(8 * k + 7, 8 * k);
            w.range(8 * k + 7, 8 * k) = isc_pre(b);
        }
        staged[i] = w;
    }
}

}  // namespace

extern "C" void isc_snappy_compress(const xf::compression::uintMemWidth_t* in,
                                    xf::compression::uintMemWidth_t* out,
                                    uint32_t* compressd_size,
                                    uint32_t* in_block_size,
                                    uint32_t block_size_in_kb,
                                    uint32_t input_size,
                                    xf::compression::uintMemWidth_t* staged) {
#pragma HLS INTERFACE m_axi port = in offset = slave bundle = gmem0
#pragma HLS INTERFACE m_axi port = out offset = slave bundle = gmem0
#pragma HLS INTERFACE m_axi port = compressd_size offset = slave bundle = gmem1
#pragma HLS INTERFACE m_axi port = in_block_size offset = slave bundle = gmem1
#pragma HLS INTERFACE m_axi port = staged offset = slave bundle = gmem2
#pragma HLS INTERFACE s_axilite port = in bundle = control
#pragma HLS INTERFACE s_axilite port = out bundle = control
#pragma HLS INTERFACE s_axilite port = compressd_size bundle = control
#pragma HLS INTERFACE s_axilite port = in_block_size bundle = control
#pragma HLS INTERFACE s_axilite port = block_size_in_kb bundle = control
#pragma HLS INTERFACE s_axilite port = input_size bundle = control
#pragma HLS INTERFACE s_axilite port = staged bundle = control
#pragma HLS INTERFACE s_axilite port = return bundle = control
    userPre(in, staged, input_size);
    xilSnappyCompress(staged, out, compressd_size, in_block_size, block_size_in_kb, input_size);
}
