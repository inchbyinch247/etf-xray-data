"""공통 도구: 경로, JSON 저장, 티커 정리."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "v1"          # GitHub Pages로 공개되는 폴더
CACHE = ROOT / "cache"              # 다음 실행 때 다시 쓰는 중간 결과
REPORTS = ROOT / "reports"          # 사람이 검토하는 목록(공개 안 됨)
CONFIG = ROOT / "config"

# SEC는 요청자 연락처가 담긴 User-Agent를 요구한다.
SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT", "etf-xray-data inchbyinch247@users.noreply.github.com"
)


def write_json(path: Path, data, compact: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        if compact:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


_EXCH_SUFFIX = re.compile(r"\s+(US|UN|UW|UQ|UA|UR|UP|UF|UV|EQUITY)(\s+EQUITY)?$")
_CLASS_SEP = re.compile(r"^([A-Z]{1,5})[ ./\-]([A-Z])$")
_VALID_US = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")


def normalize_us_ticker(raw: str | None) -> str | None:
    """'BRK/B', 'BRK B', 'brk-b', 'AAPL US Equity' → 'BRK.B', 'AAPL'. 이상하면 None."""
    if not raw:
        return None
    s = raw.strip().upper()
    s = _EXCH_SUFFIX.sub("", s).strip()
    m = _CLASS_SEP.match(s)
    if m:
        s = f"{m.group(1)}.{m.group(2)}"
    return s if _VALID_US.match(s) else None


def is_us_isin(isin: str | None) -> bool:
    return bool(isin) and isin.upper().startswith("US")


def is_kr_code(s: str) -> bool:
    """국내 종목코드인지 — 숫자로 시작하는 6자리(005930, 0000J0). 미국 티커는 영문으로 시작한다.
    isdigit()만 보면 영문이 섞인 새 코드(0000J0 등)를 미국 것으로 오인해 파일을 지운다."""
    return len(s) == 6 and s[:1].isdigit() and s.isalnum()


def kr_code_from_isin(isin: str | None) -> str | None:
    """KR7005930003 → 005930 (국내 종목코드). 국내 직접 보유분과 합쳐지게 한다.
    KR7은 주권·ETF 등 지분증권이고, 선물(KR4)·채권(KR6) 등은 None."""
    if isin and len(isin) == 12 and isin.upper().startswith("KR7"):
        return isin[3:9]
    return None
