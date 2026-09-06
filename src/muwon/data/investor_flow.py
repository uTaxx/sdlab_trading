"""외국인과 기관의 일별 순매매량을 받아 온다 (2026-09-06에 만듦).

## 왜 만들었나

주인이 "지금과 비슷했던 과거 20일 구간을 찾아 그때 좋았던 전략을 쓰자"고
정했다. 비슷한지를 판단하는 재료가 넷인데, 그중 셋(주가 추이, 변동성,
거래량)은 이미 일봉에 들어 있다. **외국인 매수 추이만 저장소 어디에도
없었다.** 이 파일이 그 하나를 채운다.

## 어디서 받나

네이버 금융의 종목별 외국인·기관 매매 표다.

    https://finance.naver.com/item/frgn.naver?code=005930&page=1

한 쪽에 20거래일씩 들어 있고, 쪽을 넘기면 과거로 간다. 2026-09-06에
005930으로 확인해 보니 100쪽이 2018년까지 갔다. 5년치는 70쪽쯤이다.

**한국거래소 자료 포털을 먼저 시험했고 안 됐다.** 종목 코드 조회
(`finder_stkisu`)는 열려 있는데 통계 조회(`MDCSTAT*`)는 전부 `LOGOUT`을
돌려준다. 브라우저 세션이 있어야 열리는 자리라, 날마다 도는 자리에 두기에는
잘 끊긴다. 증권사 API에도 투자자 매매동향이 있지만 최근 며칠치뿐이라
5년을 못 채운다.

## 못 받은 것을 0으로 채우지 않는다

외국인 순매매 0은 "그날 사지도 팔지도 않았다"는 뜻이다. "못 받았다"와
전혀 다르다. 섞으면 비슷한 시기를 찾는 계산이 실제보다 밋밋해진다.
그래서 못 받은 날은 표에 넣지 않고, 부른 쪽이 몇 종목을 못 받았는지 알 수
있도록 `받기결과`로 같이 돌려준다.
"""

from __future__ import annotations

import io
import os
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd
import requests
from loguru import logger
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

#: 네이버는 한 쪽에 20거래일을 준다. 이 값이 바뀌면 몇 쪽까지 받을지 계산도
#: 같이 틀어진다.
쪽당거래일 = 20

#: 한 종목에 최대 몇 쪽까지 넘길 것인가. 70쪽이면 5년이 넘는다. 상한을 두는
#: 것은 응답 모양이 바뀌었을 때 끝없이 도는 것을 막기 위해서다.
최대쪽 = 90

#: 이어서 부를 때 쉬는 시간(초). 남의 서버를 두드리는 자리라 간격을 둔다.
쉬는시간 = 0.35

#: 시세 캐시와 같은 자리에 둔다. 운영 DB에는 넣지 않는다. 그 파일은 워크플로
#: 마다 구글드라이브에서 받고 올리는데, 39종목 5년치면 5만 행이 넘는다.
기본캐시 = Path(os.environ.get("MUWON_FLOW_CACHE", ".cache/investor_flow.sqlite"))

_주소 = "https://finance.naver.com/item/frgn.naver"
_머리 = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://finance.naver.com/item/main.naver",
}

#: 네이버 표의 칸 이름. 표에 칸이 아홉 개다. 이름을 여기서 한 번 정하고
#: 아래에서는 이 이름만 쓴다.
_칸들 = [
    "날짜", "종가", "전일비", "등락률", "거래량",
    "기관순매매", "외국인순매매", "외국인보유주수", "외국인보유율",
]


@dataclass
class 받기결과:
    """무엇을 받았고 무엇을 못 받았는지.

    **못 받은 것을 조용히 넘기지 않으려고 둔다.** 표만 돌려주면 39종목 중
    다섯을 못 받아도 부른 쪽은 모른다."""

    자료: dict[str, pd.DataFrame] = field(default_factory=dict)
    못받은것: dict[str, str] = field(default_factory=dict)
    캐시로쓴것: list[str] = field(default_factory=list)

    @property
    def 다받았나(self) -> bool:
        return not self.못받은것

    def 한줄(self) -> str:
        받음 = len(self.자료)
        말 = f"외국인·기관 순매매 {받음}종목"
        if self.캐시로쓴것:
            말 += f" (캐시 {len(self.캐시로쓴것)}종목)"
        if self.못받은것:
            말 += f" · 못 받은 종목 {len(self.못받은것)}개: "
            말 += ", ".join(sorted(self.못받은것)[:5])
            if len(self.못받은것) > 5:
                말 += " 외"
        return 말


class 순매매캐시:
    """(종목, 날짜) 단위로 순매매를 보관한다.

    시세 캐시와 같은 생각이다. 같은 5년치를 실행마다 다시 받으면 한 번에
    2,700번을 두드리게 된다. 다만 여기서는 **받아 본 구간을 따로 적지
    않는다.** 네이버는 최근 쪽부터 주므로, 원하는 시작일보다 이른 날짜가
    캐시에 있으면 그 종목은 다 받은 것이다."""

    def __init__(self, path: Path | str = 기본캐시):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(f"sqlite:///{self.path}")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS investor_flow ("
                    "symbol TEXT NOT NULL, trade_date TEXT NOT NULL, "
                    "foreign_net REAL, institution_net REAL, "
                    "foreign_ratio REAL, close REAL, volume REAL, "
                    "PRIMARY KEY (symbol, trade_date))"
                )
            )
        self._session_factory = sessionmaker(bind=engine, class_=Session)

    def 가장이른날(self, symbol: str) -> date | None:
        with self._session_factory() as session:
            값 = session.execute(
                text("SELECT MIN(trade_date) FROM investor_flow WHERE symbol = :s"),
                {"s": symbol},
            ).scalar()
        return date.fromisoformat(값) if 값 else None

    def 읽기(self, symbol: str, 시작: date, 끝: date) -> pd.DataFrame:
        with self._session_factory() as session:
            줄들 = session.execute(
                text(
                    "SELECT trade_date, foreign_net, institution_net, foreign_ratio, "
                    "close, volume FROM investor_flow "
                    "WHERE symbol = :s AND trade_date >= :a AND trade_date <= :b "
                    "ORDER BY trade_date"
                ),
                {"s": symbol, "a": 시작.isoformat(), "b": 끝.isoformat()},
            ).all()
        표 = pd.DataFrame(
            줄들,
            columns=["날짜", "외국인순매매", "기관순매매", "외국인보유율", "종가", "거래량"],
        )
        if not 표.empty:
            표["날짜"] = pd.to_datetime(표["날짜"])
            표 = 표.set_index("날짜")
        return 표

    def 쓰기(self, symbol: str, 표: pd.DataFrame) -> int:
        if 표.empty:
            return 0
        줄들 = [
            {
                "s": symbol,
                "d": ㄱ.Index.date().isoformat() if hasattr(ㄱ.Index, "date") else str(ㄱ.Index),
                "f": float(ㄱ.외국인순매매),
                "i": float(ㄱ.기관순매매),
                "r": float(ㄱ.외국인보유율),
                "c": float(ㄱ.종가),
                "v": float(ㄱ.거래량),
            }
            for ㄱ in 표.itertuples()
        ]
        with self._session_factory() as session:
            session.execute(
                text(
                    "INSERT OR REPLACE INTO investor_flow "
                    "(symbol, trade_date, foreign_net, institution_net, "
                    " foreign_ratio, close, volume) "
                    "VALUES (:s, :d, :f, :i, :r, :c, :v)"
                ),
                줄들,
            )
            session.commit()
        return len(줄들)


def _쪽읽기(session: requests.Session, 코드: str, 쪽: int) -> pd.DataFrame:
    """한 쪽을 받아 표로 만든다. 모양이 다르면 예외를 낸다."""
    답 = session.get(
        _주소, params={"code": 코드, "page": 쪽}, headers=_머리, timeout=25
    )
    답.raise_for_status()
    답.encoding = "euc-kr"
    표들 = pd.read_html(io.StringIO(답.text))
    # 아홉 칸짜리 표가 우리가 찾는 것이다. 자리 번호로 집으면 네이버가 표를
    # 하나 더 넣는 날 조용히 다른 표를 읽는다.
    후보 = [ㅍ for ㅍ in 표들 if ㅍ.shape[1] == len(_칸들)]
    if not 후보:
        raise ValueError(f"{코드} {쪽}쪽: 아홉 칸짜리 표를 못 찾았습니다")
    표 = 후보[0].copy()
    표.columns = _칸들
    표 = 표.dropna(subset=["날짜"])
    if 표.empty:
        return 표
    표["날짜"] = pd.to_datetime(표["날짜"], format="%Y.%m.%d")
    표 = 표.set_index("날짜")[
        ["외국인순매매", "기관순매매", "외국인보유율", "종가", "거래량"]
    ]
    # 외국인 보유율은 "46.71%"처럼 온다. 나머지 넷은 숫자다. 백분율 기호를
    # 안 벗기면 숫자로 못 바꿔서 그 종목이 통째로 못 받은 것이 된다.
    보유율 = (
        표["외국인보유율"].astype(str).str.replace("%", "", regex=False).str.strip()
    )
    표 = 표.assign(외국인보유율=pd.to_numeric(보유율, errors="coerce"))
    return 표.astype(float)


def 한종목받기(
    코드: str,
    시작: date,
    끝: date,
    session: requests.Session | None = None,
    쉼: float = 쉬는시간,
) -> pd.DataFrame:
    """한 종목의 순매매를 시작일까지 거슬러 받는다.

    네이버는 최근 쪽부터 준다. 시작일보다 이른 날짜가 나오면 멈춘다."""
    session = session or requests.Session()
    모은것: list[pd.DataFrame] = []
    for 쪽 in range(1, 최대쪽 + 1):
        조각 = _쪽읽기(session, 코드, 쪽)
        if 조각.empty:
            break
        모은것.append(조각)
        if 조각.index.min().date() <= 시작:
            break
        if 쉼:
            time.sleep(쉼)
    if not 모은것:
        return pd.DataFrame()
    표 = pd.concat(모은것)
    표 = 표[~표.index.duplicated(keep="first")].sort_index()
    return 표.loc[str(시작):str(끝)]


def 받기(
    코드들: list[str],
    시작: date,
    끝: date,
    캐시: 순매매캐시 | None = None,
    쉼: float = 쉬는시간,
) -> 받기결과:
    """여러 종목을 받는다. 캐시에 있으면 안 받는다.

    **한 종목이 실패해도 나머지는 계속 받는다.** 그리고 실패한 것을 결과에
    적어 돌려준다. 예외로 통째로 죽으면 39종목 중 하나 때문에 아무것도
    못 쓰게 된다."""
    캐시 = 캐시 if 캐시 is not None else 순매매캐시()
    결과 = 받기결과()
    session = requests.Session()
    for 코드 in 코드들:
        이른날 = 캐시.가장이른날(코드)
        if 이른날 is not None and 이른날 <= 시작:
            표 = 캐시.읽기(코드, 시작, 끝)
            if not 표.empty:
                결과.자료[코드] = 표
                결과.캐시로쓴것.append(코드)
                continue
        try:
            표 = 한종목받기(코드, 시작, 끝, session=session, 쉼=쉼)
        except Exception as 탈:  # noqa: BLE001 (한 종목 때문에 전부 멈추면 안 된다)
            결과.못받은것[코드] = f"{type(탈).__name__}: {탈}"
            logger.warning(f"{코드} 외국인 순매매를 못 받았습니다: {탈}")
            continue
        if 표.empty:
            결과.못받은것[코드] = "받아 온 줄이 없습니다"
            continue
        캐시.쓰기(코드, 표)
        결과.자료[코드] = 표
    return 결과
