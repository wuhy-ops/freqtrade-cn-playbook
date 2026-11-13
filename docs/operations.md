# 日常运维

## 服务管理

```bash
systemctl status freqtrade
systemctl restart freqtrade
systemctl stop freqtrade
systemctl enable freqtrade      # 开机自启
systemctl disable freqtrade     # 取消自启
```

## 日志

```bash
# 实时日志
tail -f /opt/freqtrade/user_data/logs/freqtrade.log

# 只看关键事件
grep -E "Long signal|Bought|Sell signal|ERROR" \
  /opt/freqtrade/user_data/logs/freqtrade.log | tail -20

# AI 相关日志
tail -20 /opt/freqtrade/user_data/logs/ai_regime.log
tail -20 /opt/freqtrade/user_data/logs/ai_report.log
tail -20 /opt/freqtrade/user_data/logs/shadow_tracker.log
tail -20 /opt/freqtrade/user_data/logs/watchdog.log
```

## 定时任务

```bash
systemctl list-timers --no-pager

# 手动触发
systemctl start ai-regime.service
systemctl start ai-report.service
systemctl start shadow-tracker.service
python3 /opt/freqtrade/watchdog.py --test
```

## Telegram 常用命令

给绑定的 Telegram bot 发送：

| 命令 | 作用 |
|---|---|
| `/status` | 当前持仓与状态 |
| `/profit` | 总盈亏、胜率 |
| `/balance` | 余额 |
| `/daily` | 当日收益 |
| `/trades` | 最近交易 |
| `/performance` | 各币种表现 |
| `/whitelist` | 当前交易对 |
| `/stop` | 暂停开新仓（持仓继续管理） |
| `/start` | 恢复开仓 |
| `/reload_config` | 热加载配置 |
| `/help` | 全部命令 |

注意：`/forceexit`、`/forcebuy` 等强制命令会真实成交，实盘慎用。

## 看门狗

`watchdog.py` 每 5 分钟检查：

- systemd 服务是否 active
- Docker 容器是否运行
- 日志心跳是否在 10 分钟内
- 磁盘使用率是否超过 90%

异常时推送 Telegram；恢复时也推送。
若 freqtrade 处于“已停止且 disabled”状态，视为预期停止，不告警。

## 交易机器人与 AI 层的解耦

- 停止 freqtrade 后，AI 风控 / 晨报 / 影子跟踪仍可独立运行
- 晨报在无机器人时会显示空仓
- 看门狗只在服务 enabled 但未运行时告警

## 备份与恢复

```bash
# 备份
tar czf /root/backups/ft-$(date +%F).tar.gz -C /opt/freqtrade user_data

# 恢复
systemctl stop freqtrade
tar xzf /root/backups/ft-YYYY-MM-DD.tar.gz -C /opt/freqtrade
systemctl start freqtrade
```

## 常见异常

| 现象 | 排查 |
|---|---|
| Telegram 没消息 | 检查 `config.json` 的 token/chat_id；先给 bot 发一句话 |
| 机器人不开仓 | 看 AI 风控状态、策略信号、max_open_trades |
| 频繁重启 | `systemctl status` + `docker logs` |
| 心跳停止 | 看门狗会告警；检查容器和 API 连通性 |
| 磁盘满 | 清理日志/回测结果，或扩容 |
