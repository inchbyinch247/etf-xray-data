"""SEC 형식을 흉내 낸 작은 ZIP으로 전체 흐름을 검사한다. 실행: python -m pytest -q"""
import json
import zipfile

import pytest

from pipeline import common, us_holdings, figi, publish
from pipeline.common import normalize_us_ticker


def tsv(rows, cols):
    return "\t".join(cols) + "\n" + "\n".join("\t".join(str(r.get(c, "")) for c in cols) for r in rows) + "\n"


SUB = ["ACCESSION_NUMBER", "FILING_DATE", "FILE_NUM", "SUB_TYPE", "REPORT_ENDING_PERIOD", "REPORT_DATE", "IS_LAST_FILING"]
INFO = ["ACCESSION_NUMBER", "SERIES_NAME", "SERIES_ID", "SERIES_LEI", "TOTAL_ASSETS", "NET_ASSETS"]
HOLD = ["ACCESSION_NUMBER", "HOLDING_ID", "ISSUER_NAME", "ISSUER_LEI", "ISSUER_TITLE", "ISSUER_CUSIP",
        "BALANCE", "PERCENTAGE", "PAYOFF_PROFILE", "ASSET_CAT", "ISSUER_TYPE", "INVESTMENT_COUNTRY"]
IDS = ["HOLDING_ID", "IDENTIFIERS_ID", "IDENTIFIER_ISIN", "IDENTIFIER_TICKER", "OTHER_IDENTIFIER", "OTHER_IDENTIFIER_DESC"]


def make_zip(path, subs, infos, holds, ids):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("2026q3_nport/SUBMISSION.tsv", tsv(subs, SUB))
        z.writestr("2026q3_nport/FUND_REPORTED_INFO.tsv", tsv(infos, INFO))
        z.writestr("2026q3_nport/FUND_REPORTED_HOLDING.tsv", tsv(holds, HOLD))
        z.writestr("2026q3_nport/IDENTIFIERS.tsv", tsv(ids, IDS))


@pytest.fixture
def env(tmp_path, monkeypatch):
    for mod in (common, us_holdings, figi, publish):
        for name, sub in [("OUT", "docs/v1"), ("CACHE", "cache"), ("REPORTS", "reports"), ("CONFIG", "config")]:
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, tmp_path / sub)
    monkeypatch.setattr(figi, "CACHE_FILE", tmp_path / "cache/figi.json")
    calls = []

    def fake_figi(cusips):
        calls.append(list(cusips))
        return {"67066G104": "NVDA"}
    monkeypatch.setattr(figi, "map_cusips", fake_figi)
    (tmp_path / "config").mkdir()
    (tmp_path / "config/us_etfs.json").write_text(json.dumps({"names": {"VOO": "Vanguard S&P 500 ETF"}}))
    return tmp_path, calls


def holding(acc, hid, name, cusip, pct, cat="EC", payoff="Long", country="US"):
    return dict(ACCESSION_NUMBER=acc, HOLDING_ID=hid, ISSUER_NAME=name, ISSUER_TITLE=name,
                ISSUER_CUSIP=cusip, PERCENTAGE=pct, PAYOFF_PROFILE=payoff, ASSET_CAT=cat,
                INVESTMENT_COUNTRY=country)


def test_full_flow(env):
    tmp, calls = env
    tickers = {"fields": ["cik", "seriesId", "classId", "symbol"], "data": [
        [1, "S_VAN500", "C1", "VFIAX"], [1, "S_VAN500", "C2", "VOO"],
        [2, "S_SPY", "C3", "SPY"], [3, "S_AGG", "C4", "AGG"], [4, "S_TQQQ", "C5", "TQQQ"],
        [5, "S_MF", "C6", "ABCDX"], [6, "S_SMALL", "C7", "SMAL"],
    ]}
    tj = tmp / "tickers.json"
    tj.write_text(json.dumps(tickers))

    # 이전 분기: SPY 옛 보고서
    make_zip(tmp / "q2.zip",
             [dict(ACCESSION_NUMBER="A_SPY_OLD", FILING_DATE="15-MAY-2026", SUB_TYPE="NPORT-P", REPORT_DATE="31-MAR-2026")],
             [dict(ACCESSION_NUMBER="A_SPY_OLD", SERIES_NAME="SPDR S&P 500 ETF Trust", SERIES_ID="S_SPY", NET_ASSETS="600000000000")],
             [holding("A_SPY_OLD", "900", "OLD CO", "111111111", "100")],
             [dict(HOLDING_ID="900", IDENTIFIERS_ID="1", IDENTIFIER_TICKER="OLD")])

    subs = [dict(ACCESSION_NUMBER=a, FILING_DATE="20-AUG-2026", SUB_TYPE="NPORT-P", REPORT_DATE="30-JUN-2026")
            for a in ["A_VAN", "A_SPY", "A_AGG", "A_TQQQ", "A_MF", "A_SMALL"]]
    infos = [
        dict(ACCESSION_NUMBER="A_VAN", SERIES_NAME="Vanguard 500 Index Fund", SERIES_ID="S_VAN500", NET_ASSETS="1400000000000"),
        dict(ACCESSION_NUMBER="A_SPY", SERIES_NAME="SPDR S&P 500 ETF Trust", SERIES_ID="S_SPY", NET_ASSETS="650000000000"),
        dict(ACCESSION_NUMBER="A_AGG", SERIES_NAME="iShares Core US Aggregate Bond ETF", SERIES_ID="S_AGG", NET_ASSETS="120000000000"),
        dict(ACCESSION_NUMBER="A_TQQQ", SERIES_NAME="UltraPro QQQ", SERIES_ID="S_TQQQ", NET_ASSETS="25000000000"),
        dict(ACCESSION_NUMBER="A_MF", SERIES_NAME="Some Mutual Fund", SERIES_ID="S_MF", NET_ASSETS="900000000000"),
        dict(ACCESSION_NUMBER="A_SMALL", SERIES_NAME="Small Equity ETF", SERIES_ID="S_SMALL", NET_ASSETS="1000000"),
    ]
    holds = [
        holding("A_VAN", "1", "APPLE INC", "037833100", "7.0"),
        holding("A_VAN", "2", "BERKSHIRE HATHAWAY INC", "084670702", "1.7"),
        holding("A_VAN", "3", "MICROSOFT CORP", "594918104", "6.5"),
        holding("A_VAN", "4", "NVIDIA CORP", "67066G104", "6.8"),
        holding("A_VAN", "5", "Samsung Electronics Co Ltd", "Y74718100", "0.5", country="KR"),
        holding("A_VAN", "6", "Toyota Motor Corp", "J92676113", "0.4", country="JP"),
        holding("A_VAN", "7", "Taiwan Semiconductor ADR", "874039100", "0.3", country="TW"),
        holding("A_VAN", "8", "Cash mgmt fund", "999999999", "2.0", cat="STIV"),
        holding("A_VAN", "9", "APPLE INC", "037833100", "0.1"),  # 같은 종목 두 줄 → 합산
        holding("A_VAN", "10", "OTHER STOCKS", "333333333", "70"),
        holding("A_SPY", "23", "OTHER STOCKS", "333333333", "80"),
        holding("A_SPY", "20", "MICROSOFT CORP", "594918104", "6.6"),
        holding("A_SPY", "21", "APPLE INC", "037833100", "7.1"),
        holding("A_SPY", "22", "Tiny Co", "222222222", "0.0001"),
        holding("A_AGG", "30", "US TREASURY", "912828ZZ0", "95", cat="DBT"),
        holding("A_TQQQ", "40", "APPLE INC", "037833100", "50"),
        holding("A_SMALL", "50", "APPLE INC", "037833100", "90"),
    ]
    ids = [
        dict(HOLDING_ID="1", IDENTIFIERS_ID="1", IDENTIFIER_ISIN="US0378331005", IDENTIFIER_TICKER="AAPL"),
        dict(HOLDING_ID="2", IDENTIFIERS_ID="2", IDENTIFIER_ISIN="US0846707026", IDENTIFIER_TICKER="BRK/B"),
        dict(HOLDING_ID="3", IDENTIFIERS_ID="3", IDENTIFIER_ISIN="US5949181045"),   # 티커 없음 → SPY 행에서 빌림
        dict(HOLDING_ID="4", IDENTIFIERS_ID="4", IDENTIFIER_ISIN="US67066G1040"),   # 티커 없음 → FIGI
        dict(HOLDING_ID="5", IDENTIFIERS_ID="5", IDENTIFIER_ISIN="KR7005930003", IDENTIFIER_TICKER="005930 KS"),
        dict(HOLDING_ID="6", IDENTIFIERS_ID="6", IDENTIFIER_ISIN="JP3633400001", IDENTIFIER_TICKER="7203"),
        dict(HOLDING_ID="7", IDENTIFIERS_ID="7", IDENTIFIER_ISIN="US8740391003", IDENTIFIER_TICKER="TSM US"),
        dict(HOLDING_ID="9", IDENTIFIERS_ID="9", IDENTIFIER_TICKER="AAPL"),
        dict(HOLDING_ID="20", IDENTIFIERS_ID="20", IDENTIFIER_ISIN="US5949181045", IDENTIFIER_TICKER="MSFT"),
        dict(HOLDING_ID="21", IDENTIFIERS_ID="21", IDENTIFIER_TICKER="AAPL"),
        dict(HOLDING_ID="22", IDENTIFIERS_ID="22", IDENTIFIER_TICKER="TINY"),
        dict(HOLDING_ID="10", IDENTIFIERS_ID="10", IDENTIFIER_TICKER="OTHR"),
        dict(HOLDING_ID="23", IDENTIFIERS_ID="23", IDENTIFIER_TICKER="OTHR"),
    ]
    make_zip(tmp / "q3.zip", subs, infos, holds, ids)

    us_holdings.main(["--top", "2", "--local-zip", str(tmp / "q3.zip"), "--local-zip", str(tmp / "q2.zip"),
                      "--tickers-json", str(tj)])
    publish.main()

    out = tmp / "docs/v1"
    files = sorted(p.stem for p in (out / "holdings").glob("*.json"))
    assert files == ["SPY", "VOO"]                 # 채권·레버리지·뮤추얼펀드 제외, 상위 2개

    voo = json.loads((out / "holdings/VOO.json").read_text())
    w = {c["ticker"]: c["weight"] for c in voo["constituents"]}
    assert voo["asOf"] == "2026-06-30"
    assert w["AAPL"] == pytest.approx(7.1)         # 두 줄 합산
    assert w["BRK.B"] == 1.7                       # BRK/B 정리
    assert w["MSFT"] == 6.5                        # 다른 펀드에서 티커 빌림
    assert w["NVDA"] == 6.8                        # FIGI
    assert w["005930"] == 0.5                      # 국내 코드
    assert w["JP3633400001"] == 0.4                # 기타 해외는 ISIN
    assert w["TSM"] == 0.3                         # ADR은 미국 티커
    assert "999999999" not in json.dumps(voo)      # 현금성 제외
    assert calls == [["67066G104"]]

    spy = json.loads((out / "holdings/SPY.json").read_text())
    assert spy["asOf"] == "2026-06-30"             # 최신 분기 보고서 사용
    assert "TINY" not in {c["ticker"] for c in spy["constituents"]}  # 너무 작은 비중 생략

    inst = json.loads((out / "instruments.json").read_text())
    by = {i["ticker"]: i for i in inst}
    assert by["VOO"]["name"] == "Vanguard S&P 500 ETF" and by["VOO"]["kind"] == "ETF"
    assert by["AAPL"] == {"ticker": "AAPL", "name": "Apple Inc", "kind": "STOCK", "market": "US"}
    assert "005930" not in by and "JP3633400001" not in by

    rep = (tmp / "reports/us_etf_list.csv").read_text(encoding="utf-8-sig")
    assert "AGG" in rep and "주식 비중 낮음" in rep


@pytest.mark.parametrize("raw,exp", [
    ("BRK/B", "BRK.B"), ("brk-b", "BRK.B"), ("BRK B", "BRK.B"), ("AAPL US Equity", "AAPL"),
    ("AAPL UW", "AAPL"), ("7203", None), ("", None), ("GOOGL", "GOOGL"),
])
def test_normalize(raw, exp):
    assert normalize_us_ticker(raw) == exp
