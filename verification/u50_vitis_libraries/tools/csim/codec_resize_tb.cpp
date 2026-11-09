// C model of the official codec resize demo: same kernel source, same 8-pixel packing as host/test_resize.cpp.
#include <cstdio>
#include <cstdlib>
#include <vector>
#include "kernel_resize.hpp"
int main(int argc, char** argv) {
    int sw = atoi(argv[3]), sh = atoi(argv[4]), dw = atoi(argv[5]), dh = atoi(argv[6]);
    FILE* f = fopen(argv[1], "rb"); std::vector<unsigned char> px(sw * sh); fread(px.data(), 1, px.size(), f); fclose(f);
    std::vector<ap_uint<32> > cfg(4); cfg[0] = sw; cfg[1] = sh; cfg[2] = dw; cfg[3] = dh;
    std::vector<ap_uint<WDATA> > src(MAX_SRC), dst(MAX_DST);
    ap_uint<WDATA> w = 0;
    for (int i = 0; i < sw * sh; i++) { w.range((i % 8) * WBIT + WBIT - 1, (i % 8) * WBIT) = px[i]; if ((i + 1) % 8 == 0) src[i / 8] = w; }
    kernel_resize(cfg.data(), src.data(), dst.data());
    FILE* o = fopen(argv[2], "wb");
    for (int i = 0; i < DivCeil(dw * dh, WBIT); i++) for (int j = 0; j < WBIT; j++) if (i * 8 + j < dw * dh) { unsigned char b = dst[i].range(j * WBIT + WBIT - 1, j * WBIT); fwrite(&b, 1, 1, o); }
    fclose(o); return 0;
}
