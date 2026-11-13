#!/usr/bin/env python3
"""开启币安「使用 BNB 抵扣现货手续费」。

需要 config.json 里已填写 API Key / Secret，且 Key 具备现货交易权限。
调用 POST /sapi/v1/bnbBurn 把 spotBNBBurn 设为 true。

用法：
  python3 binance_bnb_burn.py           查询当前状态并自动开启
  python3 binance_bnb_burn.py --status  只查询
"""

import hashlib
import hmac
import json
import sys
import time
import urllib.parse
import urllib.request

CFG_PATH = "/opt/freqtrade/user_data/config.json"
BASE = "https://api.binance.com"


def signed_request(path, params, api_key, secret, method="GET"):
    params = dict(params)
    params["timestamp"] = int(time.time() * 1000)
    params.setdefault("recvWindow", 5000)
    query = urllib.parse.urlencode(params)
    signature = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    url = BASE + path + "?" + query + "&signature=" + signature
    req = urllib.request.Request(url, headers={"X-MBX-APIKEY": api_key}, method=method)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    cfg = json.load(open(CFG_PATH, encoding="utf-8"))
    ex = cfg.get("exchange") or {}
    key, secret = ex.get("key", ""), ex.get("secret", "")
    if not key or not secret:
        print("❌ config.json 里还没有 API Key / Secret。")
        print("")
        print("两种处理方式：")
        print("1) 网页手动开启（推荐，最简单）：")
        print("   币安 -> 个人中心 -> 设置 -> 支付 -> 开启「使用 BNB 支付手续费」")
        print("   确认现货账户里有少量 BNB（约 1~2 个即可）")
        print("2) 填好 API Key 后重新运行本脚本，自动开启：")
        print("   python3 /opt/freqtrade/binance_bnb_burn.py")
        sys.exit(1)

    try:
        status = signed_request("/sapi/v1/bnbBurn", {}, key, secret)
    except Exception as exc:
        print("❌ 查询失败：%r" % exc)
        print("请检查 API Key 权限（需要现货交易权限）和 IP 白名单。")
        sys.exit(1)

    print("当前状态：", json.dumps(status, ensure_ascii=False))
    if "--status" in sys.argv:
        return

    if status.get("spotBNBBurn"):
        print("✅ 已经开启 BNB 抵扣，无需操作。")
        return

    try:
        result = signed_request("/sapi/v1/bnbBurn", {"spotBNBBurn": "true"}, key, secret, method="POST")
        print("开启结果：", json.dumps(result, ensure_ascii=False))
        print("✅ 已请求开启。请确认现货账户有 BNB 余额。")
    except Exception as exc:
        print("❌ 开启失败：%r" % exc)


if __name__ == "__main__":
    main()
