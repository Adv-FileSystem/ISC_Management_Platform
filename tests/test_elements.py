"""요소 타입 레지스트리: 표기를 정준 타입으로, 값을 바이트로."""
from __future__ import annotations

import unittest

from isc.elements import ELEMENTS, lookup, spellings_for


class Registry(unittest.TestCase):
    def test_every_integer_width_exists_in_both_signs_with_the_right_size(self):
        for bits in (8, 16, 32, 64):
            self.assertEqual(ELEMENTS[f"u{bits}"].size, bits // 8)
            self.assertFalse(ELEMENTS[f"u{bits}"].signed)
            self.assertEqual(ELEMENTS[f"i{bits}"].size, bits // 8)
            self.assertTrue(ELEMENTS[f"i{bits}"].signed)

    def test_float32_is_four_bytes_and_flagged_as_float(self):
        self.assertEqual(ELEMENTS["f32"].size, 4)
        self.assertTrue(ELEMENTS["f32"].is_float)
        self.assertFalse(ELEMENTS["u32"].is_float)

    def test_pack_is_little_endian(self):
        self.assertEqual(ELEMENTS["u32"].pack([1, 258]), bytes([1, 0, 0, 0, 2, 1, 0, 0]))

    def test_unpack_reads_signed_values(self):
        self.assertEqual(ELEMENTS["i16"].unpack(bytes([0xFF, 0xFF, 0x02, 0x00])), (-1, 2))

    def test_unpack_rejects_a_partial_element(self):
        with self.assertRaises(ValueError):
            ELEMENTS["u32"].unpack(b"abc")

    def test_standard_spelling_is_the_c_name(self):
        self.assertEqual(ELEMENTS["u32"].standard, "uint32_t")
        self.assertEqual(ELEMENTS["i8"].standard, "int8_t")
        self.assertEqual(ELEMENTS["f32"].standard, "float")


class Spellings(unittest.TestCase):
    def test_hls_and_standard_spellings_name_the_same_element(self):
        for text in ("ap_uint<32>", "uint32_t", "ap_uint < 32 >"):
            self.assertEqual(lookup(text).element.name, "u32", text)
        for text in ("ap_int<16>", "int16_t"):
            self.assertEqual(lookup(text).element.name, "i16", text)
        self.assertEqual(lookup("float").element.name, "f32")

    def test_the_spelling_keeps_the_user_text_without_whitespace(self):
        self.assertEqual(lookup("ap_uint < 32 >").spelling, "ap_uint<32>")

    def test_unknown_spellings_are_none(self):
        for text in ("unsigned int", "double", "ap_uint<12>", "bool"):
            self.assertIsNone(lookup(text), text)

    def test_spellings_for_lists_only_the_requested_elements(self):
        self.assertEqual(spellings_for(["f32"]), ["float"])
        self.assertEqual(spellings_for(["u8"]), ["ap_uint<8>", "uint8_t"])


if __name__ == "__main__":
    unittest.main()
