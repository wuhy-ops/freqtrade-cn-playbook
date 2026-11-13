# 部署指南

## 1. 服务器选择

| 项目 | 建议 |
|---|---|
| 系统 | Ubuntu 22.04 / 24.04 LTS |
| 地域 | 东京 / 新加坡 / 香港（离交易所近、网络可达） |
| 配置 | 1~2 vCPU / 1~2 GB 内存 / 25 GB SSD |
| 带宽 | 按流量计费即可，机器人流量很小 |
| 成本 | 约 5~10 美元/月 |

注意事项：

- 不要选“抢占式实例”，可能被回收
- 需要固定公网 IP（用于交易所 API Key 白名单）
- 选择交易所 API 可访问的地域；使用前请自行确认当地法律与交易所条款
- 安全组只放行 SSH 端口（默认 22）

## 2. 基础部署

```bash
scp deploy/setup.sh root@<服务器IP>:/root/
ssh root@<服务器IP>
chmod +x setup.sh
./setup.sh
```

脚本会完成：

1. 安装 Docker、ufw、fail2ban
2. 防火墙默认拒绝入站，只放行 22/tcp
3. 创建 `/opt/freqtrade/user_data/{strategies,data,logs}`
4. 生成示例 `config.json`（dry_run = true）
5. 生成示例策略 `MyStrategy.py`
6. 安装快捷命令 `/usr/local/bin/ft`
7. 拉取 `freqtradeorg/freqtrade:stable`
8. 注册 `freqtrade.service`

## 2.1 附加文件放置

本仓库的脚本默认按 `/opt/freqtrade` 路径运行：

```bash
cp scripts/*.py /opt/freqtrade/
cp strategies/MyStrategy.py /opt/freqtrade/user_data/strategies/
cp deploy/config.example.json /opt/freqtrade/user_data/config.json
```

说明：

- `scripts/reports/` 是回测汇总脚本，可放在任意位置，内部路径按需修改
- `strategies/variants/` 仅用于研究，不要直接启用
- systemd 单元假设脚本位于 `/opt/freqtrade/`

## 3. 配置

```bash
nano /opt/freqtrade/user_data/config.json
```

必须确认：

```json
"dry_run": true,
"exchange": {
  "name": "binance",
  "key": "你的 API Key",
  "secret": "你的 Secret",
  "pair_whitelist": ["BTC/USDT", "ETH/USDT"]
}
```

API Key 要求：

- 只勾选「允许读取」+「允许现货交易」
- **不要**勾选「允许提现」
- 绑定服务器公网 IP
- 不要把 Key 提交到 Git

> Freqtrade 2026.x 起，配置里如果还保留 `"protections": []` 会直接报
> `DEPRECATED ... is deprecated` 并拒绝启动，删掉该字段即可。

## 4. 下载数据

```bash
docker run --rm -v /opt/freqtrade/user_data:/freqtrade/user_data \
  freqtradeorg/freqtrade:stable download-data \
  --exchange binance \
  --pairs BTC/USDT ETH/USDT \
  -t 1h 4h 1d \
  --days 1095
```

若已有数据但需要补更早的历史，加 `--prepend`。

## 5. 回测

```bash
docker run --rm -v /opt/freqtrade/user_data:/freqtrade/user_data \
  freqtradeorg/freqtrade:stable backtesting \
  --config /freqtrade/user_data/config.json \
  --strategy MyStrategy \
  --timerange 20231113- \
  --export trades
```

## 6. 模拟盘

```bash
systemctl enable --now freqtrade
systemctl status freqtrade
tail -f /opt/freqtrade/user_data/logs/freqtrade.log
```

## 7. 安装附加 systemd 单元

```bash
cp deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload

systemctl enable --now ai-regime.timer
systemctl enable --now ai-report.timer
systemctl enable --now shadow-tracker.timer
systemctl enable --now freqtrade-watchdog.timer
```

时间线（北京时间）：

```
08:10  AI 风控状态
08:12  影子跟踪
08:15  AI 晨报
每 5 分钟  看门狗
```

## 8. 安全清单

- [ ] SSH 只允许密钥登录，禁用密码
- [ ] 防火墙只开 22 端口
- [ ] API Key 不开提现权限 + IP 白名单
- [ ] 密钥文件权限 600
- [ ] 不在聊天、日志、Git 中暴露密钥
- [ ] 定期备份 `user_data`

## 9. 备份

```bash
tar czf /root/backups/ft-$(date +%F).tar.gz \
  -C /opt/freqtrade user_data
```

备份包含配置、策略、数据库、回测结果和日志。**不要提交到公开仓库。**

## 10. 常见问题

| 现象 | 处理 |
|---|---|
| 币安 API 连不上 | 换 OKX / Bybit，或确认服务器地域 |
| 服务启动即退出 | 看 `docker logs freqtrade` 和 `freqtrade.log` |
| `protections` 报错 | 删除配置里的该字段 |
| `floor("W-MON")` 报错 | pandas 3.x 不支持非固定频率，改用手动算周一 |
| 回测内存不足 | 减少币种或区间，或加 swap |
| systemd 单元不生效 | `systemctl daemon-reload` 后再 enable |
