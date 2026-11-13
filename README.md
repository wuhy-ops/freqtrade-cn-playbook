# Freqtrade 中文实践模板

> 部署 + 回测 + AI 辅助 + 负结果方法论

这是一个 Freqtrade 的中文工程模板，目标不是提供“能赚钱的策略”，而是提供一套**可复现的实践框架**：

- 海外 VPS 的完整部署脚本（Docker + systemd + 防火墙 + 看门狗）
- 可复用的回测评估框架（滚动窗口、样本外、成本压力、利润集中度）
- AI 辅助层（每日风控状态、晨报、策略诊断）
- 30 天影子跟踪（不产生真实订单）
- 13+ 轮策略实验的**负结果档案**与方法论

> **重要声明**
>
> 仓库中的所有策略都没有通过稳定盈利验证。它们是教学示例和负结果记录，
> **不构成投资建议**，请勿直接用于实盘。
>
> Freqtrade 官方文档是最好的权威来源；本项目只是中文实践补充。

---

## 适合谁

- 想在海外 VPS 上跑 Freqtrade，但被 Docker / systemd / 防火墙折腾过的人
- 想知道“回测到底该怎么信”的人
- 对 AI 辅助交易（风控、晨报、诊断）感兴趣的人
- 想看看别人怎么把策略研究做成一套**有纪律的负结果档案**的人

## 不适合谁

- 想复制一个稳定盈利策略的人（这里没有）
- 想找高频 / 做市 / 套利完整实现的人（本项目未覆盖）
- 不愿读代码、只想一键赚钱的人

---

## 快速开始

### 1. 服务器

- Ubuntu 22.04 / 24.04
- 境外地域（东京 / 新加坡 / 香港）
- 1~2 vCPU / 1~2 GB 内存 / 25 GB 磁盘
- 出口 IP 固定（用于交易所 API Key 白名单）

### 2. 部署

```bash
scp deploy/setup.sh root@<服务器IP>:/root/
ssh root@<服务器IP>
chmod +x setup.sh && ./setup.sh
```

脚本会安装 Docker、配置防火墙（仅 22 端口）、启用 fail2ban、创建目录、
生成示例配置，并注册 Freqtrade systemd 服务。

附加脚本放置：

```bash
cp scripts/*.py /opt/freqtrade/
cp strategies/MyStrategy.py /opt/freqtrade/user_data/strategies/
```

### 3. 配置

```bash
nano /opt/freqtrade/user_data/config.json
```

至少填写：

- `exchange.key` / `exchange.secret`（币安 API Key）
- `telegram.token` / `telegram.chat_id`（可选）
- `dry_run` 保持 `true`

API Key 权限只勾「读取」+「现货交易」，**绝不开提现**，并绑定服务器 IP。

### 4. 下载数据 / 回测 / 模拟盘

```bash
# 下载历史数据
docker run --rm -v /opt/freqtrade/user_data:/freqtrade/user_data \
  freqtradeorg/freqtrade:stable download-data --exchange binance \
  --pairs BTC/USDT ETH/USDT -t 1h --days 365

# 启动模拟盘
systemctl enable --now freqtrade
```

---

## 目录结构

```
freqtrade-cn-playbook/
├── deploy/
│   ├── setup.sh                  一键部署
│   ├── config.example.json       配置模板（密钥留空）
│   └── systemd/                  Freqtrade / AI / 影子跟踪 / 看门狗
├── docs/
│   ├── deployment.md             部署与安全
│   ├── operations.md             日常运维与 Telegram
│   ├── ai-layer.md               AI 风控/晨报/诊断
│   └── negative-results.md       ★ 负结果与方法论
├── scripts/
│   ├── market_data.py            公开市场数据采集
│   ├── ai_regime.py              每日风控状态
│   ├── ai_report.py              每日晨报
│   ├── ai_strategy_review.py     策略诊断
│   ├── watchdog.py               服务看门狗
│   ├── shadow_tracker.py         30 天影子跟踪
│   ├── b_rotation.py             横截面动量轮动回测
│   ├── build_momentum_ranking.py 周度动量排名
│   ├── binance_bnb_burn.py       BNB 手续费抵扣
│   └── reports/                  回测结果汇总脚本
├── strategies/
│   ├── MyStrategy.py             教学示例策略
│   └── variants/                 E / B / V / L / M / D / R 系列实验
└── results/
    └── backtest-summary.md       脱敏后的关键结果
```

---

## 回测框架

本项目把“回测评估”拆成可复用的脚本，而不是只看总收益：

| 脚本 | 用途 |
|---|---|
| `reports/compare_backtests.py` | 多策略、多时间段横向对比 |
| `reports/strategy_report.py` | 滚动窗口 + 分币种 + 退出原因 + 重复入场率 |
| `reports/d1_report.py` | 日线策略的 3 年窗口报告 |
| `reports/d1_report6.py` | 6 年窗口 + 利润集中度 + 成本敏感性 |
| `reports/limit_report.py` | 限价单成交率 / 期望 / ROI 占比 |
| `reports/mr_report.py` | 均值回归报告 |

核心评估原则：

1. **滚动窗口**，不只看一个回测区间
2. **训练 / 样本外**分离
3. **成本压力**：0.2%/边为主，0.3%/边为敏感
4. **利润集中度 <60%**：防止单年/单币撑起总收益
5. **lookahead-analysis**：检查未来函数
6. **样本量门槛**：笔数不足时标记 exploratory

详见 [docs/negative-results.md](docs/negative-results.md)。

---

## AI 辅助层

```
公开行情 API ──> market_data.py ──> ai_regime.py ──> ai_regime.json
                                              │
                          Freqtrade 开仓前读取 ┘
                                              │
                                       ai_report.py ──> Telegram 晨报
```

- `ai_regime.py`：每天 08:10（北京时间，日线收盘后）调用 DeepSeek，
  输出 `risk_on / neutral / risk_off`，作为新开仓的开关
- `ai_report.py`：每天 08:15 生成行情晨报
- `ai_strategy_review.py`：回测后让 AI 做策略诊断（研究用）
- `shadow_tracker.py`：每天 08:12 记录 B1 持仓与 RSI2 信号（不下单）

设计原则：**AI 只做过滤器和解释器，不做最终决策**。所有 LLM 输出都有
确定性降级路径，模型失败不会阻塞交易。

详见 [docs/ai-layer.md](docs/ai-layer.md)。

---

## 负结果档案

这是本项目最有价值的部分：记录了 13+ 轮策略实验，每一轮都有：

- 预注册参数与判定标准
- 训练 / 样本外 / 滚动窗口结果
- 成本压力测试
- 失败原因与可复现回测

关键结论：

1. 成本是硬约束；高换手策略很难跑赢 0.2%/边
2. 过滤器覆盖率不足时，放行集系统性偏坏（三次复现）
3. 同一过滤器在时序框架和横截面框架下结论不同，不能外推
4. 利润集中度条款阻止了多次“单年翻盘”式误判

详见 [docs/negative-results.md](docs/negative-results.md)。

---

## 安全

- API Key 永不开提现权限
- IP 白名单绑定服务器出口 IP
- 密钥只存在服务器，权限 600
- 不提交 `config.json`、`*.key`、`*.sqlite`、日志、备份
- 公开仓库前先脱敏：IP、token、chat_id、个人路径、账户信息

---

## License

GPL-3.0。Freqtrade 本身是 GPL-3.0，策略文件属于衍生作品；
使用本仓库代码时请遵守相同许可。

---

## 免责声明

本项目仅用于学习、研究和工程实践，不构成投资建议。
加密货币交易具有高风险，可能导致全部本金损失。
在任何司法辖区使用前，请自行确认当地法律与交易所条款。
