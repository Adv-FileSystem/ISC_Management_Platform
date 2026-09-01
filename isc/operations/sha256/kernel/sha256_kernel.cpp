/** SHA-256 memory-mapped kernel backed by Vitis Security Library 2025.1. */
#include <ap_int.h>
#include <cstdio>
#include <stdint.h>

#include "hls_stream.h"
#include "xf_security/sha224_256.hpp"

namespace {

void memoryToStream(const ap_uint<32>* in,
                    uint64_t input_size,
                    hls::stream<ap_uint<32> >& message,
                    hls::stream<ap_uint<64> >& length,
                    hls::stream<bool>& end_length) {
    const uint64_t words = (input_size + 3) / 4;
read_input:
    for (uint64_t i = 0; i < words; ++i) {
#pragma HLS PIPELINE II = 1
        message.write(in[i]);
    }
    length.write(input_size);
    end_length.write(false);
    end_length.write(true);
}

void streamToMemory(hls::stream<ap_uint<256> >& hash,
                    hls::stream<bool>& end_hash,
                    ap_uint<256>* digest) {
    const bool end = end_hash.read();
    if (!end) {
        digest[0] = hash.read();
    }
    // Consume the terminal marker emitted after the single digest.
    end_hash.read();
}

void sha256MemoryMapped(const ap_uint<32>* in,
                        ap_uint<256>* digest,
                        uint64_t input_size) {
#pragma HLS DATAFLOW
    hls::stream<ap_uint<32> > message("message");
    hls::stream<ap_uint<64> > length("length");
    hls::stream<bool> end_length("end_length");
    hls::stream<ap_uint<256> > hash("hash");
    hls::stream<bool> end_hash("end_hash");
#pragma HLS STREAM variable = message depth = 64
#pragma HLS STREAM variable = length depth = 2
#pragma HLS STREAM variable = end_length depth = 2
#pragma HLS STREAM variable = hash depth = 2
#pragma HLS STREAM variable = end_hash depth = 2

    memoryToStream(in, input_size, message, length, end_length);
    xf::security::sha256(message, length, end_length, hash, end_hash);
    streamToMemory(hash, end_hash, digest);
}

}  // namespace

extern "C" {

void isc_sha256(const ap_uint<32>* in,
                ap_uint<256>* digest,
                uint64_t input_size) {
#pragma HLS INTERFACE m_axi port = in offset = slave bundle = gmem0
#pragma HLS INTERFACE m_axi port = digest offset = slave bundle = gmem1
#pragma HLS INTERFACE s_axilite port = in bundle = control
#pragma HLS INTERFACE s_axilite port = digest bundle = control
#pragma HLS INTERFACE s_axilite port = input_size bundle = control
#pragma HLS INTERFACE s_axilite port = return bundle = control

    sha256MemoryMapped(in, digest, input_size);
}

}  // extern "C"
