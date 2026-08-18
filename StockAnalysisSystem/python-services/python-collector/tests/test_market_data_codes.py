from app.market_data.codes import to_ts_code


def test_to_ts_code_supports_mainland_a_share_exchanges():
    assert to_ts_code("600000") == "600000.SH"
    assert to_ts_code("000001") == "000001.SZ"
    assert to_ts_code("300001") == "300001.SZ"
    assert to_ts_code("430047") == "430047.BJ"
    assert to_ts_code("830799") == "830799.BJ"


def test_to_ts_code_preserves_and_normalizes_existing_suffix():
    assert to_ts_code("600000.sh") == "600000.SH"
    assert to_ts_code(" 000001.Sz ") == "000001.SZ"
