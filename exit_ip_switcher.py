# -*- coding: utf-8 -*-
"""
方案一：出口 IP 换向器 (Exit IP Redirector)

作用：切换本机「网络出口」——让外部网站看到的公网 IP 归属地变成另一个地区。
原理：改写操作系统的系统代理设置，把流量导向指定地区的代理节点。
      这是标准的系统功能（与浏览器插件等价，但全局生效），不修改任何网络硬件参数。

支持平台：Windows 10/11、macOS
依赖：Python 标准库（无需额外安装）

命令：
    python exit_ip_switcher.py init              生成示例节点配置
    python exit_ip_switcher.py list              列出所有节点
    python exit_ip_switcher.py add <参数>        添加节点
    python exit_ip_switcher.py use <名称>        切换到某节点
    python exit_ip_switcher.py off               关闭代理，恢复直连
    python exit_ip_switcher.py status            查看当前出口 IP 与归属地
    python exit_ip_switcher.py selftest          自检（不改动系统设置）
"""

import argparse
import json
import os
import platform
import sys
import urllib.request

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proxy_nodes.json")
IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"

# Windows 注册表：当前用户的 Internet 设置
WIN_INET_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
# 出口 IP 查询服务（国内优先，避免国际服务限流/超时）
IP_SERVICES = [
    "https://myip.ipip.net/json",
    "https://ip.zxinc.org/api.php?type=json",
    "https://ipinfo.io/json",
    "https://ip-api.com/json/?fields=query,country,regionName,city,isp",
]


def parse_ip_response(data):
    """兼容多种 IP 查询服务返回格式 -> (ip, region)"""
    ip = ""
    region = ""
    # 格式1：myip.ipip.net -> {"data":{"ip":"...","location":["中国","省","市","","运营商"]}}
    if isinstance(data.get("data"), dict):
        d = data["data"]
        raw_ip = d.get("ip", "")
        ip = raw_ip if isinstance(raw_ip, str) else ""
        if not ip:
            ip = str(d.get("myip", ""))
        loc = d.get("location")
        if isinstance(loc, list):
            region = " ".join(str(x) for x in loc if x)
        elif isinstance(loc, str):
            region = loc
        nested = d.get("ip")
        if isinstance(nested, dict):
            if not ip:
                ip = str(nested.get("query", ""))
            if not region:
                region = " ".join(str(nested.get(k, "")) for k in ("country", "regionName", "city", "isp") if nested.get(k))
        if not region:
            region = str(d.get("country", ""))
    # 格式2：ipinfo / ip-api 顶层
    if not ip:
        ip = data.get("ip", "") or data.get("query", "")
    if not region:
        region = " ".join(str(data.get(k, "")) for k in ("country", "regionName", "city", "isp") if data.get(k))
    return ip.strip(), region.strip()


# ---------------------------------------------------------------------------
# 配置管理
# ---------------------------------------------------------------------------

def load_config():
    if not os.path.isfile(CONFIG_PATH):
        return {"nodes": [], "active": None, "backup": None}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("nodes", [])
        data.setdefault("active", None)
        data.setdefault("backup", None)
        return data
    except Exception:
        return {"nodes": [], "active": None, "backup": None}


def save_config(data):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def find_node(config, name):
    for node in config["nodes"]:
        if node["name"].lower() == name.lower():
            return node
    return None


# ---------------------------------------------------------------------------
# 代理地址格式（纯函数，可自检）
# ---------------------------------------------------------------------------

def build_windows_proxy_string(node):
    """生成 Windows 的 ProxyServer 字符串。
    HTTP/HTTPS 节点：http=host:port;https=host:port
    SOCKS 节点：socks=host:port
    """
    host, port, proto = node["host"], node["port"], node["protocol"].lower()
    if proto == "socks5":
        return "socks=%s:%d" % (host, port)
    return "http=%s:%d;https=%s:%d" % (host, port, host, port)


def build_mac_proxy_args(node):
    """生成 macOS networksetup 的三个代理类型参数。"""
    proto = node["protocol"].lower()
    if proto == "socks5":
        return {"socks": (node["host"], node["port"])}
    return {"web": (node["host"], node["port"]), "secure": (node["host"], node["port"])}


def build_proxy_url(node):
    """生成供 urllib 使用的代理 URL。"""
    proto = node["protocol"].lower()
    scheme = "socks5" if proto == "socks5" else "http"
    return "%s://%s:%d" % (scheme, node["host"], node["port"])


# ---------------------------------------------------------------------------
# 系统代理读写
# ---------------------------------------------------------------------------

def _win_notify():
    """通知 Windows 系统代理设置已变化（让新设置立即生效）。"""
    try:
        import ctypes
        wininet = ctypes.windll.Wininet
        INTERNET_OPTION_SETTINGS_CHANGED = 39
        INTERNET_OPTION_REFRESH = 37
        wininet.InternetSetOptionW(0, INTERNET_OPTION_SETTINGS_CHANGED, 0, 0)
        wininet.InternetSetOptionW(0, INTERNET_OPTION_REFRESH, 0, 0)
        return True
    except Exception:
        return False


def win_read_state():
    """读取 Windows 当前系统代理状态，并备份。"""
    import winreg
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, WIN_INET_KEY, 0, winreg.KEY_READ)
    try:
        def get(name, default):
            try:
                return winreg.QueryValueEx(key, name)[0]
            except FileNotFoundError:
                return default
        return {
            "ProxyEnable": int(get("ProxyEnable", 0)),
            "ProxyServer": str(get("ProxyServer", "")),
            "ProxyOverride": str(get("ProxyOverride", "")),
        }
    finally:
        winreg.CloseKey(key)


def win_write_state(enable, server, override=None):
    import winreg
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, WIN_INET_KEY, 0, winreg.KEY_SET_VALUE)
    try:
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1 if enable else 0)
        if server is not None:
            winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, server)
        if override is not None:
            winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, override)
    finally:
        winreg.CloseKey(key)
    _win_notify()


def mac_network_service():
    """获取 macOS 当前活跃的网络服务名（Wi-Fi 或以太网）。"""
    import subprocess
    out = subprocess.check_output(["networksetup", "-listallnetworkservices"], text=True)
    for line in out.splitlines()[1:]:
        name = line.strip().lstrip("*").strip()
        if name and "disabled" not in name.lower():
            return name
    return None


def mac_set_proxy(node):
    import subprocess
    service = mac_network_service()
    if not service:
        raise RuntimeError("未找到可用的网络服务")
    args = build_mac_proxy_args(node)
    cmds = []
    if "socks" in args:
        host, port = args["socks"]
        cmds.append(["networksetup", "-setsocksfirewallproxy", service, host, str(port)])
        cmds.append(["networksetup", "-setsocksfirewallproxystate", service, "on"])
    else:
        host, port = args["web"]
        cmds.append(["networksetup", "-setwebproxy", service, host, str(port)])
        cmds.append(["networksetup", "-setsecurewebproxy", service, host, str(port)])
        cmds.append(["networksetup", "-setwebproxystate", service, "on"])
        cmds.append(["networksetup", "-setsecurewebproxystate", service, "on"])
    for cmd in cmds:
        subprocess.run(cmd, check=True)
    return service


def mac_clear_proxy():
    import subprocess
    service = mac_network_service()
    if not service:
        return False
    for flag in ("-setwebproxystate", "-setsecurewebproxystate", "-setsocksfirewallproxystate"):
        subprocess.run(["networksetup", flag, service, "off"], check=False)
    return True


# ---------------------------------------------------------------------------
# 出口 IP 查询（走当前系统出口）
# ---------------------------------------------------------------------------

def query_exit_ip(use_node=None, timeout=8):
    """查询当前出口公网 IP 及归属地。use_node 给定时，强制走该节点。
    返回 dict：{ip, region, raw, error}"""
    if use_node:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": build_proxy_url(use_node),
                                         "https": build_proxy_url(use_node)})
        )
    else:
        # 走系统默认（Windows/macOS 的 urllib 会读取系统代理设置）
        opener = urllib.request.build_opener()

    last_err = ""
    for url in IP_SERVICES:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with opener.open(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            ip, region = parse_ip_response(data)
            if ip:
                return {"ip": ip, "region": region, "raw": data, "error": ""}
        except Exception as e:
            last_err = str(e)
    return {"ip": "", "region": "", "raw": {}, "error": "查询失败：" + last_err}


# ---------------------------------------------------------------------------
# 命令实现
# ---------------------------------------------------------------------------

def cmd_init(args):
    if os.path.isfile(CONFIG_PATH):
        print("配置已存在，未覆盖：" + CONFIG_PATH)
        return 0
    sample = {
        "nodes": [
            {"name": "node-a", "region": "示例地区A", "protocol": "http", "host": "127.0.0.1", "port": 8080},
            {"name": "node-b", "region": "示例地区B", "protocol": "socks5", "host": "127.0.0.1", "port": 1080},
        ],
        "active": None, "backup": None
    }
    save_config(sample)
    print("已生成示例配置：" + CONFIG_PATH)
    print("请用 add 命令填入你自己的合法代理节点，示例节点不可用。")
    return 0


def cmd_list(args):
    config = load_config()
    if not config["nodes"]:
        print("还没有节点。先运行 init，再用 add 添加。")
        return 0
    print("%-14s %-14s %-8s %-28s %s" % ("名称", "地区", "协议", "地址", "状态"))
    for n in config["nodes"]:
        mark = "← 当前" if config["active"] == n["name"] else ""
        print("%-14s %-14s %-8s %-28s %s" % (
            n["name"], n["region"], n["protocol"],
            "%s:%d" % (n["host"], n["port"]), mark))
    return 0


def cmd_add(args):
    config = load_config()
    if find_node(config, args.name):
        print("节点已存在：" + args.name)
        return 1
    config["nodes"].append({
        "name": args.name, "region": args.region,
        "protocol": args.protocol, "host": args.host, "port": args.port
    })
    save_config(config)
    print("已添加节点：" + args.name)
    return 0


def cmd_use(args):
    config = load_config()
    node = find_node(config, args.name)
    if not node:
        print("未找到节点：" + args.name)
        return 1

    # 首次切换前备份原始系统设置，便于 off 恢复
    if IS_WINDOWS:
        if config.get("backup") is None:
            config["backup"] = win_read_state()
        win_write_state(True, build_windows_proxy_string(node))
        # 保留本地地址不走代理，避免影响内网
        win_write_state(True, build_windows_proxy_string(node),
                        "<local>;localhost;127.0.0.1;*.local")
    elif IS_MAC:
        mac_set_proxy(node)
    else:
        print("不支持的操作系统：" + platform.system())
        return 1

    config["active"] = node["name"]
    save_config(config)
    print("已切换出口到：%s（%s）" % (node["name"], node["region"]))
    print("正在验证当前出口 IP…")
    result = query_exit_ip()
    if result["error"]:
        print("  " + result["error"])
    else:
        print("  出口 IP：%s" % result["ip"])
        print("  归属地：%s" % result["region"])
    return 0


def cmd_off(args):
    config = load_config()
    if IS_WINDOWS:
        backup = config.get("backup")
        if backup:
            win_write_state(backup["ProxyEnable"], backup["ProxyServer"], backup["ProxyOverride"])
        else:
            win_write_state(False, "")
    elif IS_MAC:
        mac_clear_proxy()
    else:
        print("不支持的操作系统：" + platform.system())
        return 1
    config["active"] = None
    save_config(config)
    print("已关闭代理，恢复直连。")
    result = query_exit_ip()
    if not result["error"]:
        print("  当前出口 IP：%s（%s）" % (result["ip"], result["region"]))
    return 0


def cmd_status(args):
    print("系统：" + platform.system())
    if IS_WINDOWS:
        state = win_read_state()
        print("系统代理：%s" % ("开启" if state["ProxyEnable"] else "关闭"))
        print("代理地址：" + (state["ProxyServer"] or "无"))
    elif IS_MAC:
        print("系统代理：请在网络设置中查看（本工具通过 networksetup 管理）")
    config = load_config()
    print("当前节点：" + (config["active"] or "直连"))
    print("正在查询出口 IP…")
    result = query_exit_ip()
    if result["error"]:
        print("  " + result["error"])
    else:
        print("  出口 IP：%s" % result["ip"])
        print("  归属地：%s" % result["region"])
    return 0


# ---------------------------------------------------------------------------
# 自检（纯逻辑，不改动系统设置）
# ---------------------------------------------------------------------------

def cmd_selftest(args):
    checks = []
    n_http = {"host": "1.2.3.4", "port": 8080, "protocol": "http"}
    n_socks = {"host": "5.6.7.8", "port": 1080, "protocol": "socks5"}
    checks.append(("win_http_format",
                   build_windows_proxy_string(n_http) == "http=1.2.3.4:8080;https=1.2.3.4:8080"))
    checks.append(("win_socks_format",
                   build_windows_proxy_string(n_socks) == "socks=5.6.7.8:1080"))
    checks.append(("mac_http_args",
                   set(build_mac_proxy_args(n_http).keys()) == {"web", "secure"}))
    checks.append(("mac_socks_args",
                   set(build_mac_proxy_args(n_socks).keys()) == {"socks"}))
    checks.append(("proxy_url_http", build_proxy_url(n_http) == "http://1.2.3.4:8080"))
    checks.append(("proxy_url_socks", build_proxy_url(n_socks) == "socks5://5.6.7.8:1080"))
    cfg = {"nodes": [{"name": "A", "region": "R", "protocol": "http", "host": "h", "port": 1}]}
    checks.append(("find_node_hit", find_node(cfg, "a") is not None))
    checks.append(("find_node_miss", find_node(cfg, "b") is None))
    ip1, r1 = parse_ip_response({"data": {"ip": "1.2.3.4", "location": ["中国", "北京", "朝阳", "", "联通"]}})
    checks.append(("parse_ipip", ip1 == "1.2.3.4" and "北京" in r1))
    ip2, r2 = parse_ip_response({"ip": "5.6.7.8", "country": "US", "city": "LAX"})
    checks.append(("parse_ipinfo", ip2 == "5.6.7.8" and "US" in r2))
    ip3, r3 = parse_ip_response({"data": {"myip": "9.9.9.9", "ip": {"query": "9.9.9.9", "country": "CN"}}})
    checks.append(("parse_zxinc", ip3 == "9.9.9.9" and "CN" in r3))

    passed = sum(1 for _, ok in checks if ok)
    for name, ok in checks:
        print(("  [OK] " if ok else "  [FAIL] ") + name)
    print("SELFTEST %d/%d passed" % (passed, len(checks)))
    return 0 if passed == len(checks) else 1


def build_parser():
    p = argparse.ArgumentParser(description="出口 IP 换向器：切换系统网络出口")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("init", help="生成示例配置").set_defaults(func=cmd_init)
    sub.add_parser("list", help="列出节点").set_defaults(func=cmd_list)

    pa = sub.add_parser("add", help="添加节点")
    pa.add_argument("name")
    pa.add_argument("--region", default="未命名地区")
    pa.add_argument("--protocol", default="http", choices=["http", "socks5"])
    pa.add_argument("--host", required=True)
    pa.add_argument("--port", type=int, required=True)
    pa.set_defaults(func=cmd_add)

    pu = sub.add_parser("use", help="切换出口")
    pu.add_argument("name")
    pu.set_defaults(func=cmd_use)

    sub.add_parser("off", help="关闭代理恢复直连").set_defaults(func=cmd_off)
    sub.add_parser("status", help="查看出口 IP").set_defaults(func=cmd_status)
    sub.add_parser("selftest", help="自检").set_defaults(func=cmd_selftest)
    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
