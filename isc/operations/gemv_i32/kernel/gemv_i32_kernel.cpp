/**
 * int32 GEMV backed by Vitis BLAS L1 (Vitis Libraries 2025.1):
 *   y_out = alpha * A x + beta * y
 * Library stages wired as in blas/L1/tests/hw/gemv/uut_top.cpp, arrays as m_axi ports. n must be a
 * multiple of kPar; the host pads with zeros. Each result then goes through the user function
 * isc_decide (isc_user_logic.hpp, staged by ISC from the module's logic file or the operation's starter).
 */
#include <stdint.h>

#include "hls_stream.h"
#include "xf_blas.hpp"
#include "isc_user_logic.hpp"

namespace {
constexpr unsigned int kLogPar = 2;            // 4 int32 lanes per cycle
constexpr unsigned int kPar = 1u << kLogPar;
typedef int32_t T;
typedef typename xf::blas::WideType<T, 1>::t_TypeInt Word;

void userDecide(uint32_t m, hls::stream<Word>& in, hls::stream<Word>& out) {
ISC_USER_DECIDE:
    for (uint32_t i = 0; i < m; i++) {
#pragma HLS PIPELINE II = 1
        Word w = in.read();
        int32_t score = (int32_t)(uint32_t)w;
        out.write((Word)(uint32_t)isc_decide(score));
    }
}
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
    hls::stream<Word> str_out;
#pragma HLS DATAFLOW
    xf::blas::gem2Stream<T, kPar>(m, n, a, str_a);
    xf::blas::vec2GemStream<T, kPar>(m, n, x, str_x);
    xf::blas::readVec2Stream<T, 1>(y, m, str_y);
    xf::blas::gemv<T, kLogPar>(m, n, alpha, str_a, str_x, beta, str_y, str_res);
    userDecide(m, str_res, str_out);
    xf::blas::writeStream2Vec<T, 1>(str_out, m, y_out);
}

}  // extern "C"
