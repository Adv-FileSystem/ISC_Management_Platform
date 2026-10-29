#include <ap_int.h>
#include <stdint.h>

extern "C" {

void isc_identity(const ap_uint<8>* in, ap_uint<8>* out, uint32_t input_size,
                  uint32_t* output_size) {
#pragma HLS INTERFACE m_axi port = in offset = slave bundle = gmem0
#pragma HLS INTERFACE m_axi port = out offset = slave bundle = gmem1
#pragma HLS INTERFACE m_axi port = output_size offset = slave bundle = gmem1
#pragma HLS INTERFACE s_axilite port = in bundle = control
#pragma HLS INTERFACE s_axilite port = out bundle = control
#pragma HLS INTERFACE s_axilite port = output_size bundle = control
#pragma HLS INTERFACE s_axilite port = input_size bundle = control
#pragma HLS INTERFACE s_axilite port = return bundle = control

copy_loop:
    for (uint32_t i = 0; i < input_size; ++i) {
#pragma HLS PIPELINE II = 1
        out[i] = in[i];
    }
    output_size[0] = input_size;
}

}
