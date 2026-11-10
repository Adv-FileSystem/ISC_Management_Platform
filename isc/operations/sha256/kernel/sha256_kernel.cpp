/** SHA-256 memory-mapped kernel built from Vitis Security Library 2025.1 L1 stages. */
#include <ap_int.h>
#include <cstdio>
#include <stdint.h>

#include "hls_stream.h"
#include "xf_security/sha224_256.hpp"

// This kernel hashes exactly one message per call. It wires up the stages of
// xf::security::sha256 (internal::sha256_top in xf_security/sha224_256.hpp)
// itself instead of calling it, because sha256_top deadlocks on one message
// longer than 32 blocks (about 2 KB):
//
//   sha256_top's dup_strm forwards preProcessing's per-message block count to
//   the message-schedule and digest stages, then reads the next end flag. HLS
//   keeps the order of accesses to one stream but not across streams, so in
//   hardware that read can happen before the count is forwarded. The next end
//   flag only comes after preProcessing has sent every block, blk_strm holds 32
//   blocks, and nothing downstream starts without the count.
//
// Here the block count and both end flags go to the schedule and digest stages
// before any data, so no stage waits on a flag that depends on its own output.
// preProcessing's own count stream is drained by a process that only reads.

namespace {

namespace sec = xf::security::internal;

// Same formula as preProcessing: whole 64-byte blocks, plus one block for the
// 0x80 pad and the 64-bit length, plus one more if the tail leaves no room.
uint64_t blockCount(uint64_t input_size) {
    return (input_size >> 6) + 1 + ((input_size & 0x3f) > 55);
}

void memoryToStream(const ap_uint<32>* in,
                    uint64_t input_size,
                    hls::stream<ap_uint<32> >& message,
                    hls::stream<ap_uint<64> >& length,
                    hls::stream<bool>& end_length,
                    hls::stream<uint64_t>& schedule_nblk,
                    hls::stream<bool>& schedule_end,
                    hls::stream<uint64_t>& digest_nblk,
                    hls::stream<bool>& digest_end) {
    const uint64_t blocks = blockCount(input_size);
    schedule_end.write(false);
    schedule_nblk.write(blocks);
    schedule_end.write(true);
    digest_end.write(false);
    digest_nblk.write(blocks);
    digest_end.write(true);

    // preProcessing reads end_length, then length, then the message words, so
    // send them in that order. Writing the message first fills the message
    // FIFO after 64 words (256 bytes) while preProcessing waits for the length.
    end_length.write(false);
    length.write(input_size);
    // The last word may be partial; preProcessing ignores its unused bytes and
    // the host pads the buffer to whole words, so this read stays in bounds.
    const uint64_t words = (input_size + 3) / 4;
read_input:
    for (uint64_t i = 0; i < words; ++i) {
#pragma HLS PIPELINE II = 1
        message.write(in[i]);
    }
    end_length.write(true);
}

// Consumes preProcessing's block count stream, which this kernel does not use,
// so nothing is left in the FIFO for the next call.
void drainBlockCount(hls::stream<uint64_t>& nblk, hls::stream<bool>& end_nblk) {
drain_counts:
    for (bool end = end_nblk.read(); !end; end = end_nblk.read()) {
        nblk.read();
    }
}

void streamToMemory(hls::stream<ap_uint<256> >& hash,
                    hls::stream<bool>& end_hash,
                    ap_uint<256>* digest) {
    const bool end = end_hash.read();
    if (!end) {
        digest[0] = hash.read();
    }
    // Consume the terminal marker emitted after the single digest.
    end_hash.read();
}

void sha256MemoryMapped(const ap_uint<32>* in,
                        ap_uint<256>* digest,
                        uint64_t input_size) {
#pragma HLS DATAFLOW
    hls::stream<ap_uint<32> > message("message");
    hls::stream<ap_uint<64> > length("length");
    hls::stream<bool> end_length("end_length");
    hls::stream<sec::SHA256Block> blk_strm("blk_strm");
    hls::stream<uint64_t> unused_nblk("unused_nblk");
    hls::stream<bool> unused_end_nblk("unused_end_nblk");
    hls::stream<uint64_t> schedule_nblk("schedule_nblk");
    hls::stream<bool> schedule_end("schedule_end");
    hls::stream<uint64_t> digest_nblk("digest_nblk");
    hls::stream<bool> digest_end("digest_end");
    hls::stream<uint32_t> w_strm("w_strm");
    hls::stream<ap_uint<256> > hash("hash");
    hls::stream<bool> end_hash("end_hash");
#pragma HLS STREAM variable = message depth = 64
#pragma HLS STREAM variable = length depth = 2
#pragma HLS STREAM variable = end_length depth = 2
#pragma HLS STREAM variable = blk_strm depth = 32
#pragma HLS STREAM variable = unused_nblk depth = 2
#pragma HLS STREAM variable = unused_end_nblk depth = 2
#pragma HLS STREAM variable = schedule_nblk depth = 2
#pragma HLS STREAM variable = schedule_end depth = 2
#pragma HLS STREAM variable = digest_nblk depth = 2
#pragma HLS STREAM variable = digest_end depth = 2
#pragma HLS STREAM variable = w_strm depth = 32
#pragma HLS STREAM variable = hash depth = 2
#pragma HLS STREAM variable = end_hash depth = 2

    memoryToStream(in, input_size, message, length, end_length,
                   schedule_nblk, schedule_end, digest_nblk, digest_end);
    sec::preProcessing(message, length, end_length, blk_strm, unused_nblk, unused_end_nblk);
    drainBlockCount(unused_nblk, unused_end_nblk);
    sec::generateMsgSchedule(blk_strm, schedule_nblk, schedule_end, w_strm);
    sec::sha256Digest(digest_nblk, digest_end, w_strm, hash, end_hash);
    streamToMemory(hash, end_hash, digest);
}

}  // namespace

extern "C" {

void isc_sha256(const ap_uint<32>* in,
                ap_uint<256>* digest,
                uint64_t input_size) {
#pragma HLS INTERFACE m_axi port = in offset = slave bundle = gmem0
#pragma HLS INTERFACE m_axi port = digest offset = slave bundle = gmem1
#pragma HLS INTERFACE s_axilite port = in bundle = control
#pragma HLS INTERFACE s_axilite port = digest bundle = control
#pragma HLS INTERFACE s_axilite port = input_size bundle = control
#pragma HLS INTERFACE s_axilite port = return bundle = control

    sha256MemoryMapped(in, digest, input_size);
}

}  // extern "C"
