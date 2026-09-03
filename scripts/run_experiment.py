"""전략 실험 실행기: 가설을 손으로 스크립트 짜지 않고 실행해 본다.

시세는 한 번만 받아 모든 변형이 공유한다. 그래서 변형을 여러 개 실행해도
데이터 수집 시간은 한 번뿐이고, 무엇보다 **모든 변형이 정확히 같은 데이터를
본다**. 이게 어긋나면 비교 자체가 성립하지 않는다.

사용 예:
    # Factor를 하나씩 꺼 보고 기여도 측정 (인수인계서 28항)
    python scripts/run_experiment.py contribution --from-year 2021 --to-year 2025

    # 한 Factor의 가중치만 바꿔 가며
    python scripts/run_experiment.py sweep --factor relative_strength --weights 0,10,20,40

    # 등록된 전략들을 같은 조건에서 비교
    python scripts/run_experiment.py strategies --keys volume_surge_5d,factor_score_v1

    # 사는 쪽과 파는 쪽을 다른 전략으로 ('매수>매도', 여러 조합은 '|')
    python scripts/run_experiment.py split --keys "volume_surge_5d>ma_rsi_v1"
"""

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from muwon.analysis.entry_quality import format_entries, trace_entries
from muwon.analysis.experiment import (
    WARMUP_DAYS,
    blend_sleeves,
    correlation_matrix,
    daily_returns_by_strategy,
    factor_contribution,
    format_comparison,
    format_correlation,
    param_sweep,
    run_experiment,
    run_header,
    sleeve_curves,
    slice_for_year,
    slippage_sweep,
    take_profit_sweep,
    weight_sweep,
)
from muwon.analysis.holding_path import format_paths, trace
from muwon.analysis.intraday_stop import compare as compare_stops
from muwon.analysis.intraday_stop import format_comparison as format_stop_comparison
from muwon.analysis.market_data import load_histories
from muwon.analysis.overnight_split import format_split, split_overnight
from muwon.backtest.engine import BacktestEngine
from muwon.config import bootstrap_settings
from muwon.data.price_cache import PriceCache
from muwon.data.universe import UNIVERSE
from muwon.data.universe_builder import KIND_MARKET_CAP, active_universe
from muwon.data.yahoo_client import YahooFinanceDataSource
from muwon.db.session import make_session_factory
from muwon.risk.manager import RiskManager
from muwon.scoring.config import StrategyConfig
from muwon.settings.schema import RiskPolicy
from muwon.strategy.registry import build_strategies, build_strategy, list_definitions


def load_universe_histories(
    years: list[int], kind: str = KIND_MARKET_CAP, use_cache: bool = True
):
    session_factory = make_session_factory(bootstrap_settings.database_url)
    universe = active_universe(session_factory, list(UNIVERSE), kind=kind)
    return load_histories(
        YahooFinanceDataSource(),
        universe,
        date(min(years), 1, 1) - timedelta(days=WARMUP_DAYS),
        date(max(years), 12, 31),
        cache=PriceCache() if use_cache else None,
    )


def _짧은이름(key: str) -> str:
    """표 한 줄에 묶음 세 개가 들어가면 이름이 화면을 넘긴다.

    앞부분만 남기되 서로 구분은 되게: volume_surge_5d와 volume_surge_3d를
    같은 이름으로 줄이면 표를 읽을 수가 없다."""
    조각 = key.split("_")
    if len(조각) <= 2:
        return key
    return f"{조각[0][:4]}_{조각[-1]}"


def 조합파싱(글자: str) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    """split 모드의 --keys를 (사는키들, 파는키들) 목록으로 푼다.

    '매수키>매도키'가 조합 하나다. 여러 조합은 '|'로 나눈다. 한쪽에 전략을
    여럿 두려면 쉼표로 잇는다.

    잘못 적으면 여기서 바로 멈춘다. 조용히 한쪽을 버리면 표에는 조합이
    돌아간 것처럼 보이는데 실제로는 다른 것을 잰 상태가 된다."""
    쌍들: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    for 덩이 in (글자 or "").split("|"):
        덩이 = 덩이.strip()
        if not 덩이:
            continue
        if ">" not in 덩이:
            raise SystemExit(f"'{덩이}'에 '>'가 없습니다. '매수키>매도키' 꼴로 적으세요")
        사는, 파는 = 덩이.split(">", 1)
        사는키 = tuple(k.strip() for k in 사는.split(",") if k.strip())
        파는키 = tuple(k.strip() for k in 파는.split(",") if k.strip())
        if not 사는키 or not 파는키:
            raise SystemExit(f"'{덩이}'의 양쪽에 전략을 하나 이상 적으세요")
        쌍들.append((사는키, 파는키))
    if not 쌍들:
        raise SystemExit("--keys에 '매수키>매도키' 꼴로 조합을 지정하세요 (여러 개는 '|'로 구분)")
    return 쌍들


def main() -> None:
    parser = argparse.ArgumentParser(description="전략 실험 실행기")
    parser.add_argument(
        "mode",
        choices=[
            "contribution",
            "sweep",
            "param",
            "strategies",
            "correlation",
            "blend",
            "slippage",
            "takeprofit",
            "holding",
            "combo",
            "split",
            "entry",
            "intraday_stop",
            "overnight",
            "exit_timing",
            "fill_timing",
        ],
    )
    parser.add_argument(
        "--universe",
        choices=["market_cap", "volume"],
        default="market_cap",
        help="어느 유니버스로 실행할지. volume은 거래대금 상위(update_universe.py --kind volume)",
    )
    parser.add_argument(
        "--out",
        default="",
        help="결과를 남길 파일 경로. 로그는 만료되므로 나중에 비교하려면 필요하다.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="시세 캐시를 쓰지 않고 매번 새로 받는다",
    )
    parser.add_argument("--from-year", type=int, default=2021)
    parser.add_argument("--to-year", type=int, default=2025)
    parser.add_argument("--factor", default="relative_strength", help="sweep/param 대상 Factor")
    parser.add_argument("--weights", default="0,10,20,30,40", help="sweep에 쓸 가중치들")
    parser.add_argument("--keys", default="", help="strategies 모드에서 비교할 전략 키 (all이면 등록된 전부)")
    parser.add_argument(
        "--entry-at-open", action="store_true",
        help="매수를 다음 날 시가에 체결한다 (실거래 엔진이 실제로 하는 방식)",
    )
    parser.add_argument(
        "--exit-at-open", action="store_true",
        help="청산을 다음 날 시가에 체결한다 (실거래 엔진이 실제로 하는 방식)",
    )
    parser.add_argument("--param", default="uptrend_ma", help="param 모드에서 바꿀 파라미터")
    parser.add_argument("--values", default="0,120,200", help="param 모드에서 쓸 값들")
    parser.add_argument(
        "--base-params", default="", help="param 모드에서 함께 고정할 파라미터 (JSON)"
    )
    args = parser.parse_args()

    years = list(range(args.from_year, args.to_year + 1))
    histories = load_universe_histories(years, args.universe, use_cache=not args.no_cache)
    config = StrategyConfig()

    written: list[str] = []

    def emit(text: str) -> None:
        """화면과 파일에 같은 내용을 남긴다."""
        print(text)
        written.append(text)

    def save(extra: dict | None = None) -> None:
        if not args.out:
            return
        header = run_header(args.mode, args.universe, sorted(histories), years, extra)
        # 표는 자리를 맞춘 고정폭 텍스트라 코드 블록으로 감싸야 안 깨진다
        body = "```\n" + "\n".join(written) + "\n```\n"
        Path(args.out).write_text(header + body, encoding="utf-8")
        print(f"\n결과를 {args.out}에 남겼습니다.", file=sys.stderr)

    if args.mode == "contribution":
        emit("■ Factor 기여도: 하나씩 껐을 때 성과가 어떻게 변하는가")
        emit("  껐는데 성과가 그대로면 그 Factor는 가중치만 차지하고 있는 것이고,")
        emit("  껐더니 좋아지면 해를 끼치고 있는 것이다.\n")
        results = factor_contribution(config, histories, years)

    elif args.mode == "sweep":
        weights = [float(w) for w in args.weights.split(",")]
        emit(f"■ 가중치 스윕: {args.factor}의 비중만 바꾼다\n")
        results = weight_sweep(config, args.factor, weights, histories, years)

    elif args.mode == "param":
        values = [int(v) for v in args.values.split(",")]
        emit(f"■ 파라미터 스윕: {args.factor}.{args.param}만 바꾼다\n")
        base = json.loads(args.base_params) if args.base_params else {}
        results = param_sweep(
            config, args.factor, args.param, values, histories, years, base_params=base
        )

    elif args.mode == "slippage":
        keys = [k.strip() for k in args.keys.split(",") if k.strip()]
        if not keys:
            raise SystemExit("--keys에 전략을 지정하세요")
        rates = [float(v) / 100 for v in args.values.split(",")]
        emit("■ 슬리피지 민감도: 종가에 체결됐다는 가정을 얼마나 믿을 수 있는가")
        emit("  회전율이 높은 전략일수록 이 가정이 결과를 부풀린다.")
        emit("  0.1%에서 결론이 뒤집히면 그 결론은 원래 없던 것이다.\n")
        results = []
        for key in keys:
            results.extend(
                slippage_sweep(
                    key, (lambda k=key: build_strategy(k)), rates, histories, years
                )
            )
        emit(format_comparison(results, years))
        save({"슬리피지(%)": args.values})
        return

    elif args.mode == "takeprofit":
        keys = [k.strip() for k in args.keys.split(",") if k.strip()]
        if not keys:
            raise SystemExit("--keys에 전략을 지정하세요")
        levels = [float(v) / 100 for v in args.values.split(",")]
        emit("■ 익절선 민감도: 목표 수익률에서 파는 것이 나은가")
        emit("  지금은 익절이 아예 없다. 오르는 중이면 손절이나 보유 기간에")
        emit("  걸릴 때까지 그대로 들고 간다.")
        emit("  익절은 공짜가 아니다. 크게 벌 수 있는 구간을 자른다. 어디까지")
        emit("  좋아지고 어디부터 나빠지는지를 본다.\n")
        results = []
        for key in keys:
            results.extend(
                take_profit_sweep(
                    key, (lambda k=key: build_strategy(k)), levels, histories, years
                )
            )
        emit(format_comparison(results, years))
        save({"익절선(%)": args.values})
        return

    elif args.mode == "holding":
        keys = [k.strip() for k in args.keys.split(",") if k.strip()]
        if not keys:
            raise SystemExit("--keys에 전략을 지정하세요")
        emit("■ 보유 구간 되짚기: 익절선을 논하기 전에 볼 숫자\n")
        for key in keys:
            paths = []
            for year in years:
                sliced = slice_for_year(histories, year)
                if not sliced:
                    continue
                result = BacktestEngine(
                    strategy=build_strategy(key),
                    risk_manager=RiskManager(policy_provider=RiskPolicy),
                ).run(sliced, trade_from=date(year, 1, 1))
                paths.extend(trace(result.closed_trades, sliced))
            emit(format_paths(paths, key))
            emit("")
        save()
        return

    elif args.mode == "entry":
        keys = [k.strip() for k in args.keys.split(",") if k.strip()]
        if not keys:
            raise SystemExit("--keys에 전략을 지정하세요")
        emit("■ 진입 시점 되짚기: 산 날이 어떤 날이었나\n")
        for key in keys:
            samples = []
            for year in years:
                sliced = slice_for_year(histories, year)
                if not sliced:
                    continue
                result = BacktestEngine(
                    strategy=build_strategy(key),
                    risk_manager=RiskManager(policy_provider=RiskPolicy),
                ).run(sliced, trade_from=date(year, 1, 1))
                samples.extend(trace_entries(result.closed_trades, sliced))
            emit(format_entries(samples, key))
            emit("")
        save()
        return

    elif args.mode == "intraday_stop":
        keys = [k.strip() for k in args.keys.split(",") if k.strip()]
        if not keys:
            raise SystemExit("--keys에 전략을 지정하세요")
        emit("■ 장중 손절: 하루 한 번 종가로만 보는 지금 구조와 비교\n")
        정책 = RiskPolicy()
        for key in keys:
            비교 = []
            for year in years:
                sliced = slice_for_year(histories, year)
                if not sliced:
                    continue
                result = BacktestEngine(
                    strategy=build_strategy(key),
                    risk_manager=RiskManager(policy_provider=lambda p=정책: p),
                ).run(sliced, trade_from=date(year, 1, 1))
                비교.extend(compare_stops(result.closed_trades, sliced, 정책.stop_loss_pct))
            emit(format_stop_comparison(비교, key))
            emit("")
        save()
        return

    elif args.mode == "fill_timing":
        keys = [k.strip() for k in args.keys.split(",") if k.strip()]
        if not keys:
            raise SystemExit("--keys에 전략을 지정하세요")
        emit("■ 백테스트가 실거래와 같은 일을 하고 있나\n")
        emit("  실거래 엔진은 **어제까지의 완성된 일봉으로 판단하고 오늘 시가에 주문**한다.")
        emit("  그런데 백테스트는 그날 종가를 보고 그 종가에 체결한다고 계산해 왔다.")
        emit("  즉 지금까지의 5년 성적은 실거래와 다른 규칙의 성적이다.\n")
        emit("  아래 세 줄 중 **맨 아래가 실거래와 같은 규칙**이다. 맨 위(지금 백테스트)와의")
        emit("  차이가 곧 '지금 숫자가 얼마나 부풀어 있나'다.\n")
        # (매수, 매도): 실거래에 가까워지는 순서로 세운다
        방식들 = (
            (False, False, "①종가매수·종가매도(지금)"),
            (False, True, "②종가매수·시가매도"),
            (True, True, "③시가매수·시가매도(실거래)"),
        )
        results = []
        for key in keys:
            for 시가진입, 시가청산, 이름 in 방식들:
                results.append(
                    run_experiment(
                        f"{_짧은이름(key)} {이름}",
                        lambda k=key: build_strategy(k),
                        histories,
                        years,
                        entry_at_open=시가진입,
                        exit_at_open=시가청산,
                    )
                )
        emit(format_comparison(results, years))
        emit("")
        emit("읽는 법: ①→③으로 갈수록 실거래에 가깝다. ③이 ①보다 나쁘면 그만큼")
        emit("지금까지의 숫자가 부풀어 있었다는 뜻이고, **그 차이만큼 기대를 낮춰야 한다.**")
        emit("②는 청산만 옮긴 것이라 ③으로 가는 중간 단계다. ②와 ③의 차이가")
        emit("곧 '매수를 하루 늦추면 잃는 밤'의 크기다.")
        save()
        return

    elif args.mode == "exit_timing":
        keys = [k.strip() for k in args.keys.split(",") if k.strip()]
        if not keys:
            raise SystemExit("--keys에 전략을 지정하세요")
        emit("■ 언제 파는가. 판단한 그날 종가 vs 다음 날 시가\n")
        emit("  지금은 그날 종가를 보고 판단해서 그 종가에 판다고 계산한다. 그런데")
        emit("  실거래는 장 마감 뒤에 정하고 다음 날 아침에 주문을 낸다.")
        emit("  게다가 수익의 70~92%가 밤사이에 났다(설계안 §26): 종가에 파는")
        emit("  지금 방식은 마지막 밤을 버리고 있다.\n")
        results = []
        for key in keys:
            for 시가청산, 이름 in ((False, "종가(지금)"), (True, "시가(다음날)")):
                results.append(
                    run_experiment(
                        f"{_짧은이름(key)} · {이름}",
                        lambda k=key: build_strategy(k),
                        histories,
                        years,
                        exit_at_open=시가청산,
                    )
                )
        emit(format_comparison(results, years))
        emit("")
        emit("읽는 법: **1순위는 평균이 아니라 최악의 해다.** 평균이 올라가도 최악의")
        emit("해가 더 나빠졌으면 채택하지 않는다. 거래 수가 크게 달라졌다면 그것도")
        emit("봐야 한다. 청산이 하루 밀리면 다음 진입도 하루씩 밀린다.")
        save()
        return

    elif args.mode == "overnight":
        keys = [k.strip() for k in args.keys.split(",") if k.strip()]
        if not keys:
            raise SystemExit("--keys에 전략을 지정하세요")
        emit("■ 번 돈은 밤사이(오버나이트)에 났나, 낮(장중)에 났나\n")
        for key in keys:
            나눈것 = []
            for year in years:
                sliced = slice_for_year(histories, year)
                if not sliced:
                    continue
                result = BacktestEngine(
                    strategy=build_strategy(key),
                    risk_manager=RiskManager(policy_provider=RiskPolicy),
                ).run(sliced, trade_from=date(year, 1, 1))
                나눈것.extend(split_overnight(result.closed_trades, sliced))
            emit(format_split(나눈것, key))
            emit("")
        save()
        return

    elif args.mode == "combo":
        # 조합을 하나씩 따로 실행하면 결과가 실행마다 흩어져 비교가 안 된다.
        # '|'로 여러 묶음을 한 번에 받아 같은 표에 세운다.
        묶음들 = [
            [k.strip() for k in group.split(",") if k.strip()]
            for group in args.keys.split("|")
            if group.strip()
        ]
        if not 묶음들 or any(len(g) < 2 for g in 묶음들):
            raise SystemExit("--keys의 각 묶음에 전략을 둘 이상 지정하세요 (묶음 구분은 '|')")
        keys = sorted({k for group in 묶음들 for k in group})
        emit("■ 전략 묶기: 여럿을 같이 걸면 나아지는가")
        emit("  OR = 하나라도 신호나면 산다 / AND = 모두 신호를 내야 산다.")
        emit("  파는 쪽은 어느 쪽이든 '하나라도'다. 모두 동의해야 팔게 하면")
        emit("  한 전략이 침묵하는 동안 손실 종목을 계속 들고 있게 된다.")
        emit("")
        emit("  각각 혼자 실행한 결과를 함께 두는 것이 요점이다. 묶음이 좋아 보여도")
        emit("  가장 좋은 하나보다 못하면 묶을 이유가 없다.\n")
        results = [
            run_experiment(key, lambda k=key: build_strategy(k), histories, years) for key in keys
        ]
        for group in 묶음들:
            for mode in ("OR", "AND"):
                results.append(
                    run_experiment(
                        f"[{mode}] {'+'.join(_짧은이름(k) for k in group)}",
                        lambda k=tuple(group), m=mode: build_strategies(k, m),
                        histories,
                        years,
                    )
                )
        emit(format_comparison(results, years))
        save({"묶음": args.keys})
        return

    elif args.mode == "split":
        # 사는 쪽과 파는 쪽을 다른 전략으로 굴린다.
        #
        # '매수키>매도키'가 조합 하나다. 여러 조합은 '|'로 나눈다. 한쪽에
        # 전략을 여럿 두려면 쉼표로 잇는다. 그쪽은 OR로 묶인다.
        #
        # 각 전략을 혼자 실행한 결과를 같은 표에 함께 세운다. 이게 요점이다.
        # 섞은 것이 좋아 보여도 그냥 하나 쓴 것보다 못하면 섞을 이유가 없다.
        쌍들 = 조합파싱(args.keys)

        어떤방식 = (
            "실거래와 같은 규칙: 어제 판단 → 오늘 시가 매수·매도"
            if args.entry_at_open and args.exit_at_open
            else f"매수 {'시가' if args.entry_at_open else '종가'} · "
                 f"매도 {'시가' if args.exit_at_open else '종가'}"
        )
        emit("■ 매수와 매도를 따로 걸었을 때: 섞으면 나아지는가")
        emit(f"  체결 방식: {어떤방식}")
        emit("  사는 신호는 왼쪽 전략에서만, 파는 신호는 오른쪽 전략에서만 나온다.")
        emit("  보유 기간 상한도 파는 쪽 것을 쓴다. 그것도 청산 규칙이라 그렇다.")
        emit("")
        emit("  맨 위 줄들은 각 전략을 통째로 혼자 실행한 결과다. 섞은 줄이")
        emit("  이것들보다 못하면 섞을 이유가 없다.")
        emit("")
        emit("  판단 기준은 평균이 아니라 '최악' 칸이다. 가장 나빴던 해의 수익률.")
        emit("  평균이 높아도 한 해에 크게 잃으면 대부분 중간에 그만둔다.\n")

        # 혼자 실행한 결과를 먼저 세운다. 나온 순서를 지킨다. format_comparison이
        # 첫 줄을 기준선으로 삼아 '최악 몇 %p' 차이를 붙인다.
        홀로: list[str] = []
        for 사는키, 파는키 in 쌍들:
            for k in list(사는키) + list(파는키):
                if k not in 홀로:
                    홀로.append(k)
        results = [
            run_experiment(
                key, lambda k=key: build_strategy(k), histories, years,
                entry_at_open=args.entry_at_open, exit_at_open=args.exit_at_open,
            )
            for key in 홀로
        ]

        경고들: list[str] = []
        for 사는키, 파는키 in 쌍들:
            이름 = (
                f"{'+'.join(_짧은이름(k) for k in 사는키)}"
                f">{'+'.join(_짧은이름(k) for k in 파는키)}"
            )
            # 나가는 길이 좁은 조합은 숫자만 보고 고르면 안 된다. 표 아래에
            # 이유를 적는다. 백테스트는 5년이 끝나면 강제로 정리하므로
            # "영영 안 팔린다"가 전략 평가 결과에는 안 보인다.
            경고 = getattr(
                build_strategies(사는키, "OR", 파는키), "왜조심해야하나", ""
            )
            if 경고:
                경고들.append(f"  ⚠ {이름}: {경고}")
            results.append(
                run_experiment(
                    이름,
                    lambda b=사는키, s=파는키: build_strategies(b, "OR", s),
                    histories,
                    years,
                    entry_at_open=args.entry_at_open,
                    exit_at_open=args.exit_at_open,
                )
            )
        emit(format_comparison(results, years))
        if 경고들:
            emit("")
            emit("나가는 길이 좁은 조합")
            for 줄 in 경고들:
                emit(줄)
            emit("  백테스트는 마지막 날 전부 정리하므로 이 문제가 표에는 안 보입니다.")
            emit("  실거래에서는 값이 안 빠지면 손절 말고는 파는 길이 없습니다.")
        save({"조합": args.keys})
        return

    elif args.mode == "blend":
        pairs = [k.strip() for k in args.keys.split(",") if k.strip()]
        shares = [float(w) for w in args.weights.split(",")]
        if len(pairs) != len(shares):
            raise SystemExit("--keys와 --weights의 개수가 같아야 합니다")
        emit("■ 갈래 배분: 나눠서 굴렸을 때 합친 계좌가 어떻게 되는가")
        emit("  비중은 연 단위로만 맞춘다. 연중에 갈래끼리 자금을 옮기지 않는다.")
        emit("  매일 맞추면 깨진 쪽에 계속 돈을 부어 주는 셈이라 결과가 부풀려진다.\n")
        curves, trades = sleeve_curves(
            {key: (lambda k=key: build_strategy(k)) for key in pairs}, histories, years
        )
        results = [blend_sleeves(curves, {key: 100.0}, years, trades) for key in pairs]
        results.append(
            blend_sleeves(curves, dict(zip(pairs, shares, strict=True)), years, trades)
        )
        emit(format_comparison(results, years))
        save()
        return

    elif args.mode == "correlation":
        keys = [k.strip() for k in args.keys.split(",") if k.strip()]
        if not keys:
            raise SystemExit("--keys에 비교할 전략을 지정하세요")
        emit("■ 전략 간 상관: 자금을 갈래로 나눌 가치가 있는가")
        emit("  같은 날 같이 움직이면 나눠도 분산 효과가 없다. 낮을수록 좋다.\n")
        series, exposure = daily_returns_by_strategy(
            {key: (lambda k=key: build_strategy(k)) for key in keys}, histories, years
        )
        emit(format_correlation(correlation_matrix(series), exposure))
        save()
        return

    else:
        # `all`이면 등록된 전략 전부. 전략 평가 결과를 통째로 다시 잴 때 쓴다.
        # 키를 손으로 나열하면 하나 빠뜨려도 표에는 아무 표시가 안 남는다.
        if args.keys.strip().lower() == "all":
            keys = [d.key for d in list_definitions()]
        else:
            keys = [k.strip() for k in args.keys.split(",") if k.strip()]
        if not keys:
            raise SystemExit("--keys에 비교할 전략을 지정하세요 (또는 all)")
        어떤방식 = (
            "실거래와 같은 규칙: 어제 판단 → 오늘 시가 매수·매도"
            if args.entry_at_open and args.exit_at_open
            else f"매수 {'시가' if args.entry_at_open else '종가'} · "
                 f"매도 {'시가' if args.exit_at_open else '종가'}"
        )
        emit(f"■ 전략 비교: 같은 데이터·같은 예열 조건 ({len(keys)}개)")
        emit(f"  체결 방식: {어떤방식}\n")
        results = [
            run_experiment(
                key, lambda k=key: build_strategy(k), histories, years,
                entry_at_open=args.entry_at_open, exit_at_open=args.exit_at_open,
            )
            for key in keys
        ]

    emit(format_comparison(results, years))
    save()


if __name__ == "__main__":
    main()
