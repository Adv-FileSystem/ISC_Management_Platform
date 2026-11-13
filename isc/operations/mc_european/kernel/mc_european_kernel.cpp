/**
 * European option pricing by Monte Carlo, backed by Vitis Quantitative Finance L2
 * (xf::fintech::MCEuropeanEngine, Vitis Libraries 2025.1).
 *
 * Engine settings: float, UN=2, tolerance 0.02. The kernel loops over a batch of options read from
 * memory; seeds are fixed per option so runs are repeatable. Each option's volatility goes through
 * the user function isc_volatility before the engine call
 * (isc_user_logic.hpp, staged by ISC from the module's logic file or the operation's starter).
 */
#include <stdint.h>

#include "xf_fintech/enums.hpp"
#include "xf_fintech/mc_engine.hpp"
#include "xf_fintech/rng.hpp"
#include "isc_user_logic.hpp"

typedef float DT;
static const int kFields = 8;  // S, K, sigma, q, r, T, type, reserved

extern "C" void isc_mc_european(const DT* options, DT* prices, uint32_t count, uint32_t samples,
                                uint32_t time_steps) {
#pragma HLS INTERFACE m_axi port = options offset = slave bundle = gmem0
#pragma HLS INTERFACE m_axi port = prices offset = slave bundle = gmem1
#pragma HLS INTERFACE s_axilite port = options bundle = control
#pragma HLS INTERFACE s_axilite port = prices bundle = control
#pragma HLS INTERFACE s_axilite port = count bundle = control
#pragma HLS INTERFACE s_axilite port = samples bundle = control
#pragma HLS INTERFACE s_axilite port = time_steps bundle = control
#pragma HLS INTERFACE s_axilite port = return bundle = control

options_loop:
    for (uint32_t i = 0; i < count; ++i) {
        DT underlying = options[i * kFields + 0];
        DT strike = options[i * kFields + 1];
        DT volatility = isc_volatility(options[i * kFields + 2]);
        DT dividend = options[i * kFields + 3];
        DT rate = options[i * kFields + 4];
        DT time_length = options[i * kFields + 5];
        bool put = options[i * kFields + 6] != 0;  // engine: false = call, true = put

        ap_uint<32> seed[2];
        seed[0] = 1 + i;
        seed[1] = 10001 + i;
        DT out[1];
        xf::fintech::MCEuropeanEngine<DT, 2>(underlying, volatility, dividend, rate, time_length, strike, put,
                                             seed, out, (DT)0.02, samples, time_steps);
        prices[i] = out[0];
    }
}
