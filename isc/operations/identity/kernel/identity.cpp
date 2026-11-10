#include <ap_int.h>
#include <stdint.h>

// The copy moves whole 32-byte words (the HBM port width), so every AXI write beat has all
// byte lanes enabled and no width converter sits in the path. Byte-wide copies left a final
// partial beat, and hw_emu dropped the last byte for lengths such as 17, 33 and 65 (the card
// was correct). The adapter pads both buffers to a multiple of WORD_BYTES and returns only
// the first output_size bytes.
typedef ap_uint<256> word_t;
static const uint32_t WORD_BYTES = 32;

extern "C" {

void isc_identity(const word_t* in, word_t* out, uint32_t input_size,
                  uint32_t* output_size) {
#pragma HLS INTERFACE m_axi port = in offset = slave bundle = gmem0
#pragma HLS INTERFACE m_axi port = out offset = slave bundle = gmem1
#pragma HLS INTERFACE m_axi port = output_size offset = slave bundle = gmem2
#pragma HLS INTERFACE s_axilite port = in bundle = control
#pragma HLS INTERFACE s_axilite port = out bundle = control
#pragma HLS INTERFACE s_axilite port = output_size bundle = control
#pragma HLS INTERFACE s_axilite port = input_size bundle = control
#pragma HLS INTERFACE s_axilite port = return bundle = control

    const uint32_t words = input_size / WORD_BYTES + (input_size % WORD_BYTES != 0);
copy_loop:
    for (uint32_t i = 0; i < words; ++i) {
#pragma HLS PIPELINE II = 1
        out[i] = in[i];
    }
    output_size[0] = input_size;
}

}
