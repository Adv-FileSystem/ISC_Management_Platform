/**
 * Two-hop path count backed by Vitis Graph L2 (xf::graph::twoHop, Vitis Libraries 2025.1).
 * Same body and interface pragmas as the official test kernel (graph/L2/tests/twoHop/kernel/
 * twoHop_kernel.cpp); only the kernel name differs. pair[i] = (src << 32) | dst, 0-based.
 */
#include "xf_graph_L2.hpp"
#include <ap_int.h>

extern "C" void isc_twohop(unsigned num_pairs, ap_uint<64>* pair, unsigned* offset_one, unsigned* index_one,
                           unsigned* offset_two, unsigned* index_two, unsigned* count) {
#pragma HLS INTERFACE m_axi offset = slave latency = 32 num_write_outstanding = 1 num_read_outstanding = 16 max_write_burst_length = 2 max_read_burst_length = 256 bundle = gmem0 port = pair
#pragma HLS INTERFACE m_axi offset = slave latency = 32 num_write_outstanding = 16 num_read_outstanding = 1 max_write_burst_length = 2 max_read_burst_length = 2 bundle = gmem1 port = count
#pragma HLS INTERFACE m_axi offset = slave latency = 64 num_write_outstanding = 1 num_read_outstanding = 64 max_write_burst_length = 2 max_read_burst_length = 2 bundle = gmem2 port = offset_one
#pragma HLS INTERFACE m_axi offset = slave latency = 64 num_write_outstanding = 1 num_read_outstanding = 64 max_write_burst_length = 2 max_read_burst_length = 256 bundle = gmem3 port = index_one
#pragma HLS INTERFACE m_axi offset = slave latency = 64 num_write_outstanding = 1 num_read_outstanding = 64 max_write_burst_length = 2 max_read_burst_length = 2 bundle = gmem4 port = offset_two
#pragma HLS INTERFACE m_axi offset = slave latency = 64 num_write_outstanding = 1 num_read_outstanding = 64 max_write_burst_length = 2 max_read_burst_length = 256 bundle = gmem5 port = index_two
#pragma HLS INTERFACE s_axilite port = num_pairs bundle = control
#pragma HLS INTERFACE s_axilite port = pair bundle = control
#pragma HLS INTERFACE s_axilite port = offset_one bundle = control
#pragma HLS INTERFACE s_axilite port = index_one bundle = control
#pragma HLS INTERFACE s_axilite port = offset_two bundle = control
#pragma HLS INTERFACE s_axilite port = index_two bundle = control
#pragma HLS INTERFACE s_axilite port = count bundle = control
#pragma HLS INTERFACE s_axilite port = return bundle = control
    xf::graph::twoHop(num_pairs, pair, offset_one, index_one, offset_two, index_two, count);
}
