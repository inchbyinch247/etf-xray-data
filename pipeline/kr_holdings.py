"""국내 ETF 구성종목 수집 (한국거래소 정보데이터시스템, 각 운용사 PDF).

하는 일
1. ETF 전종목 시세에서 순자산총액을 읽고, ETF 기본정보에서 자산 분류를 읽는다.
2. 순자산 1,000억 원 이상, 자산 분류 '주식', 레버리지·인버스·선물이 아닌 ETF만 고른다.
3. ETF마다 PDF(구성종목)를 받아 주식 비중이 60% 이상인 것만 저장한다.
   (스왑으로 지수를 따라가는 합성 ETF는 여기서 걸러진다)
4. docs/v1/holdings/<6자리코드>.json, docs/v1/instruments_kr.json을 만든다.

실행: python -m pipeline.kr_holdings   (옵션: --min-aum 100000000000 --date 20261015)
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
import time

import requests

from .common import CACHE, CONFIG, OUT, REPORTS, is_us_isin, kr_code_from_isin, read_json, write_json

URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
HEADERS = {
    # 2026-02부터 KRX는 로그인 세션 없이는 'LOGOUT'만 돌려준다. 로그인 흐름은 브라우저 UA를 기대한다
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd",
}
BLD_PRICE = "dbms/MDC/STAT/standard/MDCSTAT04301"   # ETF 전종목 시세 (순자산총액 포함)
BLD_INFO = "dbms/MDC/STAT/standard/MDCSTAT04601"    # ETF 전종목 기본정보 (자산 분류)
BLD_PDF = "dbms/MDC/STAT/standard/MDCSTAT05001"     # ETF PDF (구성종목)

MIN_AUM = 100_000_000_000          # 1,000억 원
MIN_EQUITY_PCT = 60.0
EXCLUDE_NAME = re.compile(r"(레버리지|인버스|2X|선물|합성)", re.I)
NON_STOCK_NAME = re.compile(r"(현금|예금|선물|스왑|SWAP|FUTURE|CASH|원화|달러|미수|미지급|설정)", re.I)

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def log(*a):
    print(*a, flush=True)


def login() -> None:
    """KRX_ID/KRX_PW로 data.krx.co.kr에 로그인해 SESSION에 세션 쿠키를 심는다 (pykrx #276과 같은 흐름)."""
    uid, pw = os.environ.get("KRX_ID"), os.environ.get("KRX_PW")
    if not uid or not pw:
        raise SystemExit("KRX_ID, KRX_PW 환경변수가 필요해요 (GitHub: Settings → Secrets and variables → Actions)")
    page = "https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001.cmd"
    SESSION.get(page, timeout=30)
    SESSION.get("https://data.krx.co.kr/contents/MDC/COMS/client/view/login.jsp?site=mdc", headers={"Referer": page}, timeout=30)
    form = {"mbrNm": "", "telNo": "", "di": "", "certType": "", "mbrId": uid, "pw": pw}
    url = "https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001D1.cmd"
    code = SESSION.post(url, data=form, headers={"Referer": page}, timeout=30).json().get("_error_code", "")
    if code == "CD011":  # 다른 곳에 로그인돼 있음 → 기존 세션을 끊고 로그인
        form["skipDup"] = "Y"
        code = SESSION.post(url, data=form, headers={"Referer": page}, timeout=30).json().get("_error_code", "")
    if code != "CD001":
        raise SystemExit(f"KRX 로그인 실패 (코드 {code or '없음'}). 아이디·비밀번호를 확인해 주세요")
    log("[KRX] 로그인 성공")


def post(bld: str, **params) -> list[dict]:
    data = {"bld": bld, "locale": "ko_KR", "share": "1", "money": "1", "csvxls_isNo": "false", **params}
    for attempt in range(4):
        try:
            r = SESSION.post(URL, data=data, timeout=60)
            r.raise_for_status()
            js = r.json()
            return js.get("output") or js.get("OutBlock_1") or []
        except (requests.RequestException, ValueError) as e:
            log(f"  KRX 요청 재시도 {attempt + 1}: {e}")
            time.sleep(5 * (attempt + 1))
    raise SystemExit("KRX 정보데이터시스템에 연결하지 못했어요")


def num(v) -> float:
    try:
        return float(str(v).replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def pick(row: dict, *keys, default=""):
    for k in keys:
        if row.get(k) not in (None, "", "-"):
            return row[k]
    return default


def latest_trade_date(start: dt.date) -> tuple[str, list[dict]]:
    d = start
    for _ in range(10):
        ds = d.strftime("%Y%m%d")
        rows = post(BLD_PRICE, trdDd=ds)
        if rows and any(num(pick(r, "INVSTASST_NETASST_TOTAMT", "NETASST_TOTAMT")) > 0 for r in rows):
            return ds, rows
        d -= dt.timedelta(days=1)
    raise SystemExit("최근 10일 안에 ETF 시세 자료가 없어요")


def constituent_key(code: str, isin_ticker: dict[str, str]) -> str | None:
    """PDF의 구성종목코드를 앱의 종목 키로 바꾼다. 주식이 아니면 None."""
    c = (code or "").strip().upper()
    if not c:
        return None
    if len(c) == 12 and c[:2].isalpha() and c[2:].isalnum():   # ISIN
        if c.startswith("KR"):
            return kr_code_from_isin(c)                          # KR7만 주식, 나머지(선물 등)는 None
        if is_us_isin(c):
            return isin_ticker.get(c, c)                         # 미국 종목은 티커로
        return c                                                 # 기타 해외 주식
    if len(c) == 6 and any(ch.isdigit() for ch in c):
        return c                                                 # 국내 종목코드 (005930, 0126Z0)
    m = re.fullmatch(r"([A-Z]{1,5})(?:[ ./]([A-Z]))?(?:\s+(?:US|UN|UW|UQ)(?:\s+EQUITY)?)?", c)
    if m:
        return m.group(1) + (f".{m.group(2)}" if m.group(2) else "")
    return None


def build(min_aum: float, start: dt.date) -> None:
    cfg = read_json(CONFIG / "kr_etfs.json", {})
    exclude, force = set(cfg.get("exclude", [])), set(cfg.get("include", []))
    isin_ticker = read_json(CACHE / "us_isin_ticker.json", {})
    usd_price = read_json(CACHE / "us_isin_price.json", {})

    trd_dd, prices = latest_trade_date(start)
    log(f"[기준일] {trd_dd}, ETF {len(prices)}개")
    info = {pick(r, "ISU_SRT_CD"): r for r in post(BLD_INFO, trdDd=trd_dd)}

    cands, report = [], []
    for r in prices:
        code = pick(r, "ISU_SRT_CD")
        name = pick(r, "ISU_ABBRV", "ISU_NM")
        aum = num(pick(r, "INVSTASST_NETASST_TOTAMT", "NETASST_TOTAMT"))
        meta = info.get(code, {})
        asset = pick(meta, "IDX_ASST_CLSS_NM", default="")
        reason = ""
        if code in exclude:
            reason = "설정 파일에서 제외"
        elif code not in force:
            if aum < min_aum:
                continue                                 # 작은 ETF는 목록에서도 생략
            if asset and asset != "주식":
                reason = f"자산 분류 '{asset}'"
            elif EXCLUDE_NAME.search(name):
                reason = "레버리지·인버스·선물·합성"
        isin = pick(meta, "ISU_CD", default=pick(r, "ISU_CD"))
        row = {"code": code, "name": name, "aum": aum, "asset": asset, "isin": isin,
               "issuer": pick(meta, "COM_ABBRV", default="")}
        report.append({**row, "reason": reason})
        if not reason:
            cands.append(row)
    cands.sort(key=lambda x: -x["aum"])
    log(f"[후보] {len(cands)}개 (순자산 {min_aum / 1e8:,.0f}억 원 이상 주식형)")

    holdings_dir = OUT / "holdings"
    holdings_dir.mkdir(parents=True, exist_ok=True)
    as_of = f"{trd_dd[:4]}-{trd_dd[4:6]}-{trd_dd[6:]}"
    instruments, kept, stock_names = [], set(), {}
    by_code = {x["code"]: x for x in report}

    for i, etf in enumerate(cands, 1):
        rows = post(BLD_PDF, isuCd=etf["isin"], trdDd=trd_dd)
        time.sleep(0.4)
        parts, foreign, total_val = [], [], 0.0
        for p in rows:
            nm = pick(p, "COMPST_ISU_NM")
            val = num(pick(p, "VALU_AMT", "COMPST_AMT"))
            total_val += val
            if NON_STOCK_NAME.search(nm):
                continue                                         # 현금·선물·스왑 등
            code = pick(p, "COMPST_ISU_CD").strip().upper()
            key = constituent_key(code, isin_ticker)
            if key:
                parts.append((key, nm, num(pick(p, "COMPST_RTO")), val))
                if len(code) == 12 and not code.startswith("KR"):
                    foreign.append((key, nm, code, num(pick(p, "COMPST_ISU_CU1_SHRS"))))
        if not any(w for _, _, w, _ in parts) and total_val > 0:     # 비중이 비어 있으면 금액으로 계산
            parts = [(k, n, v / total_val * 100, v) for k, n, _, v in parts]
        agg: dict[str, list] = {}
        for k, n, w, _ in parts:
            a = agg.setdefault(k, [n, 0.0])
            a[1] += w
        equity = sum(w for _, w in agg.values())
        estimated = False
        if equity < MIN_EQUITY_PCT and foreign:
            # 해외 주식은 PDF에 비중·금액 없이 주식 수만 있다 → 주식 수 × 미국 공시 가격으로 비중 추정
            # shortcut: 가격이 SEC 분기 말 기준이라 그 뒤 주가 변동만큼 비중이 어긋남. 무료 일별 시세를 구하면 교체
            priced = [(k, n, sh * usd_price[c]) for k, n, c, sh in foreign if c in usd_price and sh > 0]
            tot = sum(v for *_, v in priced)
            if tot > 0 and len(priced) >= 0.8 * len(foreign):
                agg = {}
                for k, n, v in priced:
                    a = agg.setdefault(k, [n, 0.0])
                    a[1] += v / tot * 100
                equity, estimated = 100.0, True
        if equity < MIN_EQUITY_PCT and etf["code"] not in force:
            by_code[etf["code"]]["reason"] = f"주식 비중 {equity:.0f}% (스왑·현금 위주)"
            continue
        cons = sorted(({"ticker": k, "name": n, "weight": round(w, 4)} for k, (n, w) in agg.items() if w >= 0.001),
                      key=lambda c: -c["weight"])
        write_json(holdings_dir / f"{etf['code']}.json", {
            "ticker": etf["code"], "asOf": as_of, "source": f"{etf['issuer'] or '운용사'} 공시(PDF)" + (" · 비중은 SEC 공시 가격으로 추정" if estimated else ""),
            "constituents": cons,
        })
        kept.add(etf["code"])
        instruments.append({"ticker": etf["code"], "name": etf["name"], "kind": "ETF", "market": "KR"})
        for c in cons:
            if re.fullmatch(r"\d{6}|\d{5}[A-Z]", c["ticker"]):
                stock_names.setdefault(c["ticker"], c["name"])
        if i % 50 == 0:
            log(f"  {i}/{len(cands)}")

    for old in holdings_dir.glob("*.json"):
        if old.stem.isdigit() and old.stem not in kept:
            old.unlink()
    kr_etfs = {x["code"] for x in report}
    instruments += [{"ticker": k, "name": v, "kind": "STOCK", "market": "KR"}
                    for k, v in sorted(stock_names.items()) if k not in kr_etfs]
    write_json(OUT / "instruments_kr.json", instruments)

    REPORTS.mkdir(parents=True, exist_ok=True)
    with open(REPORTS / "kr_etf_list.csv", "w", encoding="utf-8-sig") as f:
        f.write("코드,종목명,운용사,순자산(억원),자산분류,선정,제외 사유\n")
        for x in sorted(report, key=lambda x: -x["aum"]):
            f.write(f"{x['code']},\"{x['name']}\",{x['issuer']},{x['aum'] / 1e8:.0f},{x['asset']},"
                    f"{'O' if x['code'] in kept else ''},{x['reason']}\n")
    write_json(CACHE / "kr_meta.json", {
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "tradeDate": trd_dd, "etfCount": len(kept),
        "stockCount": len(instruments) - len(kept),
    }, compact=False)
    log(f"[완료] 국내 ETF {len(kept)}개, 국내 주식 {len(instruments) - len(kept)}개")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-aum", type=float, default=MIN_AUM)
    ap.add_argument("--date", help="YYYYMMDD (기본: 오늘부터 거슬러 올라가며 자료 있는 날)")
    a = ap.parse_args(argv)
    kst = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()
    start = dt.datetime.strptime(a.date, "%Y%m%d").date() if a.date else kst
    login()
    build(a.min_aum, start)


if __name__ == "__main__":
    sys.exit(main())
