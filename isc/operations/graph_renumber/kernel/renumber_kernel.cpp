/**
 * Contiguous renumbering backed by Vitis Graph L2 (xf::graph::renumberCore, Vitis Libraries 2025.1).
 * Same body and interface pragmas as the official test kernel (graph/L2/tests/renumber/kernel/
 * kernel_renumber.cpp); only the kernel name differs. configs[0] = N in, configs[1] = clusters out.
 */
#include "xf_graph_L2.hpp"

extern "C" void isc_renumber(int32_t* configs, ap_int<DWIDTHS>* old_ids, ap_int<DWIDTHS>* map0,
                             ap_int<DWIDTHS>* map1, ap_int<DWIDTHS>* new_ids) {
#pragma HLS INTERFACE m_axi offset = slave bundle = gmem0 port = configs latency = 32 num_read_outstanding = 64 max_read_burst_length = 64 num_write_outstanding = 64 max_write_burst_length = 32 depth = 128
#pragma HLS INTERFACE m_axi offset = slave bundle = gmem1 port = old_ids latency = 32 num_read_outstanding = 64 max_read_burst_length = 64 num_write_outstanding = 64 max_write_burst_length = 32 depth = 128
#pragma HLS INTERFACE m_axi offset = slave bundle = gmem2 port = map0 latency = 32 num_read_outstanding = 64 max_read_burst_length = 64 num_write_outstanding = 64 max_write_burst_length = 32 depth = 128
#pragma HLS INTERFACE m_axi offset = slave bundle = gmem3 port = map1 latency = 32 num_read_outstanding = 64 max_read_burst_length = 64 num_write_outstanding = 64 max_write_burst_length = 32 depth = 128
#pragma HLS INTERFACE m_axi offset = slave bundle = gmem4 port = new_ids latency = 32 num_read_outstanding = 64 max_read_burst_length = 64 num_write_outstanding = 64 max_write_burst_length = 32 depth = 128
#pragma HLS INTERFACE s_axilite port = configs bundle = control
#pragma HLS INTERFACE s_axilite port = old_ids bundle = control
#pragma HLS INTERFACE s_axilite port = map0 bundle = control
#pragma HLS INTERFACE s_axilite port = map1 bundle = control
#pragma HLS INTERFACE s_axilite port = new_ids bundle = control
#pragma HLS INTERFACE s_axilite port = return bundle = control
    xf::graph::internal::initRenumber(configs[0], map0, map1);
    xf::graph::renumberCore(configs[0], configs[1], old_ids, map0, map1, new_ids);
}
