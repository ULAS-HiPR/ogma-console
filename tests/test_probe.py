from ogma_app.probe import parse_st_info_probe


def test_parse_st_info_probe_connected() -> None:
    text = """
Found 1 stlink programmers
  serial: 303030303030303030303031
  flash: 65536 (pagesize: 1024)
  sram: 8192
  chipid: 0x0445
  descr: F04x
"""
    result = parse_st_info_probe(text)
    assert result.connected
    assert result.programmers == 1
    assert result.fields["chipid"] == "0x0445"
    assert result.fields["descr"] == "F04x"


def test_parse_st_info_probe_no_target() -> None:
    result = parse_st_info_probe("Found 0 stlink programmers\n")
    assert not result.connected
    assert result.programmers == 0


def test_parse_st_info_probe_tool_error() -> None:
    result = parse_st_info_probe("unknown chip id! 0\n", returncode=1)
    assert not result.connected
    assert result.returncode == 1
