/** Adler-32 memory-mapped kernel backed by Vitis Libraries 2025.1. */
#include <ap_int.h>
#include <stdint.h>

#include "checksum_wrapper.hpp"

namespace {
constexpr int kParallelBytes = 16;
}

extern "C" {

void isc_adler32(const ap_uint<kParallelBytes * 8>* in,
                 ap_uint<32>* checksum,
                 uint32_t input_size) {
#pragma HLS INTERFACE m_axi port = in offset = slave bundle = gmem0
#pragma HLS INTERFACE m_axi port = checksum offset = slave bundle = gmem1
#pragma HLS INTERFACE s_axilite port = in bundle = control
#pragma HLS INTERFACE s_axilite port = checksum bundle = control
#pragma HLS INTERFACE s_axilite port = input_size bundle = control
#pragma HLS INTERFACE s_axilite port = return bundle = control

    xf::compression::adler32_mm<kParallelBytes>(in, checksum, input_size);
}

}  // extern "C"
