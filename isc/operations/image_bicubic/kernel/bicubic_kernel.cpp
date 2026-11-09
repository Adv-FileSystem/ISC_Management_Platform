/**
 * Bicubic downscale backed by Vitis Codec L2 (xf::codec::resizeTop, Vitis Libraries 2025.1).
 * Same body and interface pragmas as the official demo kernel (codec/L2/demos/resize/kernel/
 * kernel_resize.cpp); only the kernel name differs. configs = {src_w, src_h, dst_w, dst_h};
 * pixels travel 8 per 64-bit word, first pixel in the low byte.
 */
#include "kernel_resize.hpp"
#include "bicubicinterpolator.hpp"

extern "C" void isc_bicubic_resize(ap_uint<32>* configs, ap_uint<WDATA>* src, ap_uint<WDATA>* dst) {
#pragma HLS INTERFACE m_axi offset = slave bundle = gmem0 port = configs latency = 32 num_read_outstanding = 64 max_read_burst_length = 64 num_write_outstanding = 64 max_write_burst_length = 32 depth = 5
#pragma HLS INTERFACE m_axi offset = slave bundle = gmem1 port = src latency = 32 num_read_outstanding = 64 max_read_burst_length = 64 num_write_outstanding = 64 max_write_burst_length = 32 depth = 128
#pragma HLS INTERFACE m_axi offset = slave bundle = gmem2 port = dst latency = 32 num_read_outstanding = 64 max_read_burst_length = 64 num_write_outstanding = 64 max_write_burst_length = 32 depth = 128
#pragma HLS INTERFACE s_axilite port = configs bundle = control
#pragma HLS INTERFACE s_axilite port = src bundle = control
#pragma HLS INTERFACE s_axilite port = dst bundle = control
#pragma HLS INTERFACE s_axilite port = return bundle = control
    xf::codec::resizeTop(configs, src, dst);
}
