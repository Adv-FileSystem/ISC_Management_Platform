"""생성기: 사용자 함수의 시그니처 해석, 래퍼 렌더링, 시작 파일."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from isc.generate import (
    SignatureError, parse_signature, render_kernel, render_starter, strip_comments,
)
from isc.operations import get_profile

INTEGERS = ["u8", "u16", "u32", "u64", "i8", "i16", "i32", "i64"]
FILTER_SPEC = {
    "symbol": "isc_filter", "signature": "bool isc_filter(T value)", "returns": "bool",
    "output_element": "input", "element_types": INTEGERS + ["f32"],
}
MAP_SPEC = {
    "symbol": "isc_map", "signature": "U isc_map(T value)", "returns": "element",
    "output_element": "declared", "element_types": INTEGERS + ["f32"],
}
AGGREGATE_SPEC = {
    "symbol": "isc_where", "signature": "bool isc_where(T value)", "returns": "bool",
    "output_element": "none", "element_types": INTEGERS,
}


class StripComments(unittest.TestCase):
    def test_line_and_block_comments_disappear(self):
        self.assertEqual(strip_comments("a // x\nb /* y\nz */ c").split(), ["a", "b", "c"])


class ParseSignature(unittest.TestCase):
    def test_hls_spelling_is_read_and_kept(self):
        types = parse_signature("inline bool isc_filter(ap_uint<32> value) { return true; }", FILTER_SPEC)
        self.assertEqual(types.inp.element.name, "u32")
        self.assertEqual(types.inp.spelling, "ap_uint<32>")
        self.assertEqual(types.out, types.inp)

    def test_standard_float_and_signed_spellings(self):
        float_types = parse_signature("bool isc_filter(float value) { return true; }", FILTER_SPEC)
        signed_types = parse_signature("bool isc_filter(int16_t value) { return true; }", FILTER_SPEC)
        self.assertEqual(float_types.inp.element.name, "f32")
        self.assertEqual(signed_types.inp.element.name, "i16")

    def test_const_inline_static_and_spacing_are_accepted(self):
        source = "static inline bool isc_filter( const ap_uint < 8 >  value ){ return true; }"
        types = parse_signature(source, FILTER_SPEC)
        self.assertEqual((types.inp.element.name, types.inp.spelling), ("u8", "ap_uint<8>"))

    def test_map_reads_the_output_type_from_the_return(self):
        types = parse_signature("uint8_t isc_map(float value) { return 0; }", MAP_SPEC)
        self.assertEqual((types.inp.element.name, types.out.element.name), ("f32", "u8"))

    def test_aggregate_has_no_output_element(self):
        types = parse_signature("bool isc_where(int32_t value) { return value > 0; }", AGGREGATE_SPEC)
        self.assertEqual(types.inp.element.name, "i32")
        self.assertIsNone(types.out)

    def test_aggregate_rejects_float_and_does_not_offer_it(self):
        with self.assertRaises(SignatureError) as caught:
            parse_signature("bool isc_where(float value) { return true; }", AGGREGATE_SPEC)
        supported = str(caught.exception).split("supported:")[1]
        self.assertNotIn("float", supported)
        self.assertIn("uint32_t", supported)

    def test_missing_function_names_the_expected_signature(self):
        with self.assertRaises(SignatureError) as caught:
            parse_signature("int twice(int x) { return 2 * x; }", FILTER_SPEC)
        self.assertIn("isc_filter", str(caught.exception))
        self.assertIn("bool isc_filter(T value)", str(caught.exception))

    def test_reference_parameters_are_not_supported(self):
        with self.assertRaises(SignatureError) as caught:
            parse_signature("bool isc_filter(const uint32_t& value) { return true; }", FILTER_SPEC)
        self.assertIn("unsupported form", str(caught.exception))

    def test_the_parameter_must_be_named_value(self):
        with self.assertRaises(SignatureError):
            parse_signature("bool isc_filter(uint32_t v) { return true; }", FILTER_SPEC)

    def test_filter_must_return_bool(self):
        with self.assertRaises(SignatureError) as caught:
            parse_signature("int isc_filter(uint32_t value) { return 1; }", FILTER_SPEC)
        self.assertIn("must return bool", str(caught.exception))

    def test_unsupported_parameter_type_lists_the_supported_ones(self):
        with self.assertRaises(SignatureError) as caught:
            parse_signature("bool isc_filter(double value) { return true; }", FILTER_SPEC)
        message = str(caught.exception)
        for expected in ("double", "uint32_t", "float"):
            self.assertIn(expected, message)

    def test_unsupported_map_return_type_is_named_as_such(self):
        with self.assertRaises(SignatureError) as caught:
            parse_signature("double isc_map(float value) { return 0; }", MAP_SPEC)
        self.assertIn("return type", str(caught.exception))

    def test_a_commented_out_function_does_not_count(self):
        source = "// bool isc_filter(uint32_t value) { return true; }\nint other();"
        with self.assertRaises(SignatureError):
            parse_signature(source, FILTER_SPEC)


class HookTypesViews(unittest.TestCase):
    def test_adapter_view_has_widths_and_struct_codes(self):
        types = parse_signature("uint8_t isc_map(float value) { return 0; }", MAP_SPEC)
        self.assertEqual(types.adapter_view(), {
            "in": "f32", "in_bytes": 4, "in_fmt": "f",
            "out": "u8", "out_bytes": 1, "out_fmt": "B",
        })

    def test_adapter_view_omits_the_output_for_aggregates(self):
        types = parse_signature("bool isc_where(int16_t value) { return true; }", AGGREGATE_SPEC)
        self.assertEqual(types.adapter_view(), {"in": "i16", "in_bytes": 2, "in_fmt": "h"})

    def test_fingerprint_view_distinguishes_spellings(self):
        hls = parse_signature("bool isc_filter(ap_uint<32> value) { return true; }", FILTER_SPEC)
        std = parse_signature("bool isc_filter(uint32_t value) { return true; }", FILTER_SPEC)
        self.assertNotEqual(hls.fingerprint_view(), std.fingerprint_view())


class RenderKernel(unittest.TestCase):
    def test_types_are_substituted_as_the_user_spelled_them(self):
        types = parse_signature("uint8_t isc_map(float value) { return 0; }", MAP_SPEC)
        self.assertEqual(
            render_kernel("const ${IN_T}* in; ${OUT_T}* out;", types),
            "const float* in; uint8_t* out;",
        )

    def test_the_accumulator_is_signed_only_for_signed_inputs(self):
        signed = parse_signature("bool isc_where(int8_t value) { return true; }", AGGREGATE_SPEC)
        unsigned = parse_signature("bool isc_where(ap_uint<8> value) { return true; }", AGGREGATE_SPEC)
        self.assertEqual(render_kernel("${ACC_T}", signed), "int64_t")
        self.assertEqual(render_kernel("${ACC_T}", unsigned), "uint64_t")

    def test_an_unknown_placeholder_fails_loudly(self):
        types = parse_signature("bool isc_filter(uint32_t value) { return true; }", FILTER_SPEC)
        with self.assertRaises(KeyError):
            render_kernel("${NOPE}", types)


class RenderStarter(unittest.TestCase):
    SPEC = {
        **FILTER_SPEC,
        "input_description": "{in} 값", "output_description": "{in} 값만",
        "examples": {"int": ["return value >= 1000;", "return value < 5;"],
                     "float": ["return value > 0.5f;"]},
    }
    TEMPLATE = "// ${PROFILE} ${IN_T}\n${EXAMPLES}\n// ${INPUT_DESC} | ${OUTPUT_DESC}\n"

    def test_integer_input_gets_integer_examples_and_the_standard_spelling(self):
        self.assertEqual(
            render_starter(self.TEMPLATE, self.SPEC, "filter", "u32"),
            "// filter uint32_t\n// 예)  return value >= 1000;\n//      return value < 5;\n"
            "// u32 값 | u32 값만\n",
        )

    def test_float_input_gets_float_examples(self):
        text = render_starter(self.TEMPLATE, self.SPEC, "filter", "f32")
        self.assertIn("// 예)  return value > 0.5f;", text)
        self.assertIn("// filter float\n", text)

    def test_an_unsupported_type_name_is_rejected(self):
        with self.assertRaises(SignatureError):
            render_starter(self.TEMPLATE, self.SPEC, "filter", "double")

    def test_a_profile_without_an_output_element_rejects_an_output_type(self):
        with self.assertRaises(SignatureError):
            render_starter(self.TEMPLATE, self.SPEC, "filter", "u32", "u8")

    def test_map_output_defaults_to_the_input_type(self):
        spec = {**MAP_SPEC, "input_description": "{in}", "output_description": "{out}",
                "examples": {"int": ["return value;"], "float": ["return value;"]}}
        self.assertEqual(render_starter("${IN_T}->${OUT_T}", spec, "map", "i16"), "int16_t->int16_t")

    def test_map_output_type_can_differ_from_the_input_type(self):
        spec = {**MAP_SPEC, "input_description": "{in}", "output_description": "{out}",
                "examples": {"int": ["return value;"], "float": ["return value;"]}}
        self.assertEqual(
            render_starter("${IN_T}->${OUT_T}", spec, "map", "f32", "u8"), "float->uint8_t",
        )


def hls_include() -> Path | None:
    candidates = [os.environ.get("ISC_HLS_INCLUDE")]
    for variable in ("XILINX_HLS", "XILINX_VITIS"):
        root = os.environ.get(variable)
        if root:
            candidates.append(str(Path(root) / "include"))
    for candidate in candidates:
        if candidate and (Path(candidate) / "ap_int.h").is_file():
            return Path(candidate)
    return None


@unittest.skipUnless(
    hls_include() and shutil.which("g++"),
    "needs g++ and the Vitis HLS headers (set ISC_HLS_INCLUDE to the directory with ap_int.h)",
)
class GeneratedKernelsRun(unittest.TestCase):
    """생성한 래퍼를 g++ 로 컴파일해 배열에 직접 호출한다. 합성 가능성이 아니라 데이터 처리 논리를 본다."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.work = Path(self._temp.name)

    def run_kernel(self, profile_id: str, logic: str, driver: str) -> list[str]:
        profile = get_profile(profile_id)
        types = parse_signature(logic, profile.hook_spec)
        template = (profile.directory / profile.raw["kernel"]["source"]).read_text()
        (self.work / "isc_user_logic.hpp").write_text(logic)
        (self.work / "kernel.cpp").write_text(render_kernel(template, types))
        (self.work / "driver.cpp").write_text(
            '#include "kernel.cpp"\n#include <iostream>\nint main() {\n' + driver + "\n}\n"
        )
        built = subprocess.run(
            ["g++", "-std=c++14", f"-I{hls_include()}", f"-I{self.work}",
             str(self.work / "driver.cpp"), "-o", str(self.work / "driver")],
            capture_output=True, text=True,
        )
        self.assertEqual(built.returncode, 0, built.stderr[-2000:])
        done = subprocess.run([str(self.work / "driver")], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.split()

    def test_filter_keeps_matching_values_in_order_for_an_hls_integer(self):
        logic = ("#include <ap_int.h>\n"
                 "inline bool isc_filter(ap_uint<32> value) "
                 "{ return value >= 1000 && (value & 1) == 0; }\n")
        out = self.run_kernel("filter", logic, """
            ap_uint<32> in[6] = {10, 1500, 20, 3000, 2001, 4096};
            ap_uint<32> out[6];
            uint32_t kept = 0;
            isc_filter_kernel(in, out, 6, &kept);
            std::cout << kept;
            for (uint32_t i = 0; i < kept; ++i) std::cout << ' ' << (unsigned long long)out[i];
        """)
        self.assertEqual(out, ["3", "1500", "3000", "4096"])

    def test_filter_handles_floats(self):
        logic = "inline bool isc_filter(float value) { return value >= -1.5f && value <= 2.0f; }\n"
        out = self.run_kernel("filter", logic, """
            float in[5] = {-3.0f, -1.5f, 0.25f, 2.0f, 2.5f};
            float out[5];
            uint32_t kept = 0;
            isc_filter_kernel(in, out, 5, &kept);
            std::cout << kept;
            for (uint32_t i = 0; i < kept; ++i) std::cout << ' ' << out[i];
        """)
        self.assertEqual(out, ["3", "-1.5", "0.25", "2"])

    def test_filter_handles_signed_integers(self):
        logic = "inline bool isc_filter(int32_t value) { return value < 0; }\n"
        out = self.run_kernel("filter", logic, """
            int32_t in[5] = {-5, 0, 7, -2, 9};
            int32_t out[5];
            uint32_t kept = 0;
            isc_filter_kernel(in, out, 5, &kept);
            std::cout << kept;
            for (uint32_t i = 0; i < kept; ++i) std::cout << ' ' << out[i];
        """)
        self.assertEqual(out, ["2", "-5", "-2"])

    def test_map_converts_float_to_u8(self):
        logic = "inline uint8_t isc_map(float value) { return (uint8_t)(value * 2.0f); }\n"
        out = self.run_kernel("map", logic, """
            float in[3] = {0.0f, 1.5f, 100.0f};
            uint8_t out[3];
            isc_map_kernel(in, out, 3);
            for (uint32_t i = 0; i < 3; ++i) std::cout << ' ' << (unsigned)out[i];
        """)
        self.assertEqual(out, ["0", "3", "200"])

    def test_map_keeps_an_hls_integer_type(self):
        logic = ("#include <ap_int.h>\n"
                 "inline ap_uint<32> isc_map(ap_uint<32> value) { return value * 3; }\n")
        out = self.run_kernel("map", logic, """
            ap_uint<32> in[3] = {1, 2, 3};
            ap_uint<32> out[3];
            isc_map_kernel(in, out, 3);
            for (uint32_t i = 0; i < 3; ++i) std::cout << ' ' << (unsigned long long)out[i];
        """)
        self.assertEqual(out, ["3", "6", "9"])

    def test_aggregate_counts_sums_and_bounds_the_selected_values(self):
        logic = "inline bool isc_where(uint32_t value) { return value >= 7; }\n"
        out = self.run_kernel("aggregate", logic, """
            uint32_t in[4] = {5, 10, 20, 7};
            uint64_t result[4] = {1, 1, 1, 1};
            isc_aggregate_kernel(in, result, 4);
            for (int i = 0; i < 4; ++i) std::cout << ' ' << (unsigned long long)result[i];
        """)
        self.assertEqual(out, ["3", "37", "7", "20"])

    def test_aggregate_sign_extends_negative_values(self):
        logic = "inline bool isc_where(int32_t value) { return true; }\n"
        out = self.run_kernel("aggregate", logic, """
            int32_t in[4] = {-4, 3, -9, 8};
            uint64_t result[4];
            isc_aggregate_kernel(in, result, 4);
            for (int i = 0; i < 4; ++i) std::cout << ' ' << (unsigned long long)result[i];
        """)
        self.assertEqual(out, ["4", "18446744073709551614", "18446744073709551607", "8"])

    def test_aggregate_with_no_match_is_all_zeros(self):
        logic = "inline bool isc_where(uint8_t value) { return false; }\n"
        out = self.run_kernel("aggregate", logic, """
            uint8_t in[3] = {1, 2, 3};
            uint64_t result[4] = {9, 9, 9, 9};
            isc_aggregate_kernel(in, result, 3);
            for (int i = 0; i < 4; ++i) std::cout << ' ' << (unsigned long long)result[i];
        """)
        self.assertEqual(out, ["0", "0", "0", "0"])


if __name__ == "__main__":
    unittest.main()
