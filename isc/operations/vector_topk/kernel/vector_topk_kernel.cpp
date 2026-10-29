/**
 * Exact top-k inner product of int8 vectors (ISC validation workload).
 *
 * Speed is not a goal. The kernel exists so that hw_emu, hw and the CPU baseline can be held to
 * the same bytes: a score is the int32 sum of int8 products, and the result is ordered by
 * (score descending, id ascending), a total order, so there is exactly one correct answer.
 */
#include <ap_int.h>
#include <stdint.h>

namespace {

constexpr int kLanes = 16;  // bytes read per cycle
constexpr int kMaxDim = 2048;
constexpr int kMaxK = 16;
constexpr int kMaxWords = kMaxDim / kLanes;
constexpr uint32_t kNoId = 0xFFFFFFFFu;
constexpr int32_t kNoScore = INT32_MIN;

typedef ap_uint<kLanes * 8> Word;

inline int32_t lane(const Word& word, int index) {
#pragma HLS INLINE
    ap_uint<8> raw = word.range(8 * index + 7, 8 * index);
    ap_int<8> value = raw;
    return value.to_int();
}

}  // namespace

extern "C" {

void isc_vector_topk(const Word* query, const Word* db, ap_uint<64>* out, uint32_t rows,
                     uint32_t dim, uint32_t k, uint32_t id_base) {
#pragma HLS INTERFACE m_axi port = query offset = slave bundle = gmem0
#pragma HLS INTERFACE m_axi port = db offset = slave bundle = gmem1
#pragma HLS INTERFACE m_axi port = out offset = slave bundle = gmem2
#pragma HLS INTERFACE s_axilite port = query bundle = control
#pragma HLS INTERFACE s_axilite port = db bundle = control
#pragma HLS INTERFACE s_axilite port = out bundle = control
#pragma HLS INTERFACE s_axilite port = rows bundle = control
#pragma HLS INTERFACE s_axilite port = dim bundle = control
#pragma HLS INTERFACE s_axilite port = k bundle = control
#pragma HLS INTERFACE s_axilite port = id_base bundle = control
#pragma HLS INTERFACE s_axilite port = return bundle = control

    const uint32_t words = dim / kLanes;  // dim is a multiple of kLanes: the host pads with zeros
    const uint32_t keep = k < (uint32_t)kMaxK ? k : (uint32_t)kMaxK;

    Word held[kMaxWords];
    int32_t best_score[kMaxK];
    uint32_t best_id[kMaxK];
#pragma HLS ARRAY_PARTITION variable = best_score complete
#pragma HLS ARRAY_PARTITION variable = best_id complete

load_query:
    for (uint32_t w = 0; w < words; ++w) {
#pragma HLS PIPELINE II = 1
        held[w] = query[w];
    }

init_best:
    for (int i = 0; i < kMaxK; ++i) {
#pragma HLS UNROLL
        best_score[i] = kNoScore;
        best_id[i] = kNoId;
    }

scan_rows:
    for (uint32_t r = 0; r < rows; ++r) {
        const uint32_t base = r * words;
        int32_t score = 0;
    dot_product:
        for (uint32_t w = 0; w < words; ++w) {
#pragma HLS PIPELINE II = 1
            const Word a = held[w];
            const Word b = db[base + w];
            int32_t partial = 0;
        lanes:
            for (int l = 0; l < kLanes; ++l) {
#pragma HLS UNROLL
                partial += lane(a, l) * lane(b, l);
            }
            score += partial;
        }

        // Insert before the first slot this row STRICTLY beats. Rows arrive in id order, so a tie
        // stays behind the row that came first: (score descending, id ascending).
        const uint32_t id = id_base + r;
    insert:
        for (int i = kMaxK - 1; i >= 0; --i) {
#pragma HLS UNROLL
            if (i >= (int)keep) continue;
            const bool beats = score > best_score[i];
            const bool beats_previous = i > 0 && score > best_score[i - 1];
            if (beats_previous) {
                best_score[i] = best_score[i - 1];
                best_id[i] = best_id[i - 1];
            } else if (beats) {
                best_score[i] = score;
                best_id[i] = id;
            }
        }
    }

write_out:
    for (uint32_t i = 0; i < keep; ++i) {
#pragma HLS PIPELINE II = 1
        ap_uint<64> entry;
        entry.range(31, 0) = best_id[i];
        entry.range(63, 32) = (uint32_t)best_score[i];
        out[i] = entry;
    }
}

}  // extern "C"
