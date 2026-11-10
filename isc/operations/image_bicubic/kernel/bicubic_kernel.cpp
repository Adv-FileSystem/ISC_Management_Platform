/**
 * Bicubic downscale backed by Vitis Codec L2 (xf::codec::resizeTop, Vitis Libraries 2025.1).
 * configs = {src_w, src_h, dst_w, dst_h}; pixels travel 8 per 64-bit word, first pixel in the low byte.
 * After resizeTop writes dst, every output pixel goes through the user function isc_post
 * (isc_user_logic.hpp, staged by ISC from the module's logic file or the operation's starter).
 */
#include <stdint.h>

#include "kernel_resize.hpp"
#include "bicubicinterpolator.hpp"
#include "isc_user_logic.hpp"

extern "C" void isc_bicubic_resize(ap_uint<32>* configs, ap_uint<WDATA>* src, ap_uint<WDATA>* dst) {
#pragma HLS INTERFACE m_axi offset = slave bundle = gmem0 port = configs latency = 32 num_read_outstanding = 64 max_read_burst_length = 64 num_write_outstanding = 64 max_write_burst_length = 32 depth = 5
#pragma HLS INTERFACE m_axi offset = slave bundle = gmem1 port = src latency = 32 num_read_outstanding = 64 max_read_burst_length = 64 num_write_outstanding = 64 max_write_burst_length = 32 depth = 128
#pragma HLS INTERFACE m_axi offset = slave bundle = gmem2 port = dst latency = 32 num_read_outstanding = 64 max_read_burst_length = 64 num_write_outstanding = 64 max_write_burst_length = 32 depth = 128
#pragma HLS INTERFACE s_axilite port = configs bundle = control
#pragma HLS INTERFACE s_axilite port = src bundle = control
#pragma HLS INTERFACE s_axilite port = dst bundle = control
#pragma HLS INTERFACE s_axilite port = return bundle = control
    xf::codec::resizeTop(configs, src, dst);

    unsigned dst_w = configs[2];
    unsigned dst_h = configs[3];
    unsigned words = (dst_w * dst_h + NPPC - 1) / NPPC;
ISC_USER_POST:
    for (unsigned i = 0; i < words; i++) {
#pragma HLS PIPELINE II = 1
#pragma HLS LOOP_TRIPCOUNT min = 1 max = 8388608
        ap_uint<WDATA> w = dst[i];
        for (int k = 0; k < NPPC; k++) {
#pragma HLS UNROLL
            uint8_t px = w.range(8 * k + 7, 8 * k);
            w.range(8 * k + 7, 8 * k) = isc_post(px);
        }
        dst[i] = w;
    }
}
