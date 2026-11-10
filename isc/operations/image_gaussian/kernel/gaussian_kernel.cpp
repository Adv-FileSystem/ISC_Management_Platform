/**
 * 3x3 Gaussian blur on 8-bit gray images, backed by Vitis Vision L1 (Vitis Libraries 2025.1).
 * Same body as the library's L2 example (vision/L2/examples/gaussianfilter) with the official
 * 3x3 / XF_8UC1 / NPPC1 / 8-bit pointer configuration; only the maximum size is raised to 1080p.
 * sigma arrives as the bits of a float so the host passes a plain 32-bit integer.
 */
#include <stdint.h>

#include "hls_stream.h"
#include "common/xf_common.hpp"
#include "common/xf_utility.hpp"
#include "imgproc/xf_gaussian_filter.hpp"

#define HEIGHT 1080
#define WIDTH 1920
#define FILTER_WIDTH 3
#define IN_TYPE XF_8UC1
#define OUT_TYPE XF_8UC1
#define NPPCX XF_NPPC1
#define PTR_WIDTH 8
#define XF_CV_DEPTH_IN_1 2
#define XF_CV_DEPTH_OUT_1 2

extern "C" {

void isc_gaussian3x3(ap_uint<PTR_WIDTH>* img_in, ap_uint<PTR_WIDTH>* img_out, int rows, int cols,
                     uint32_t sigma_bits) {
#pragma HLS INTERFACE m_axi port = img_in offset = slave bundle = gmem1
#pragma HLS INTERFACE m_axi port = img_out offset = slave bundle = gmem2
#pragma HLS INTERFACE s_axilite port = img_in bundle = control
#pragma HLS INTERFACE s_axilite port = img_out bundle = control
#pragma HLS INTERFACE s_axilite port = rows bundle = control
#pragma HLS INTERFACE s_axilite port = cols bundle = control
#pragma HLS INTERFACE s_axilite port = sigma_bits bundle = control
#pragma HLS INTERFACE s_axilite port = return bundle = control

    union {
        uint32_t u;
        float f;
    } sigma;
    sigma.u = sigma_bits;

    xf::cv::Mat<IN_TYPE, HEIGHT, WIDTH, NPPCX, XF_CV_DEPTH_IN_1> in_mat(rows, cols);
    xf::cv::Mat<OUT_TYPE, HEIGHT, WIDTH, NPPCX, XF_CV_DEPTH_OUT_1> out_mat(rows, cols);
#pragma HLS DATAFLOW
    xf::cv::Array2xfMat<PTR_WIDTH, IN_TYPE, HEIGHT, WIDTH, NPPCX, XF_CV_DEPTH_IN_1>(img_in, in_mat);
    xf::cv::GaussianBlur<FILTER_WIDTH, XF_BORDER_CONSTANT, IN_TYPE, HEIGHT, WIDTH, NPPCX, XF_CV_DEPTH_IN_1,
                         XF_CV_DEPTH_OUT_1>(in_mat, out_mat, sigma.f);
    xf::cv::xfMat2Array<PTR_WIDTH, OUT_TYPE, HEIGHT, WIDTH, NPPCX, XF_CV_DEPTH_OUT_1>(out_mat, img_out);
}

}  // extern "C"
