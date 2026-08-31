# 상태 DB 스키마 (SQLAlchemy 모델에서 뽑음)

파일 하나짜리 SQLite다. 구글드라이브에 `muwon.db`로 있다.

## app_settings

| 칸 | 형 | NULL | 키 |
|---|---|---|---|
| key | VARCHAR(100) | 아니오 | PK |
| value | TEXT | 아니오 |  |
| is_secret | BOOLEAN | 아니오 |  |
| updated_at | DATETIME | 아니오 |  |

## app_settings_history

| 칸 | 형 | NULL | 키 |
|---|---|---|---|
| id | INTEGER | 아니오 | PK |
| key | VARCHAR(100) | 아니오 |  |
| old_value | TEXT | 예 |  |
| new_value | TEXT | 아니오 |  |
| is_secret | BOOLEAN | 아니오 |  |
| changed_at | DATETIME | 아니오 |  |

## backtest_runs

| 칸 | 형 | NULL | 키 |
|---|---|---|---|
| id | INTEGER | 아니오 | PK |
| strategy_key | VARCHAR(50) | 아니오 |  |
| params_json | TEXT | 아니오 |  |
| period_start | DATE | 아니오 |  |
| period_end | DATE | 아니오 |  |
| total_return_pct | FLOAT | 아니오 |  |
| max_drawdown_pct | FLOAT | 아니오 |  |
| win_rate_pct | FLOAT | 아니오 |  |
| num_trades | INTEGER | 아니오 |  |
| notes | TEXT | 아니오 |  |
| created_at | DATETIME | 아니오 |  |

## engine_state

| 칸 | 형 | NULL | 키 |
|---|---|---|---|
| key | VARCHAR(50) | 아니오 | PK |
| value | TEXT | 아니오 |  |

## orders

| 칸 | 형 | NULL | 키 |
|---|---|---|---|
| id | INTEGER | 아니오 | PK |
| symbol | VARCHAR(10) | 아니오 |  |
| side | VARCHAR(4) | 아니오 |  |
| quantity | INTEGER | 아니오 |  |
| price | FLOAT | 아니오 |  |
| is_paper | BOOLEAN | 아니오 |  |
| kis_order_id | VARCHAR(50) | 아니오 |  |
| reason | VARCHAR(100) | 아니오 |  |
| reference_price | FLOAT | 예 |  |
| fill_confirmed | BOOLEAN | 예 |  |
| created_at | DATETIME | 아니오 |  |

## positions

| 칸 | 형 | NULL | 키 |
|---|---|---|---|
| symbol | VARCHAR(10) | 아니오 | PK |
| quantity | INTEGER | 아니오 |  |
| entry_price | FLOAT | 아니오 |  |
| entry_date | DATE | 아니오 |  |
| entered_at | DATETIME | 아니오 |  |
| entry_reason | VARCHAR(100) | 아니오 |  |
| strategy_key | VARCHAR(50) | 아니오 |  |

## price_bars

| 칸 | 형 | NULL | 키 |
|---|---|---|---|
| id | INTEGER | 아니오 | PK |
| symbol | VARCHAR(10) | 아니오 |  |
| trade_date | DATE | 아니오 |  |
| open | FLOAT | 아니오 |  |
| high | FLOAT | 아니오 |  |
| low | FLOAT | 아니오 |  |
| close | FLOAT | 아니오 |  |
| volume | INTEGER | 아니오 |  |

## run_logs

| 칸 | 형 | NULL | 키 |
|---|---|---|---|
| id | INTEGER | 아니오 | PK |
| run_date | DATE | 예 |  |
| strategy_key | VARCHAR(50) | 아니오 |  |
| universe_size | INTEGER | 아니오 |  |
| checked_symbols | INTEGER | 아니오 |  |
| buy_signals | INTEGER | 아니오 |  |
| sell_signals | INTEGER | 아니오 |  |
| orders | INTEGER | 아니오 |  |
| rejections | TEXT | 아니오 |  |
| cash | FLOAT | 아니오 |  |
| equity | FLOAT | 아니오 |  |
| created_at | DATETIME | 아니오 |  |

## signals

| 칸 | 형 | NULL | 키 |
|---|---|---|---|
| id | INTEGER | 아니오 | PK |
| symbol | VARCHAR(10) | 아니오 |  |
| trade_date | DATE | 아니오 |  |
| strategy_name | VARCHAR(50) | 아니오 |  |
| signal_type | VARCHAR(10) | 아니오 |  |
| score | FLOAT | 아니오 |  |
| created_at | DATETIME | 아니오 |  |

## trades

| 칸 | 형 | NULL | 키 |
|---|---|---|---|
| id | INTEGER | 아니오 | PK |
| symbol | VARCHAR(10) | 아니오 |  |
| strategy_key | VARCHAR(50) | 아니오 |  |
| quantity | INTEGER | 아니오 |  |
| entry_price | FLOAT | 아니오 |  |
| exit_price | FLOAT | 아니오 |  |
| entry_reason | VARCHAR(100) | 아니오 |  |
| exit_reason | VARCHAR(100) | 아니오 |  |
| pnl_amount | FLOAT | 아니오 |  |
| pnl_pct | FLOAT | 아니오 |  |
| is_paper | BOOLEAN | 아니오 |  |
| entered_at | DATETIME | 아니오 |  |
| exited_at | DATETIME | 아니오 |  |

## universe_snapshots

| 칸 | 형 | NULL | 키 |
|---|---|---|---|
| id | INTEGER | 아니오 | PK |
| snapshot_at | DATETIME | 아니오 |  |
| symbol | VARCHAR(20) | 아니오 |  |
| name | VARCHAR(100) | 아니오 |  |
| market | VARCHAR(20) | 아니오 |  |
| market_cap | INTEGER | 아니오 |  |
| turnover | INTEGER | 예 |  |
| rank | INTEGER | 아니오 |  |
| kind | VARCHAR(20) | 예 |  |
