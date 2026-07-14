from ogma_app.openocd import parse_words, words_to_bytes


def test_parse_mdw_output() -> None:
    text = """
0x20000000: 11223344 55667788 99aabbcc ddeeff00
0x20000010: 00000001
"""
    assert parse_words(text) == [0x11223344, 0x55667788, 0x99AABBCC, 0xDDEEFF00, 1]


def test_words_to_bytes_little_endian() -> None:
    assert words_to_bytes([0x11223344, 0x55667788], 6) == b"\x44\x33\x22\x11\x88\x77"
