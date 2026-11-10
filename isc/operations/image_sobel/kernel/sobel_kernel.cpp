/**
 * Sobel 3x3 x/y gradients of 8-bit gray images, backed by Vitis Vision L1 (Vitis Libraries 2025.1).
 * Same body and configuration as the library's L1 example (vision/L1/examples/sobelfilter:
 * 3x3, XF_8UC1 in and out, NPPC1, 8-bit pointers, no URAM); only the maximum size is raised to 1080p.
 */
#include <stdint.h>

#include "hls_stream.h"
#include "common/xf_common.hpp"
#include "common/xf_utility.hpp"
#include "imgproc/xf_sobel.hpp"

#define HEIGHT 1080
#define WIDTH 1920
#define FILTER_WIDTH 3
#define XF_USE_URAM 0
#define IN_TYPE XF_8UC1
#define OUT_TYPE XF_8UC1
#define NPPCX XF_NPPC1
#define PTR_WIDTH 8
#define XF_CV_DEPTH_IN 2
#define XF_CV_DEPTH_OUT_GX 2
#define XF_CV_DEPTH_OUT_GY 2

extern "C" {

void isc_sobel3x3(ap_uint<PTR_WIDTH>* img_in, ap_uint<PTR_WIDTH>* grad_x, ap_uint<PTR_WIDTH>* grad_y, int rows,
                  int cols) {
#pragma HLS INTERFACE m_axi port = img_in offset = slave bundle = gmem1
#pragma HLS INTERFACE m_axi port = grad_x offset = slave bundle = gmem2
#pragma HLS INTERFACE m_axi port = grad_y offset = slave bundle = gmem3
#pragma HLS INTERFACE s_axilite port = img_in bundle = control
#pragma HLS INTERFACE s_axilite port = grad_x bundle = control
#pragma HLS INTERFACE s_axilite port = grad_y bundle = control
#pragma HLS INTERFACE s_axilite port = rows bundle = control
#pragma HLS INTERFACE s_axilite port = cols bundle = control
#pragma HLS INTERFACE s_axilite port = return bundle = control

    xf::cv::Mat<IN_TYPE, HEIGHT, WIDTH, NPPCX, XF_CV_DEPTH_IN> in_mat(rows, cols);
    xf::cv::Mat<OUT_TYPE, HEIGHT, WIDTH, NPPCX, XF_CV_DEPTH_OUT_GX> gx_mat(rows, cols);
    xf::cv::Mat<OUT_TYPE, HEIGHT, WIDTH, NPPCX, XF_CV_DEPTH_OUT_GY> gy_mat(rows, cols);
#pragma HLS DATAFLOW
    xf::cv::Array2xfMat<PTR_WIDTH, IN_TYPE, HEIGHT, WIDTH, NPPCX, XF_CV_DEPTH_IN>(img_in, in_mat);
    xf::cv::Sobel<XF_BORDER_CONSTANT, FILTER_WIDTH, IN_TYPE, OUT_TYPE, HEIGHT, WIDTH, NPPCX, XF_USE_URAM,
                  XF_CV_DEPTH_IN, XF_CV_DEPTH_OUT_GX, XF_CV_DEPTH_OUT_GY>(in_mat, gx_mat, gy_mat);
    xf::cv::xfMat2Array<PTR_WIDTH, OUT_TYPE, HEIGHT, WIDTH, NPPCX, XF_CV_DEPTH_OUT_GX>(gx_mat, grad_x);
    xf::cv::xfMat2Array<PTR_WIDTH, OUT_TYPE, HEIGHT, WIDTH, NPPCX, XF_CV_DEPTH_OUT_GY>(gy_mat, grad_y);
}

}  // extern "C"
