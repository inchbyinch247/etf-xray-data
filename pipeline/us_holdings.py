"""미국 ETF 구성종목 수집 (SEC Form N-PORT 분기 데이터셋).

하는 일
1. SEC 펀드 티커 목록(company_tickers_mf.json)에서 ETF 후보를 고른다.
2. 최근 N-PORT 데이터셋 2개 분기를 받아 펀드별 최신 보고서를 찾는다.
3. 순자산 큰 순으로 보유내역을 읽고, 주식 비중이 낮은 ETF(채권·원자재·현금형)와
   레버리지·인버스 ETF를 뺀 뒤 상위 N개(기본 300)를 남긴다.
4. ETF별 구성종목 JSON(docs/v1/holdings/<티커>.json)과 검색 목록을 만든다.

실행: python -m pipeline.us_holdings   (옵션: --top 300 --local-zip 파일경로)
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
import sys
import tempfile
import time
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd
import requests

from .common import (
    CACHE, CONFIG, OUT, REPORTS, SEC_USER_AGENT,
    is_us_isin, kr_code_from_isin, normalize_us_ticker, read_json, write_json,
)
from . import figi

DATASET_URL = "https://www.sec.gov/files/dera/data/form-n-port-data-sets/{q}_nport.zip"
TICKERS_MF_URL = "https://www.sec.gov/files/company_tickers_mf.json"

EQUITY_CATS = {"EC", "EP"}          # 보통주, 우선주
MIN_EQUITY_PCT = 60.0               # 이 비율 미만이면 주식형이 아니라고 본다
MIN_WEIGHT = 0.001                  # 0.001% 미만 종목은 파일에서 생략(합계는 '기타'로 남음)
LEVERAGED = re.compile(
    r"(?:\b[23]X\b|\bULTRA|\bINVERSE\b|\bBEAR\b|\bBULL\b|LEVERAGED|\b-?1X\b|\bSHORT\b|DAILY)",
    re.I,
)
CHUNK = 400_000

SESSION = requests.Session()
SESSION.headers["User-Agent"] = SEC_USER_AGENT


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- 다운로드
def recent_quarters(n: int, today: dt.date | None = None) -> list[str]:
    today = today or dt.date.today()
    y, q = today.year, (today.month - 1) // 3 + 1
    out = []
    for _ in range(n + 2):  # 이번 분기 파일이 아직 없을 수 있어 여유를 둔다
        out.append(f"{y}q{q}")
        q -= 1
        if q == 0:
            y, q = y - 1, 4
    return out


def download_datasets(want: int, workdir: Path) -> list[Path]:
    paths = []
    for q in recent_quarters(want):
        if len(paths) == want:
            break
        url = DATASET_URL.format(q=q)
        dest = workdir / f"{q}_nport.zip"
        log(f"[다운로드] {url}")
        with SESSION.get(url, stream=True, timeout=120) as r:
            if r.status_code == 404:
                log("  아직 공개되지 않음, 이전 분기로")
                continue
            r.raise_for_status()
            with open(dest, "wb") as f:
                for block in r.iter_content(1 << 20):
                    f.write(block)
        paths.append(dest)
        time.sleep(1)
    if not paths:
        raise SystemExit("N-PORT 데이터셋을 하나도 받지 못했어요")
    return paths


def fetch_etf_candidates() -> dict[str, str]:
    """series_id → ETF 티커. 뮤추얼펀드 티커(5글자, X로 끝남)는 뺀다."""
    r = SESSION.get(TICKERS_MF_URL, timeout=60)
    r.raise_for_status()
    js = r.json()
    idx = {f: i for i, f in enumerate(js["fields"])}
    by_series: dict[str, list[str]] = defaultdict(list)
    for row in js["data"]:
        sym = str(row[idx["symbol"]] or "").strip().upper()
        sid = row[idx["seriesId"]]
        if sid and re.fullmatch(r"[A-Z]{1,4}", sym):
            by_series[sid].append(sym)
    return {sid: sorted(syms, key=len)[0] for sid, syms in by_series.items()}


# ---------------------------------------------------------------- TSV 읽기
def _member(zf: zipfile.ZipFile, table: str) -> str:
    for name in zf.namelist():
        if name.upper().endswith(f"{table}.TSV"):
            return name
    raise KeyError(f"{table}.tsv 가 ZIP 안에 없어요")


def read_table(zf, table, usecols, chunksize=None):
    return pd.read_csv(
        zf.open(_member(zf, table)), sep="\t", dtype=str, usecols=usecols,
        quoting=csv.QUOTE_NONE, keep_default_na=False, chunksize=chunksize,
        encoding="utf-8", encoding_errors="replace", on_bad_lines="skip",
    )


def latest_reports(zips: list[Path], series_to_ticker: dict[str, str]) -> pd.DataFrame:
    """ETF 후보 series마다 가장 최근 보고서 1건 (데이터셋 여러 개를 합쳐서)."""
    frames = []
    for zp in zips:
        with zipfile.ZipFile(zp) as zf:
            sub = read_table(zf, "SUBMISSION",
                             ["ACCESSION_NUMBER", "FILING_DATE", "SUB_TYPE", "REPORT_DATE"])
            info = read_table(zf, "FUND_REPORTED_INFO",
                              ["ACCESSION_NUMBER", "SERIES_NAME", "SERIES_ID", "NET_ASSETS"])
        df = info.merge(sub, on="ACCESSION_NUMBER")
        df["ZIP"] = str(zp)
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    df = df[df["SUB_TYPE"].str.upper().isin(["NPORT-P", "NPORT-P/A"])]
    df = df[df["SERIES_ID"].isin(series_to_ticker)]
    df["REPORT_DATE_D"] = pd.to_datetime(df["REPORT_DATE"], errors="coerce", format="mixed")
    df["FILING_DATE_D"] = pd.to_datetime(df["FILING_DATE"], errors="coerce", format="mixed")
    df["NET_ASSETS_N"] = pd.to_numeric(df["NET_ASSETS"], errors="coerce").fillna(0)
    df = df.sort_values(["SERIES_ID", "REPORT_DATE_D", "FILING_DATE_D"])
    df = df.groupby("SERIES_ID", as_index=False).tail(1)
    df["TICKER"] = df["SERIES_ID"].map(series_to_ticker)
    return df.sort_values("NET_ASSETS_N", ascending=False).reset_index(drop=True)


def load_holdings(reports: pd.DataFrame) -> pd.DataFrame:
    """선택된 보고서들의 보유내역 + 식별자(ISIN, 티커)."""
    parts = []
    for zp, grp in reports.groupby("ZIP"):
        accs = set(grp["ACCESSION_NUMBER"])
        log(f"[보유내역] {Path(zp).name}: 보고서 {len(accs)}건")
        with zipfile.ZipFile(zp) as zf:
            hold = []
            for ch in read_table(zf, "FUND_REPORTED_HOLDING",
                                 ["ACCESSION_NUMBER", "HOLDING_ID", "ISSUER_NAME", "ISSUER_TITLE",
                                  "ISSUER_CUSIP", "PERCENTAGE", "PAYOFF_PROFILE", "ASSET_CAT",
                                  "INVESTMENT_COUNTRY", "BALANCE", "UNIT", "CURRENCY_VALUE"], chunksize=CHUNK):
                hold.append(ch[ch["ACCESSION_NUMBER"].isin(accs)])
            h = pd.concat(hold, ignore_index=True)
            ids = set(h["HOLDING_ID"])
            idf = []
            for ch in read_table(zf, "IDENTIFIERS",
                                 ["HOLDING_ID", "IDENTIFIER_ISIN", "IDENTIFIER_TICKER"],
                                 chunksize=CHUNK):
                idf.append(ch[ch["HOLDING_ID"].isin(ids)])
            i = pd.concat(idf, ignore_index=True)
        i = i.replace("", pd.NA).groupby("HOLDING_ID", as_index=False).first()
        parts.append(h.merge(i, on="HOLDING_ID", how="left"))
    df = pd.concat(parts, ignore_index=True)
    df["PCT"] = pd.to_numeric(df["PERCENTAGE"], errors="coerce").fillna(0.0)
    for c in ["IDENTIFIER_ISIN", "IDENTIFIER_TICKER", "ISSUER_CUSIP", "ISSUER_NAME", "ISSUER_TITLE"]:
        df[c] = df[c].fillna("").astype(str).str.strip()
    return df


# ---------------------------------------------------------------- 종목 키 정하기
def resolve_keys(eq: pd.DataFrame) -> pd.Series:
    """주식 보유행마다 앱에서 쓸 종목 키를 정한다.
    국내 종목 → 6자리 코드, 미국 상장 → 티커, 그 밖의 해외 → ISIN."""
    # pandas의 NaN은 참으로 취급되므로 순수 파이썬 값 목록으로 바꿔서 다룬다
    isin = [str(x or "").upper() for x in eq["IDENTIFIER_ISIN"].tolist()]
    cusip = [str(x or "").upper() for x in eq["ISSUER_CUSIP"].tolist()]
    tick = [normalize_us_ticker(x if isinstance(x, str) else None) for x in eq["IDENTIFIER_TICKER"].tolist()]

    # 1) 같은 CUSIP에 다른 펀드가 적어 둔 티커를 빌려 쓴다
    known = {}
    for c, t, i in zip(cusip, tick, isin):
        if t and c and c not in ("000000000", "N/A") and (not i or is_us_isin(i)):
            known.setdefault(c, Counter())[t] += 1
    cusip_ticker = {c: cnt.most_common(1)[0][0] for c, cnt in known.items()}

    keys, need_figi = [], set()
    for c, t, i in zip(cusip, tick, isin):
        kr = kr_code_from_isin(i)
        us_listed = is_us_isin(i) or (not i and c[:1].isdigit())
        if kr:
            keys.append(kr)
        elif us_listed:
            t = t or cusip_ticker.get(c)
            if not t and c:
                need_figi.add(c)
            keys.append(t or f"CUSIP:{c}")
        else:
            keys.append(i or (f"CUSIP:{c}" if c else None))

    # 2) 남은 미국 종목은 OpenFIGI로 CUSIP → 티커
    if need_figi:
        mapped = figi.map_cusips(sorted(need_figi))
        keys = [mapped.get(k[6:], k) if k and k.startswith("CUSIP:") else k for k in keys]
    return pd.Series(keys, index=eq.index)


_SUFFIX = {"INC", "CO", "LTD", "PLC", "LLC", "CORP", "THE", "AND", "OF"}


def display_name(name: str, title: str) -> str:
    n = name or title
    if n.isupper():  # 'APPLE INC' → 'Apple Inc', 'AMD'·'IBM' 같은 약어는 그대로
        n = " ".join(w.capitalize() if len(w) > 3 or w in _SUFFIX else w for w in n.split())
    return n


# ---------------------------------------------------------------- 메인
def build(zips: list[Path], series_to_ticker: dict[str, str], top: int) -> None:
    cfg = read_json(CONFIG / "us_etfs.json", {})
    exclude = set(cfg.get("exclude", []))
    force = set(cfg.get("include", []))
    names = cfg.get("names", {})

    reports = latest_reports(zips, series_to_ticker)
    reports = reports[~reports["TICKER"].isin(exclude)]
    lev = reports["SERIES_NAME"].str.contains(LEVERAGED, na=False) & ~reports["TICKER"].isin(force)
    reports = reports[~lev]
    # 주식 비중을 보려고 여유 있게 읽는다
    cand = pd.concat([reports.head(top * 2), reports[reports["TICKER"].isin(force)]]).drop_duplicates("SERIES_ID")
    log(f"[후보] {len(cand)}개 펀드 (상위 {top}개 선정용)")

    h = load_holdings(cand)
    h["IS_EQ"] = h["ASSET_CAT"].isin(EQUITY_CATS) & (h["PAYOFF_PROFILE"].str.lower() != "short")
    eq_pct = h[h["IS_EQ"]].groupby("ACCESSION_NUMBER")["PCT"].sum()
    cand = cand.assign(EQUITY_PCT=cand["ACCESSION_NUMBER"].map(eq_pct).fillna(0.0))
    eligible = cand[cand["EQUITY_PCT"] >= MIN_EQUITY_PCT]
    keep = pd.concat([eligible.head(top), cand[cand["TICKER"].isin(force)]]).drop_duplicates("SERIES_ID")

    # 검토용 목록 (제외된 이유까지)
    rep = cand[["TICKER", "SERIES_NAME", "NET_ASSETS_N", "EQUITY_PCT", "REPORT_DATE"]].copy()
    rep["선정"] = rep["TICKER"].isin(keep["TICKER"])
    rep["제외 사유"] = ""
    rep.loc[~rep["선정"] & (rep["EQUITY_PCT"] < MIN_EQUITY_PCT), "제외 사유"] = "주식 비중 낮음(채권·원자재 등)"
    rep.loc[~rep["선정"] & (rep["제외 사유"] == ""), "제외 사유"] = f"순위 {top}위 밖"
    REPORTS.mkdir(parents=True, exist_ok=True)
    rep.rename(columns={"NET_ASSETS_N": "순자산(USD)", "EQUITY_PCT": "주식비중(%)",
                        "REPORT_DATE": "기준일"}).to_csv(
        REPORTS / "us_etf_list.csv", index=False, encoding="utf-8-sig")

    eq = h[h["IS_EQ"] & h["ACCESSION_NUMBER"].isin(keep["ACCESSION_NUMBER"])].copy()
    eq["KEY"] = resolve_keys(eq)
    eq = eq[eq["KEY"].notna()]
    eq["NAME"] = [display_name(n, t) for n, t in zip(eq["ISSUER_NAME"], eq["ISSUER_TITLE"])]

    # 국내 ETF 속 미국 종목(ISIN으로 적혀 있음)을 티커로 바꿀 때 쓰는 표
    isin_ticker = read_json(CACHE / "us_isin_ticker.json", {})
    for i, k in zip(eq["IDENTIFIER_ISIN"].str.upper(), eq["KEY"]):
        if is_us_isin(i) and normalize_us_ticker(k) == k:
            isin_ticker[i] = k
    write_json(CACHE / "us_isin_ticker.json", dict(sorted(isin_ticker.items())))

    # 국내 ETF PDF는 해외 종목을 주식 수로만 준다 → 비중 계산용 1주당 달러 가격(보고서 기준일, 펀드 간 중앙값)
    ns = h[h["IS_EQ"] & (h["UNIT"] == "NS")]
    px = pd.to_numeric(ns["CURRENCY_VALUE"], errors="coerce") / pd.to_numeric(ns["BALANCE"], errors="coerce")
    ok = (px > 0) & (px < float("inf")) & (ns["IDENTIFIER_ISIN"].str.len() == 12)
    prices = px[ok].groupby(ns.loc[ok, "IDENTIFIER_ISIN"].str.upper()).median()
    write_json(CACHE / "us_isin_price.json", {k: round(float(v), 4) for k, v in sorted(prices.items())})

    stock_names: dict[str, Counter] = defaultdict(Counter)
    holdings_dir = OUT / "holdings"
    holdings_dir.mkdir(parents=True, exist_ok=True)
    for old in holdings_dir.glob("*.json"):
        if old.stem not in set(keep["TICKER"]) and not old.stem.isdigit():
            old.unlink()  # 목록에서 빠진 미국 ETF 파일 정리 (국내 6자리 파일은 그대로)

    etf_index = []
    for _, r in keep.iterrows():
        rows = eq[eq["ACCESSION_NUMBER"] == r["ACCESSION_NUMBER"]]
        agg = rows.groupby("KEY").agg(weight=("PCT", "sum"), name=("NAME", "first"))
        agg = agg[agg["weight"] >= MIN_WEIGHT].sort_values("weight", ascending=False)
        for k, n in agg["name"].items():
            stock_names[k][n] += 1
        as_of = pd.to_datetime(r["REPORT_DATE"], errors="coerce", format="mixed")
        doc = {
            "ticker": r["TICKER"],
            "asOf": as_of.strftime("%Y-%m-%d") if pd.notna(as_of) else None,
            "source": "SEC Form N-PORT",
            "constituents": [
                {"ticker": k, "name": v["name"], "weight": round(float(v["weight"]), 4)}
                for k, v in agg.iterrows()
            ],
        }
        write_json(holdings_dir / f"{r['TICKER']}.json", doc)
        etf_index.append({
            "ticker": r["TICKER"],
            "name": names.get(r["TICKER"], r["SERIES_NAME"]),
            "kind": "ETF", "market": "US",
        })

    # 구성종목에 나온 미국 상장 주식 → 검색 목록
    stocks = []
    etf_set = {e["ticker"] for e in etf_index}
    for k, cnt in stock_names.items():
        if normalize_us_ticker(k) == k and k not in etf_set:
            stocks.append({"ticker": k, "name": cnt.most_common(1)[0][0], "kind": "STOCK", "market": "US"})
    # GOOGL·GOOG처럼 이름이 같은 종목은 앱에서 구분되게 티커를 붙인다
    dup = Counter(s["name"] for s in stocks)
    for s in stocks:
        if dup[s["name"]] > 1:
            s["name"] = f"{s['name']} ({s['ticker']})"
    stocks.sort(key=lambda s: s["ticker"])
    write_json(OUT / "instruments_us.json", etf_index + stocks)
    write_json(CACHE / "us_meta.json", {
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "datasets": [Path(z).name for z in zips],
        "etfCount": len(etf_index), "stockCount": len(stocks),
    }, compact=False)
    unresolved = int(eq["KEY"].astype(str).str.startswith("CUSIP:").sum())
    log(f"[완료] ETF {len(etf_index)}개, 미국 주식 {len(stocks)}개, 티커 못 찾은 행 {unresolved}개")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=300)
    ap.add_argument("--quarters", type=int, default=2)
    ap.add_argument("--local-zip", action="append", help="미리 받아 둔 데이터셋 ZIP (테스트용)")
    ap.add_argument("--tickers-json", help="company_tickers_mf.json 로컬 파일 (테스트용)")
    a = ap.parse_args(argv)

    if a.tickers_json:
        js = read_json(Path(a.tickers_json))
        idx = {f: i for i, f in enumerate(js["fields"])}
        s2t = {}
        for row in js["data"]:
            sym = str(row[idx["symbol"]]).upper()
            if re.fullmatch(r"[A-Z]{1,4}", sym):
                s2t.setdefault(row[idx["seriesId"]], sym)
    else:
        s2t = fetch_etf_candidates()
    log(f"[ETF 후보 series] {len(s2t)}개")

    if a.local_zip:
        build([Path(p) for p in a.local_zip], s2t, a.top)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            zips = download_datasets(a.quarters, Path(tmp))
            build(zips, s2t, a.top)


if __name__ == "__main__":
    sys.exit(main())
