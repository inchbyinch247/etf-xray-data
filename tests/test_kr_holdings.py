"""KRX 응답을 흉내 내서 국내 ETF 선정·구성종목 변환을 검사한다."""
import datetime as dt
import json

import pytest

from pipeline import common, kr_holdings


@pytest.fixture
def env(tmp_path, monkeypatch):
    for name, sub in [("OUT", "docs/v1"), ("CACHE", "cache"), ("REPORTS", "reports"), ("CONFIG", "config")]:
        monkeypatch.setattr(kr_holdings, name, tmp_path / sub)
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache/us_isin_ticker.json").write_text(json.dumps({"US0378331005": "AAPL"}))
    return tmp_path


PRICES = [
    {"ISU_SRT_CD": "069500", "ISU_ABBRV": "KODEX 200", "INVSTASST_NETASST_TOTAMT": "8,000,000,000,000"},
    {"ISU_SRT_CD": "360750", "ISU_ABBRV": "TIGER 미국S&P500", "INVSTASST_NETASST_TOTAMT": "9,000,000,000,000"},
    {"ISU_SRT_CD": "122630", "ISU_ABBRV": "KODEX 레버리지", "INVSTASST_NETASST_TOTAMT": "2,000,000,000,000"},
    {"ISU_SRT_CD": "153130", "ISU_ABBRV": "KODEX 단기채권", "INVSTASST_NETASST_TOTAMT": "1,500,000,000,000"},
    {"ISU_SRT_CD": "999990", "ISU_ABBRV": "작은 ETF", "INVSTASST_NETASST_TOTAMT": "50,000,000,000"},
    {"ISU_SRT_CD": "888880", "ISU_ABBRV": "스왑형 ETF", "INVSTASST_NETASST_TOTAMT": "300,000,000,000"},
]
INFO = [
    {"ISU_SRT_CD": "069500", "ISU_CD": "KR7069500007", "IDX_ASST_CLSS_NM": "주식", "COM_ABBRV": "삼성자산운용"},
    {"ISU_SRT_CD": "360750", "ISU_CD": "KR7360750004", "IDX_ASST_CLSS_NM": "주식", "COM_ABBRV": "미래에셋자산운용"},
    {"ISU_SRT_CD": "122630", "ISU_CD": "KR7122630007", "IDX_ASST_CLSS_NM": "주식", "COM_ABBRV": "삼성자산운용"},
    {"ISU_SRT_CD": "153130", "ISU_CD": "KR7153130000", "IDX_ASST_CLSS_NM": "채권", "COM_ABBRV": "삼성자산운용"},
    {"ISU_SRT_CD": "888880", "ISU_CD": "KR7888880000", "IDX_ASST_CLSS_NM": "주식", "COM_ABBRV": "어딘가운용"},
]
PDF = {
    "KR7069500007": [
        {"COMPST_ISU_CD": "005930", "COMPST_ISU_NM": "삼성전자", "COMPST_RTO": "25.5", "VALU_AMT": "255"},
        {"COMPST_ISU_CD": "KR7000660001", "COMPST_ISU_NM": "SK하이닉스", "COMPST_RTO": "9.0", "VALU_AMT": "90"},
        {"COMPST_ISU_CD": "", "COMPST_ISU_NM": "원화현금", "COMPST_RTO": "0.5", "VALU_AMT": "5"},
        {"COMPST_ISU_CD": "KR4101V30001", "COMPST_ISU_NM": "코스피200 F 202612", "COMPST_RTO": "", "VALU_AMT": "0"},
        {"COMPST_ISU_CD": "000000", "COMPST_ISU_NM": "기타주식묶음", "COMPST_RTO": "64", "VALU_AMT": "640"},
    ],
    "KR7360750004": [  # 비중 칸이 비어 있어 금액으로 계산
        {"COMPST_ISU_CD": "US0378331005", "COMPST_ISU_NM": "APPLE INC", "COMPST_RTO": "", "VALU_AMT": "70"},
        {"COMPST_ISU_CD": "MSFT US EQUITY", "COMPST_ISU_NM": "MICROSOFT CORP", "COMPST_RTO": "", "VALU_AMT": "65"},
        {"COMPST_ISU_CD": "BRK/B US", "COMPST_ISU_NM": "BERKSHIRE", "COMPST_RTO": "", "VALU_AMT": "15"},
        {"COMPST_ISU_CD": "US5949181045X", "COMPST_ISU_NM": "이상한 코드", "COMPST_RTO": "", "VALU_AMT": "0"},
        {"COMPST_ISU_CD": "OTHR", "COMPST_ISU_NM": "OTHERS", "COMPST_RTO": "", "VALU_AMT": "840"},
        {"COMPST_ISU_CD": "", "COMPST_ISU_NM": "USD 현금", "COMPST_RTO": "", "VALU_AMT": "10"},
    ],
    "KR7888880000": [
        {"COMPST_ISU_CD": "", "COMPST_ISU_NM": "TRS 스왑", "COMPST_RTO": "95", "VALU_AMT": "950"},
        {"COMPST_ISU_CD": "005930", "COMPST_ISU_NM": "삼성전자", "COMPST_RTO": "5", "VALU_AMT": "50"},
    ],
}


def fake_post(bld, **p):
    if bld == kr_holdings.BLD_PRICE:
        return PRICES if p["trdDd"] == "20261015" else []      # 16일(오늘)은 아직 자료 없음
    if bld == kr_holdings.BLD_INFO:
        return INFO
    return PDF.get(p["isuCd"], [])


def test_kr_flow(env, monkeypatch):
    monkeypatch.setattr(kr_holdings, "post", fake_post)
    monkeypatch.setattr(kr_holdings.time, "sleep", lambda s: None)
    kr_holdings.build(kr_holdings.MIN_AUM, dt.date(2026, 10, 16))

    out = env / "docs/v1"
    assert sorted(p.stem for p in (out / "holdings").glob("*.json")) == ["069500", "360750"]

    k200 = json.loads((out / "holdings/069500.json").read_text())
    w = {c["ticker"]: c["weight"] for c in k200["constituents"]}
    assert k200["asOf"] == "2026-10-15" and k200["source"] == "삼성자산운용 공시(PDF)"
    assert w["005930"] == 25.5 and w["000660"] == 9.0
    assert len(w) == 3                                   # 현금·선물 제외

    sp = json.loads((out / "holdings/360750.json").read_text())
    w = {c["ticker"]: c["weight"] for c in sp["constituents"]}
    assert w["AAPL"] == pytest.approx(7.0)               # ISIN → 티커, 금액으로 비중 계산
    assert w["MSFT"] == pytest.approx(6.5)
    assert w["BRK.B"] == pytest.approx(1.5)

    inst = {i["ticker"]: i for i in json.loads((out / "instruments_kr.json").read_text())}
    assert inst["069500"]["kind"] == "ETF" and inst["005930"]["kind"] == "STOCK"
    assert "AAPL" not in inst                            # 미국 종목은 미국 목록에서

    rep = (env / "reports/kr_etf_list.csv").read_text(encoding="utf-8-sig")
    assert "레버리지" in rep and "자산 분류 '채권'" in rep and "주식 비중 5%" in rep
    assert "작은 ETF" not in rep


@pytest.mark.parametrize("code,exp", [
    ("005930", "005930"), ("KR7005930003", "005930"), ("KR4101V30001", None), ("0126Z0", "0126Z0"),
    ("US0378331005", "AAPL"), ("JP3633400001", "JP3633400001"), ("AAPL US EQUITY", "AAPL"),
    ("BRK/B US", "BRK.B"), ("", None),
])
def test_key(code, exp):
    assert kr_holdings.constituent_key(code, {"US0378331005": "AAPL"}) == exp
