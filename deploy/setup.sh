#!/usr/bin/env bash
# ============================================================
#  Freqtrade 一键部署脚本（Ubuntu 22.04 / 24.04）
#  用法:  scp setup.sh root@<服务器IP>:/root/
#         ssh root@<服务器IP>
#         chmod +x setup.sh && ./setup.sh
# ============================================================
set -euo pipefail

# ----------------- 可修改配置 -----------------
INSTALL_DIR="/opt/freqtrade"
EXCHANGE="binance"
STRATEGY="MyStrategy"
PAIRS=("BTC/USDT" "ETH/USDT")
TIMEFRAME="1h"
DAYS=365
SSH_PORT=22
# ---------------------------------------------

GREEN='\033[1;32m'; RED='\033[1;31m'; YEL='\033[1;33m'; NC='\033[0m'
log()  { echo -e "\n${GREEN}[+]${NC} $*"; }
warn() { echo -e "${YEL}[!]${NC} $*"; }
err()  { echo -e "${RED}[x]${NC} $*"; }

[[ $EUID -eq 0 ]] || { err "请用 root 运行（sudo ./setup.sh）"; exit 1; }

# ------------------------------------------------------------
log "1/8 更新系统并安装依赖"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y ca-certificates curl gnupg git ufw fail2ban docker.io unzip

log "2/8 启用 Docker"
systemctl enable --now docker
docker --version

log "3/8 配置防火墙（只放行 SSH）"
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow "${SSH_PORT}/tcp" >/dev/null
ufw --force enable >/dev/null
ufw status

log "4/8 启用 fail2ban（防 SSH 爆破）"
cat > /etc/fail2ban/jail.d/sshd.local <<'EOF'
[sshd]
enabled = true
maxretry = 5
bantime  = 1h
findtime = 10m
EOF
systemctl enable --now fail2ban >/dev/null 2>&1 || warn "fail2ban 启动失败，可忽略（不影响交易）"

log "5/8 创建目录结构"
mkdir -p "${INSTALL_DIR}/user_data"/{strategies,data,logs}
chmod 750 "${INSTALL_DIR}"
cd "${INSTALL_DIR}"

log "6/8 写入配置文件与策略"
cat > user_data/config.json <<'JSON'
{
  "max_open_trades": 2,
  "stake_currency": "USDT",
  "stake_amount": 100,
  "tradable_balance_ratio": 0.5,
  "fiat_display_currency": "USD",
  "dry_run": true,
  "dry_run_wallet": 1000,
  "cancel_open_orders_on_exit": false,
  "trading_mode": "spot",
  "margin_mode": "",
  "unfilledtimeout": {
    "entry": 10,
    "exit": 10,
    "unit": "minutes"
  },
  "entry_pricing": {
    "price_side": "same",
    "use_order_book": true,
    "order_book_top": 1,
    "price_last_balance": 0.0,
    "check_depth_of_market": {
      "enabled": false,
      "bids_to_ask_delta": 1
    }
  },
  "exit_pricing": {
    "price_side": "same",
    "use_order_book": true,
    "order_book_top": 1
  },
  "exchange": {
    "name": "__EXCHANGE__",
    "key": "",
    "secret": "",
    "ccxt_config": {},
    "ccxt_async_config": {},
    "pair_whitelist": [
      "BTC/USDT",
      "ETH/USDT"
    ],
    "pair_blacklist": []
  },
  "pairlists": [
    { "method": "StaticPairList" }
  ],
  "protections": [],
  "bot_name": "freqtrade",
  "initial_state": "running",
  "internals": {
    "process_throttle_secs": 5
  }
}
JSON
sed -i "s/__EXCHANGE__/${EXCHANGE}/" user_data/config.json

cat > user_data/strategies/MyStrategy.py <<'PY'
from pandas import DataFrame

from freqtrade.strategy import IStrategy

import talib.abstract as ta


class MyStrategy(IStrategy):
    """示例策略：多头趋势中回踩买入。

    注意：这只是教学示例，回测好看不代表实盘能赚钱。
    先用 dry-run 观察，再逐步改造成自己的逻辑。
    """

    INTERFACE_VERSION = 3

    timeframe = "1h"
    can_short = False

    # 止盈：时间越久要求越低
    minimal_roi = {
        "0": 0.12,
        "240": 0.06,
        "720": 0.03,
    }

    # 硬止损 8%
    stoploss = -0.08

    trailing_stop = True
    trailing_stop_positive = 0.03
    trailing_stop_positive_offset = 0.06
    trailing_only_offset_is_reached = True

    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    startup_candle_count = 210

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema20"] = ta.EMA(dataframe, timeperiod=20)
        dataframe["ema50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
        dataframe["vol_mean"] = dataframe["volume"].rolling(24).mean()
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        cond = (
            (dataframe["ema20"] > dataframe["ema50"])
            & (dataframe["ema50"] > dataframe["ema200"])
            & (dataframe["close"] > dataframe["ema20"])
            & (dataframe["rsi"] > 50)
            & (dataframe["rsi"] < 70)
            & (dataframe["volume"] > dataframe["vol_mean"])
        )
        dataframe.loc[cond, "enter_long"] = 1
        dataframe.loc[cond, "enter_tag"] = "trend_pullback"
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        cond = (dataframe["ema20"] < dataframe["ema50"]) | (dataframe["rsi"] > 80)
        dataframe.loc[cond, "exit_long"] = 1
        dataframe.loc[cond, "exit_tag"] = "trend_break"
        return dataframe
PY

log "7/8 安装命令行快捷脚本 ft"
cat > /usr/local/bin/ft <<EOF
#!/usr/bin/env bash
exec docker run --rm -it \\
  -v ${INSTALL_DIR}/user_data:/freqtrade/user_data \\
  freqtradeorg/freqtrade:stable "\$@"
EOF
chmod +x /usr/local/bin/ft

log "8/8 拉取镜像并注册 systemd 服务"
docker pull freqtradeorg/freqtrade:stable

cat > /etc/systemd/system/freqtrade.service <<EOF
[Unit]
Description=Freqtrade trading bot
After=docker.service network-online.target
Requires=docker.service
Wants=network-online.target

[Service]
Restart=always
RestartSec=15
ExecStartPre=-/usr/bin/docker rm -f freqtrade
ExecStart=/usr/bin/docker run --rm --name freqtrade \\
  -v ${INSTALL_DIR}/user_data:/freqtrade/user_data \\
  freqtradeorg/freqtrade:stable trade \\
  --logfile /freqtrade/user_data/logs/freqtrade.log \\
  --db-url sqlite:////freqtrade/user_data/tradesv3.sqlite \\
  --config /freqtrade/user_data/config.json \\
  --strategy ${STRATEGY}
ExecStop=/usr/bin/docker stop freqtrade

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload

# 连通性测试
log "检查交易所连通性（${EXCHANGE}）"
case "${EXCHANGE}" in
    binance) TEST_URL="https://api.binance.com/api/v3/time" ;;
    okx)     TEST_URL="https://www.okx.com/api/v5/public/time" ;;
    bybit)   TEST_URL="https://api.bybit.com/v5/market/time" ;;
    *)       TEST_URL="" ;;
esac
if [[ -n "${TEST_URL}" ]] && curl -fsS -m 10 "${TEST_URL}" >/dev/null 2>&1; then
    echo "  ✅ ${EXCHANGE} API 可达"
else
    warn "无法连接 ${EXCHANGE} API —— 可能是地域封锁或该域名不适用"
    warn "如果确认不通，把 config.json 里 name 改成 okx 或 bybit 再试"
fi

cat <<EOF

============================================================
部署完成。接下来按顺序执行：

  1) 填 API Key（可选，dry-run 可以先用公共数据跑）
     nano ${INSTALL_DIR}/user_data/config.json
     修改 "key" 和 "secret" 两行

  2) 下载历史数据（约 1~3 分钟）
     ft download-data --exchange ${EXCHANGE} --pairs ${PAIRS[*]} -t ${TIMEFRAME} --days ${DAYS}

  3) 回测
     ft backtesting --config /freqtrade/user_data/config.json \\
        --strategy ${STRATEGY} --timerange 20250101-

  4) 启动模拟盘（dry_run 已默认开启）
     systemctl enable --now freqtrade
     tail -f ${INSTALL_DIR}/user_data/logs/freqtrade.log

  5) 停止 / 重启
     systemctl stop freqtrade
     systemctl restart freqtrade

  6) 回测满意后改实盘：config.json 里 "dry_run": true -> false
     然后 systemctl restart freqtrade

  安全提醒：
   - API Key 不要开「允许提现」，并绑定本机公网 IP
   - 保持 firewalls 只开 22 端口
============================================================
EOF
