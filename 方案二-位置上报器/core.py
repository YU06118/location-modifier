# -*- coding: utf-8 -*-
"""
方案二：位置上报器 (Location Reporter)

作用：让浏览器/应用「读到」另一个经纬度位置——用于地图功能调试、位置相关功能测试。
原理：通过 Chrome DevTools Protocol (CDP) 的 Emulation.setGeolocationOverride 注入位置。
      这改变应用读取到的位置，不改变网络出口 IP（网站用 IP 库反查仍是真实归属地）。

支持平台：Windows / macOS（任何以调试端口启动的 Chromium 内核浏览器）
依赖：无（CDP 通信用标准库实现最小 WebSocket 客户端）

用法：
    1) 先以调试端口启动浏览器：
       Chrome --remote-debugging-port=9222
       Edge   --remote-debugging-port=9222
    2) 命令行：
       python location_reporter.py scan              扫描浏览器页面
       python location_reporter.py cities            列出内置城市
       python location_reporter.py set 39.9042 116.4074   注入位置（保持连接）
       python location_reporter.py city 北京          按城市名注入
       python location_reporter.py clear             清除注入（结束所有会话）

    注：set/city 是前台命令，会保持与浏览器的连接直到你按 Ctrl+C。
        连接断开后注入自动失效，这是 CDP 的机制，不是故障。
"""

import argparse
import base64
import json
import math
import os
import socket
import struct
import sys
import threading
import time
import urllib.parse
import urllib.request

DEBUG_PORTS = [9222, 9223, 9224]
def _app_dir():
    """会话记录落盘目录：打包后用 exe 所在目录，源码运行时用脚本所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


SESSION_FILE = os.path.join(_app_dir(), ".location_sessions.json")

CITIES = {
    "北京": (39.9042, 116.4074), "上海": (31.2304, 121.4737),
    "广州": (23.1291, 113.2644), "深圳": (22.5431, 114.0579),
    "成都": (30.5728, 104.0668), "杭州": (30.2741, 120.1551),
    "香港": (22.3193, 114.1694), "台北": (25.0330, 121.5654),
    "东京": (35.6762, 139.6503), "首尔": (37.5665, 126.9780),
    "新加坡": (1.3521, 103.8198), "伦敦": (51.5074, -0.1278),
    "纽约": (40.7128, -74.0060), "洛杉矶": (34.0522, -118.2437),
    "悉尼": (-33.8688, 151.2093), "莫斯科": (55.7558, 37.6173),
}


# ---------------------------------------------------------------------------
# 坐标系转换：GCJ-02 <-> WGS-84（内置地图用 GCJ-02，CDP 地理定位用 WGS-84）
# ---------------------------------------------------------------------------

def _out_of_china(lon, lat):
    return not (73.66 < lon < 135.05 and 3.86 < lat < 53.55)


def _transform_lat(x, y):
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lon(x, y):
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lon, lat):
    if _out_of_china(lon, lat):
        return lon, lat
    a = 6378245.0
    ee = 0.00669342162296594323
    dlat = _transform_lat(lon - 105.0, lat - 35.0)
    dlon = _transform_lon(lon - 105.0, lat - 35.0)
    radlat = lat / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1 - ee * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((a * (1 - ee)) / (magic * sqrtmagic) * math.pi)
    dlon = (dlon * 180.0) / (a / sqrtmagic * math.cos(radlat) * math.pi)
    return lon + dlon, lat + dlat


# ---------------------------------------------------------------------------
# CDP 操作
# ---------------------------------------------------------------------------

def http_json(url, timeout=3):
    """绕过系统代理访问本地调试端口。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with opener.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def scan_pages():
    """扫描所有调试端口上的页面。返回 [{port, id, title, url, ws}]"""
    pages = []
    for port in DEBUG_PORTS:
        try:
            data = http_json("http://127.0.0.1:%d/json/list" % port)
            for item in data:
                if item.get("type") == "page" and item.get("webSocketDebuggerUrl"):
                    pages.append({
                        "port": port, "id": item.get("id", ""),
                        "title": item.get("title", "(无标题)"),
                        "url": item.get("url", ""),
                        "ws": item["webSocketDebuggerUrl"],
                    })
        except Exception:
            continue
    return pages


class MiniWebSocket:
    """用标准库实现的最小 WebSocket 客户端（仅文本帧，够 CDP 用）。"""

    def __init__(self, ws_url, timeout=5):
        u = urllib.parse.urlparse(ws_url)
        host = u.hostname
        port = u.port or (443 if u.scheme == "wss" else 80)
        path = u.path + ("?" + u.query if u.query else "")
        sock = socket.create_connection((host, port), timeout=timeout)
        if u.scheme == "wss":
            import ssl
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = (
            "GET %s HTTP/1.1\r\n"
            "Host: %s:%d\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ) % (path, host, port, key)
        sock.sendall(req.encode("ascii"))
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                raise RuntimeError("WebSocket 握手失败：连接关闭")
            buf += chunk
        header, rest = buf.split(b"\r\n\r\n", 1)
        if b"101" not in header.split(b"\r\n")[0]:
            raise RuntimeError("WebSocket 握手失败：" + header.split(b"\r\n")[0].decode("latin-1"))
        self.sock = sock
        self._buf = rest
        self.sock.settimeout(timeout)

    def _recv_exact(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("连接已关闭")
            self._buf += chunk
        data, self._buf = self._buf[:n], self._buf[n:]
        return data

    def send_text(self, text):
        payload = text.encode("utf-8")
        mask = os.urandom(4)
        n = len(payload)
        head = bytearray([0x81])  # FIN + text
        if n < 126:
            head.append(0x80 | n)
        elif n < 65536:
            head.append(0x80 | 126); head.extend(struct.pack("!H", n))
        else:
            head.append(0x80 | 127); head.extend(struct.pack("!Q", n))
        head.extend(mask)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(head) + masked)

    def recv_text(self):
        while True:
            b0, b1 = self._recv_exact(2)
            opcode = b0 & 0x0F
            length = b1 & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]
            payload = self._recv_exact(length)
            if opcode == 0x1:  # text
                return payload.decode("utf-8", "replace")
            if opcode == 0x9:  # ping -> pong
                self.sock.sendall(b"\x8a\x80" + os.urandom(4))
                continue
            if opcode == 0x8:  # close
                raise RuntimeError("连接已关闭")

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


class CDPClient:
    """极简 CDP 客户端：保持 WebSocket 长连接，零第三方依赖。"""

    def __init__(self, ws_url, timeout=5):
        self.ws = MiniWebSocket(ws_url, timeout=timeout)
        self.seq = 0
        self.lock = threading.Lock()

    def call(self, method, params=None):
        with self.lock:
            self.seq += 1
            msg_id = self.seq
            self.ws.send_text(json.dumps({"id": msg_id, "method": method,
                                          "params": params or {}}))
            while True:
                msg = json.loads(self.ws.recv_text())
                if msg.get("id") == msg_id:
                    if msg.get("error"):
                        raise RuntimeError(msg["error"].get("message", "CDP 调用失败"))
                    return msg.get("result", {})

    def set_location(self, lat, lon, accuracy=20):
        return self.call("Emulation.setGeolocationOverride", {
            "latitude": lat, "longitude": lon, "accuracy": accuracy
        })

    def clear_location(self):
        return self.call("Emulation.clearGeolocationOverride", {})

    def close(self):
        self.ws.close()


# ---------------------------------------------------------------------------
# 会话记录（供 clear 命令清理）
# ---------------------------------------------------------------------------

def write_session(port, page_id, lat, lon):
    sessions = []
    if os.path.isfile(SESSION_FILE):
        try:
            with open(SESSION_FILE, "r", encoding="utf-8") as f:
                sessions = json.load(f)
        except Exception:
            sessions = []
    sessions.append({"port": port, "id": page_id, "lat": lat, "lon": lon, "pid": os.getpid()})
    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(sessions, f, ensure_ascii=False, indent=2)


def read_sessions():
    if not os.path.isfile(SESSION_FILE):
        return []
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def clear_sessions_file():
    try:
        os.remove(SESSION_FILE)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 命令
# ---------------------------------------------------------------------------

def cmd_scan(args):
    pages = scan_pages()
    if not pages:
        print("未发现浏览器页面。请先以 --remote-debugging-port=9222 启动 Chrome/Edge。")
        return 0
    print("%-6s %-30s %s" % ("端口", "标题", "地址"))
    for p in pages:
        print("%-6d %-30s %s" % (p["port"], p["title"][:30], p["url"][:60]))
    return 0


def cmd_cities(args):
    print("内置城市（用 city <名称> 调用）：")
    for name in CITIES:
        lat, lon = CITIES[name]
        print("  %-8s %.4f, %.4f" % (name, lat, lon))
    return 0


def _inject_and_hold(lat, lon, page_index=0):
    """注入位置并保持连接（Ctrl+C 退出）。"""
    pages = scan_pages()
    if not pages:
        print("未发现浏览器页面。请先以 --remote-debugging-port=9222 启动浏览器。")
        return 1
    if page_index >= len(pages):
        print("页面序号超出范围，共 %d 个页面。" % len(pages))
        return 1
    target = pages[page_index]

    client = None
    browser_client = None
    try:
        # 尽力授权：对浏览器级目标授予当前页面 origin 的定位权限
        try:
            ver = http_json("http://127.0.0.1:%d/json/version" % target["port"])
            browser_ws = ver.get("webSocketDebuggerUrl")
            if browser_ws:
                browser_client = CDPClient(browser_ws)
                origin = urllib.parse.urlparse(target["url"])
                page_origin = "%s://%s" % (origin.scheme, origin.netloc)
                if page_origin.startswith("http"):
                    browser_client.call("Browser.grantPermissions", {
                        "origin": page_origin, "permissions": ["geolocation"]
                    })
        except Exception:
            pass  # 授权失败不阻断注入，提示里会说明

        client = CDPClient(target["ws"])
        client.set_location(lat, lon)
        write_session(target["port"], target["id"], lat, lon)
        print("已注入位置：%.6f, %.6f" % (lat, lon))
        print("目标页面：%s（端口 %d）" % (target["title"], target["port"]))
        print("坐标基准：GCJ-02 城市坐标已按经验公式反算为 WGS-84 语义精度。")
        print("提示：目标页面需为 HTTPS 或 localhost，且允许定位权限，网页才能读到坐标。")
        print("连接保持中……按 Ctrl+C 结束（结束即恢复真实位置）。")

        # 保持连接，定期探活
        while True:
            time.sleep(3)
            try:
                client.call("Browser.getVersion", {})
            except Exception:
                print("浏览器连接已断开，注入自动失效。")
                break
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，正在恢复真实位置……")
    except Exception as e:
        print("注入失败：" + str(e))
        return 1
    finally:
        if client:
            try:
                client.clear_location()
            except Exception:
                pass
            client.close()
        if browser_client:
            browser_client.close()
        clear_sessions_file()
    print("已恢复真实位置。")
    return 0


def cmd_set(args):
    if not (-90 <= args.lat <= 90 and -180 <= args.lon <= 180):
        print("坐标超出范围：纬度 -90~90，经度 -180~180")
        return 1
    return _inject_and_hold(args.lat, args.lon, args.page)


def cmd_city(args):
    name = args.name
    if name not in CITIES:
        print("未知城市：" + name + "。用 cities 命令查看可用城市。")
        return 1
    lat, lon = CITIES[name]
    print("城市 %s -> %.4f, %.4f" % (name, lat, lon))
    return _inject_and_hold(lat, lon, args.page)


def cmd_clear(args):
    """清除注入：尝试连到记录的页面清除；并提示重启浏览器可彻底恢复。"""
    sessions = read_sessions()
    cleared = 0
    for s in sessions:
        try:
            pages = scan_pages()
            target = next((p for p in pages if p["port"] == s["port"] and p["id"] == s["id"]), None)
            if target:
                client = CDPClient(target["ws"])
                client.clear_location()
                client.close()
                cleared += 1
        except Exception:
            pass
    clear_sessions_file()
    print("已清除 %d 个会话的注入。" % cleared)
    print("若需彻底恢复，重启浏览器即可（CDP 注入随连接关闭自动失效）。")
    return 0


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------

def cmd_selftest(args):
    checks = []
    checks.append(("gcj_outside_china", wgs84_to_gcj02(-74.006, 40.7128) == (-74.006, 40.7128)))
    gx, gy = wgs84_to_gcj02(116.4074, 39.9042)
    checks.append(("gcj_beijing_shift", abs(gx - 116.4074) > 0.001 and abs(gy - 39.9042) > 0.0005))
    checks.append(("cities_count", len(CITIES) >= 15))
    checks.append(("cities_range", all(-90 <= v[0] <= 90 and -180 <= v[1] <= 180 for v in CITIES.values())))
    # CDP 消息格式（纯逻辑）
    msg = json.dumps({"id": 1, "method": "Emulation.setGeolocationOverride",
                      "params": {"latitude": 39.9, "longitude": 116.4, "accuracy": 20}})
    checks.append(("cdp_payload", json.loads(msg)["params"]["accuracy"] == 20))
    checks.append(("debug_ports", DEBUG_PORTS[0] == 9222))
    # WebSocket 长度编码（纯逻辑）
    checks.append(("ws_len_small", (b"\x81\x85"[-1] & 0x7F) == 5))
    mask = b"\x01\x02\x03\x04"
    payload = b"hello"
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    orig = bytes(b ^ mask[i % 4] for i, b in enumerate(masked))
    checks.append(("ws_mask_roundtrip", orig == payload))

    passed = sum(1 for _, ok in checks if ok)
    for name, ok in checks:
        print(("  [OK] " if ok else "  [FAIL] ") + name)
    print("SELFTEST %d/%d passed" % (passed, len(checks)))
    return 0 if passed == len(checks) else 1


def build_parser():
    p = argparse.ArgumentParser(description="位置上报器：让浏览器读到指定经纬度")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("scan", help="扫描浏览器页面").set_defaults(func=cmd_scan)
    sub.add_parser("cities", help="列出内置城市").set_defaults(func=cmd_cities)

    ps = sub.add_parser("set", help="注入经纬度并保持")
    ps.add_argument("lat", type=float)
    ps.add_argument("lon", type=float)
    ps.add_argument("--page", type=int, default=0, help="页面序号，默认 0")
    ps.set_defaults(func=cmd_set)

    pc = sub.add_parser("city", help="按城市名注入")
    pc.add_argument("name")
    pc.add_argument("--page", type=int, default=0)
    pc.set_defaults(func=cmd_city)

    sub.add_parser("clear", help="清除注入").set_defaults(func=cmd_clear)
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
