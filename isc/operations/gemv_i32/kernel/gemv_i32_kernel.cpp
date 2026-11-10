/**
 * int32 GEMV backed by Vitis BLAS L1 (Vitis Libraries 2025.1):
 *   y_out = alpha * A x + beta * y
 * The body is the library's own L1 test top (blas/L1/tests/hw/gemv/uut_top.cpp) with the
 * arrays turned into m_axi ports. n must be a multiple of kPar; the host pads with zeros.
 */
#include <stdint.h>

#include "hls_stream.h"
#include "xf_blas.hpp"

namespace {
constexpr unsigned int kLogPar = 2;            // 4 int32 lanes per cycle
constexpr unsigned int kPar = 1u << kLogPar;
typedef int32_t T;
}  // namespace

extern "C" {

void isc_gemv_i32(T* a, T* x, T* y, T* y_out, uint32_t m, uint32_t n, int32_t alpha, int32_t beta) {
#pragma HLS INTERFACE m_axi port = a offset = slave bundle = gmem0
#pragma HLS INTERFACE m_axi port = x offset = slave bundle = gmem1
#pragma HLS INTERFACE m_axi port = y offset = slave bundle = gmem2
#pragma HLS INTERFACE m_axi port = y_out offset = slave bundle = gmem3
#pragma HLS INTERFACE s_axilite port = a bundle = control
#pragma HLS INTERFACE s_axilite port = x bundle = control
#pragma HLS INTERFACE s_axilite port = y bundle = control
#pragma HLS INTERFACE s_axilite port = y_out bundle = control
#pragma HLS INTERFACE s_axilite port = m bundle = control
#pragma HLS INTERFACE s_axilite port = n bundle = control
#pragma HLS INTERFACE s_axilite port = alpha bundle = control
#pragma HLS INTERFACE s_axilite port = beta bundle = control
#pragma HLS INTERFACE s_axilite port = return bundle = control

    hls::stream<typename xf::blas::WideType<T, kPar>::t_TypeInt> str_a;
    hls::stream<typename xf::blas::WideType<T, kPar>::t_TypeInt> str_x;
    hls::stream<typename xf::blas::WideType<T, 1>::t_TypeInt> str_y;
    hls::stream<typename xf::blas::WideType<T, 1>::t_TypeInt> str_res;
#pragma HLS DATAFLOW
    xf::blas::gem2Stream<T, kPar>(m, n, a, str_a);
    xf::blas::vec2GemStream<T, kPar>(m, n, x, str_x);
    xf::blas::readVec2Stream<T, 1>(y, m, str_y);
    xf::blas::gemv<T, kLogPar>(m, n, alpha, str_a, str_x, beta, str_y, str_res);
    xf::blas::writeStream2Vec<T, 1>(str_res, m, y_out);
}

}  // extern "C"
