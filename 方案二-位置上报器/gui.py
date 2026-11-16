# -*- coding: utf-8 -*-
"""
位置上报器 · 图形界面
依赖：core.py（同目录）+ customtkinter（已内置于 exe）
窗口分区：左侧城市快捷选择 / 右侧坐标与浏览器 / 底部操作与日志
"""

import sys
import threading

import customtkinter as ctk

import core

APP_TITLE = "位置上报器"
FONT = "Microsoft YaHei"


def f(size, weight="normal"):
    return ctk.CTkFont(family=FONT, size=size, weight=weight)


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE + "  v1.0")
        self.geometry("980x660")
        self.minsize(860, 580)
        ctk.set_appearance_mode("Light")
        ctk.set_default_color_theme("blue")

        self.pages = []
        self.injecting = False
        self.client = None
        self.browser_client = None
        self.stop_event = threading.Event()

        self._build()
        self._refresh_cities()
        self._scan_async()

    # ---------------------------------------------------------------- 布局
    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        head = ctk.CTkFrame(self, corner_radius=10, height=56)
        head.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 8))
        head.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(head, text="  " + APP_TITLE, font=f(18, "bold"), anchor="w").grid(
            row=0, column=0, sticky="w", padx=10, pady=10)
        ctk.CTkLabel(head, text="让浏览器读到指定坐标 · 用于地图与定位功能调试",
                     font=f(11), text_color="gray", anchor="e").grid(
            row=0, column=1, sticky="e", padx=14)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=14, pady=4)
        body.grid_columnconfigure(0, weight=1, uniform="c")
        body.grid_columnconfigure(1, weight=1, uniform="c")
        body.grid_rowconfigure(0, weight=1)

        self._build_left(body)
        self._build_right(body)

        foot = ctk.CTkFrame(self, corner_radius=10, height=40)
        foot.grid(row=2, column=0, sticky="ew", padx=14, pady=(8, 14))
        self.status = ctk.CTkLabel(foot, text="就绪", font=f(11), anchor="w")
        self.status.grid(row=0, column=0, sticky="w", padx=14, pady=8)
        ctk.CTkButton(foot, text="重新扫描浏览器", font=f(11), width=130,
                      command=self._scan_async).grid(row=0, column=1, sticky="e", padx=14)

    def _build_left(self, parent):
        left = ctk.CTkFrame(parent, corner_radius=10)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(left, text="快捷城市", font=f(13, "bold")).grid(
            row=0, column=0, sticky="w", padx=14, pady=(12, 6))

        self.city_frame = ctk.CTkScrollableFrame(left, corner_radius=8, height=260)
        self.city_frame.grid(row=1, column=0, sticky="nsew", padx=14, pady=4)
        self.city_frame.grid_columnconfigure(0, weight=1)

        note = ctk.CTkFrame(left, corner_radius=8, fg_color="#fff6e8")
        note.grid(row=2, column=0, sticky="ew", padx=14, pady=(4, 12))
        ctk.CTkLabel(note, text="点城市即填入坐标；再点右侧「开始注入」生效。\n目标网页须为 HTTPS / localhost 且允许定位权限。",
                     font=f(11), justify="left", anchor="w", text_color="#8a5a00").pack(
            anchor="w", padx=12, pady=10)

    def _build_right(self, parent):
        right = ctk.CTkFrame(parent, corner_radius=10)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        right.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(right, text="坐标与目标页面", font=f(13, "bold")).grid(
            row=0, column=0, sticky="w", padx=14, pady=(12, 6))

        coord = ctk.CTkFrame(right, corner_radius=8)
        coord.grid(row=1, column=0, sticky="ew", padx=14, pady=4)
        coord.grid_columnconfigure(1, weight=1)
        coord.grid_columnconfigure(3, weight=1)
        ctk.CTkLabel(coord, text="纬度", font=f(11, "bold")).grid(row=0, column=0, sticky="w", padx=(10, 4))
        self.e_lat = ctk.CTkEntry(coord, placeholder_text="39.9042", font=f(11))
        self.e_lat.grid(row=0, column=1, sticky="ew", padx=4, pady=8)
        ctk.CTkLabel(coord, text="经度", font=f(11, "bold")).grid(row=0, column=2, sticky="w", padx=(10, 4))
        self.e_lon = ctk.CTkEntry(coord, placeholder_text="116.4074", font=f(11))
        self.e_lon.grid(row=0, column=3, sticky="ew", padx=(4, 10), pady=8)

        pg = ctk.CTkFrame(right, corner_radius=8)
        pg.grid(row=2, column=0, sticky="ew", padx=14, pady=8)
        ctk.CTkLabel(pg, text="可注入的浏览器页面", font=f(12, "bold")).grid(
            row=0, column=0, sticky="w", padx=10, pady=(8, 4))
        self.page_box = ctk.CTkTextbox(pg, font=f(10), height=96, wrap="word")
        self.page_box.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        self.page_box.configure(state="disabled")

        self.lbl_state = ctk.CTkLabel(right, text="注入状态：未注入", font=f(12), anchor="w")
        self.lbl_state.grid(row=3, column=0, sticky="w", padx=14, pady=(4, 2))

        ops = ctk.CTkFrame(right, fg_color="transparent")
        ops.grid(row=4, column=0, sticky="ew", padx=14, pady=(8, 12))
        self.btn_inject = ctk.CTkButton(ops, text="开始注入", font=f(12, "bold"),
                                        height=38, command=self._toggle_inject)
        self.btn_inject.pack(side="left", fill="x", expand=True)

    # ---------------------------------------------------------------- 逻辑
    def _log(self, text):
        self.status.configure(text=text)

    def _refresh_cities(self):
        for w in self.city_frame.winfo_children():
            w.destroy()
        for name, (lat, lon) in core.CITIES.items():
            row = ctk.CTkFrame(self.city_frame, corner_radius=6)
            row.pack(fill="x", padx=6, pady=2)
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(row, text=name, font=f(11, "bold"), anchor="w").grid(
                row=0, column=0, sticky="w", padx=10, pady=6)
            ctk.CTkLabel(row, text="%.3f, %.3f" % (lat, lon), font=f(10),
                         anchor="w", text_color="gray").grid(row=0, column=1, padx=6)
            ctk.CTkButton(row, text="填入", font=f(10), width=52,
                          command=lambda a=lat, b=lon: self._fill(a, b)).grid(row=0, column=2, padx=8)

    def _fill(self, lat, lon):
        self.e_lat.delete(0, "end")
        self.e_lat.insert(0, "%.6f" % lat)
        self.e_lon.delete(0, "end")
        self.e_lon.insert(0, "%.6f" % lon)
        self._log("已填入坐标：%.4f, %.4f" % (lat, lon))

    def _scan_async(self):
        self._log("正在扫描浏览器调试端口…")

        def work():
            try:
                pages = core.scan_pages()
                self.after(0, lambda: self._apply_pages(pages))
            except Exception as e:
                self.after(0, lambda: self._log("扫描失败：" + str(e)))

        threading.Thread(target=work, daemon=True).start()

    def _apply_pages(self, pages):
        self.pages = pages
        self.page_box.configure(state="normal")
        self.page_box.delete("1.0", "end")
        if not pages:
            self.page_box.insert("1.0", "未发现浏览器页面。\n请先用 --remote-debugging-port=9222 启动 Chrome / Edge。")
            self._log("未发现可注入页面")
        else:
            for i, p in enumerate(pages):
                self.page_box.insert("end", "[%d] 端口%d  %s\n    %s\n" % (
                    i, p["port"], p["title"][:40], p["url"][:70]))
        self.page_box.configure(state="disabled")
        if not self.injecting:
            self._log("扫描完成：发现 %d 个页面" % len(pages))

    def _toggle_inject(self):
        if self.injecting:
            self._stop_inject()
        else:
            self._start_inject()

    def _start_inject(self):
        try:
            lat = float(self.e_lat.get().strip())
            lon = float(self.e_lon.get().strip())
        except ValueError:
            self._log("请先填入数字坐标（或点左侧城市）")
            return
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            self._log("坐标超出范围")
            return
        if not self.pages:
            self._log("没有可注入页面，请先扫描")
            return

        self.stop_event.clear()
        self.injecting = True
        self.btn_inject.configure(text="停止注入", fg_color="#8a1c12", hover_color="#a52a1c")
        self.lbl_state.configure(text="注入状态：注入中…")
        self._log("正在注入 %.4f, %.4f …" % (lat, lon))

        target = self.pages[0]

        def work():
            try:
                # 尽力授权
                try:
                    ver = core.http_json("http://127.0.0.1:%d/json/version" % target["port"])
                    if ver.get("webSocketDebuggerUrl"):
                        self.browser_client = core.CDPClient(ver["webSocketDebuggerUrl"])
                        from urllib.parse import urlparse
                        u = urlparse(target["url"])
                        origin = "%s://%s" % (u.scheme, u.netloc)
                        if origin.startswith("http"):
                            self.browser_client.call("Browser.grantPermissions", {
                                "origin": origin, "permissions": ["geolocation"]})
                except Exception:
                    pass

                self.client = core.CDPClient(target["ws"])
                self.client.set_location(lat, lon)
                self.after(0, lambda: self._log("已注入，保持连接中；目标页需 HTTPS 且允许定位"))

                # 保活循环，直到用户点停止
                while not self.stop_event.wait(2):
                    try:
                        self.client.call("Browser.getVersion", {})
                    except Exception:
                        break
            except Exception as e:
                self.after(0, lambda: self._log("注入失败：" + str(e)))
            finally:
                self.after(0, self._stop_inject)

        threading.Thread(target=work, daemon=True).start()

    def _stop_inject(self):
        self.stop_event.set()
        if self.client:
            try:
                self.client.clear_location()
            except Exception:
                pass
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None
        if self.browser_client:
            try:
                self.browser_client.close()
            except Exception:
                pass
            self.browser_client = None
        self.injecting = False
        try:
            core.clear_sessions_file()
        except Exception:
            pass
        self.btn_inject.configure(text="开始注入", fg_color=["#3B8ED0", "#1F6AA5"],
                                  hover_color=["#36719F", "#144870"])
        self.lbl_state.configure(text="注入状态：未注入")
        self._log("已停止注入，浏览器位置恢复真实值")

    def on_close(self):
        self._stop_inject()
        self.destroy()


def main():
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
