/**
 * Snappy block compression: the official Vitis Data Compression L2 kernel source compiled as is
 * (data_compression/L2/src/snappy_compress_mm.cpp, kernel xilSnappyCompress), with the compile-time
 * setting of the official test (data_compression/L2/tests/snappy_compress/Makefile: -D PARALLEL_BLOCK=8).
 */
#define PARALLEL_BLOCK 8
#include "snappy_compress_mm.cpp"
