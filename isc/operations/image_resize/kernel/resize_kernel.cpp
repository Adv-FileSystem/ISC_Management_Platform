/**
 * Bilinear resize of 8-bit gray images, backed by Vitis Vision L1 (Vitis Libraries 2025.1).
 * Configuration: bilinear, max input 3840x2160, max output 1920x1080, MAXDOWNSCALE 2, URAM,
 * 8-bit pointers. Every output pixel then goes through the user function isc_post
 * (isc_user_logic.hpp, staged by ISC from the module's logic file or the operation's starter).
 */
#include <stdint.h>

#include "hls_stream.h"
#include "common/xf_common.hpp"
#include "common/xf_utility.hpp"
#include "imgproc/xf_resize.hpp"
#include "isc_user_logic.hpp"

#define HEIGHT 2160
#define WIDTH 3840
#define NEWHEIGHT 1080
#define NEWWIDTH 1920
#define MAXDOWNSCALE 2
#define INTERPOLATION 1           /* 0 nearest, 1 bilinear, 2 area */
#define XF_USE_URAM 1
#define IN_TYPE XF_8UC1
#define OUT_TYPE XF_8UC1
#define NPPCX XF_NPPC1
#define PTR_WIDTH 8
#define XF_CV_DEPTH_IN 2
#define XF_CV_DEPTH_OUT 2

namespace {

template <int DEPTH_IN, int DEPTH_OUT>
void userPost(xf::cv::Mat<OUT_TYPE, NEWHEIGHT, NEWWIDTH, NPPCX, DEPTH_IN>& in,
              xf::cv::Mat<OUT_TYPE, NEWHEIGHT, NEWWIDTH, NPPCX, DEPTH_OUT>& out, int rows, int cols) {
ISC_USER_POST:
    for (int i = 0; i < rows * cols; i++) {
#pragma HLS PIPELINE II = 1
#pragma HLS LOOP_TRIPCOUNT min = 1 max = NEWHEIGHT * NEWWIDTH
        uint8_t px = in.read(i);
        out.write(i, isc_post(px));
    }
}

}  // namespace

extern "C" {

void isc_resize_bilinear(ap_uint<PTR_WIDTH>* img_in, ap_uint<PTR_WIDTH>* img_out, int rows_in, int cols_in,
                         int rows_out, int cols_out) {
#pragma HLS INTERFACE m_axi port = img_in offset = slave bundle = gmem1
#pragma HLS INTERFACE m_axi port = img_out offset = slave bundle = gmem2
#pragma HLS INTERFACE s_axilite port = img_in bundle = control
#pragma HLS INTERFACE s_axilite port = img_out bundle = control
#pragma HLS INTERFACE s_axilite port = rows_in bundle = control
#pragma HLS INTERFACE s_axilite port = cols_in bundle = control
#pragma HLS INTERFACE s_axilite port = rows_out bundle = control
#pragma HLS INTERFACE s_axilite port = cols_out bundle = control
#pragma HLS INTERFACE s_axilite port = return bundle = control

    xf::cv::Mat<IN_TYPE, HEIGHT, WIDTH, NPPCX, XF_CV_DEPTH_IN> in_mat(rows_in, cols_in);
    xf::cv::Mat<OUT_TYPE, NEWHEIGHT, NEWWIDTH, NPPCX, XF_CV_DEPTH_OUT> out_mat(rows_out, cols_out);
    xf::cv::Mat<OUT_TYPE, NEWHEIGHT, NEWWIDTH, NPPCX, XF_CV_DEPTH_OUT> post_mat(rows_out, cols_out);
#pragma HLS DATAFLOW
    xf::cv::Array2xfMat<PTR_WIDTH, IN_TYPE, HEIGHT, WIDTH, NPPCX, XF_CV_DEPTH_IN>(img_in, in_mat);
    xf::cv::resize<INTERPOLATION, IN_TYPE, HEIGHT, WIDTH, NEWHEIGHT, NEWWIDTH, NPPCX, XF_USE_URAM, MAXDOWNSCALE,
                   XF_CV_DEPTH_IN, XF_CV_DEPTH_OUT>(in_mat, out_mat);
    userPost(out_mat, post_mat, rows_out, cols_out);
    xf::cv::xfMat2Array<PTR_WIDTH, OUT_TYPE, NEWHEIGHT, NEWWIDTH, NPPCX, XF_CV_DEPTH_OUT>(post_mat, img_out);
}

}  // extern "C"
