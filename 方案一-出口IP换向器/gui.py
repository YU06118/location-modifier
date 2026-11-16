# -*- coding: utf-8 -*-
"""
出口 IP 换向器 · 图形界面
依赖：core.py（同目录）+ customtkinter（已内置于 exe）
窗口分区：左侧节点管理 / 右侧当前出口状态 / 底部操作与日志
"""

import os
import sys
import threading

import customtkinter as ctk

import core

APP_TITLE = "出口 IP 换向器"
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

        self.config = core.load_config()
        self.selected_id = None
        self.busy = False

        self._build()
        self._refresh_nodes()
        self._refresh_status_async()

    # ---------------------------------------------------------------- 布局
    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        head = ctk.CTkFrame(self, corner_radius=10, height=56)
        head.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 8))
        head.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(head, text="  " + APP_TITLE, font=f(18, "bold"), anchor="w").grid(
            row=0, column=0, sticky="w", padx=10, pady=10)
        ctk.CTkLabel(head, text="切换网络出口 · 让网站看到另一地区的公网 IP",
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
        ctk.CTkButton(foot, text="查询当前出口", font=f(11), width=120,
                      command=self._refresh_status_async).grid(row=0, column=1, sticky="e", padx=14)

    def _build_left(self, parent):
        left = ctk.CTkFrame(parent, corner_radius=10)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(left, text="节点列表", font=f(13, "bold")).grid(
            row=0, column=0, sticky="w", padx=14, pady=(12, 6))

        # 节点列表容器
        self.list_frame = ctk.CTkScrollableFrame(left, corner_radius=8, height=200)
        self.list_frame.grid(row=2, column=0, sticky="nsew", padx=14, pady=4)
        self.list_frame.grid_columnconfigure(0, weight=1)

        # 添加节点表单
        form = ctk.CTkFrame(left, corner_radius=8)
        form.grid(row=3, column=0, sticky="ew", padx=14, pady=(4, 12))
        form.grid_columnconfigure(1, weight=1)
        form.grid_columnconfigure(3, weight=1)

        self.e_name = ctk.CTkEntry(form, placeholder_text="名称", font=f(11))
        self.e_name.grid(row=0, column=0, sticky="ew", padx=(10, 4), pady=(10, 4))
        self.e_region = ctk.CTkEntry(form, placeholder_text="地区", font=f(11))
        self.e_region.grid(row=0, column=1, sticky="ew", padx=4, pady=(10, 4))
        self.e_proto = ctk.CTkOptionMenu(form, values=["http", "socks5"], font=f(11))
        self.e_proto.grid(row=0, column=2, padx=4, pady=(10, 4))
        self.e_port = ctk.CTkEntry(form, placeholder_text="端口", font=f(11), width=70)
        self.e_port.grid(row=0, column=3, sticky="ew", padx=4, pady=(10, 4))
        self.e_host = ctk.CTkEntry(form, placeholder_text="主机 IP 或域名", font=f(11))
        self.e_host.grid(row=1, column=0, columnspan=4, sticky="ew", padx=10, pady=4)
        ctk.CTkButton(form, text="添加节点", font=f(11), command=self._add_node).grid(
            row=2, column=0, columnspan=4, sticky="ew", padx=10, pady=(4, 10))

    def _build_right(self, parent):
        right = ctk.CTkFrame(parent, corner_radius=10)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        right.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(right, text="当前出口状态", font=f(13, "bold")).grid(
            row=0, column=0, sticky="w", padx=14, pady=(12, 6))

        card = ctk.CTkFrame(right, corner_radius=8)
        card.grid(row=1, column=0, sticky="ew", padx=14, pady=4)
        card.grid_columnconfigure(1, weight=1)
        self.lbl_proxy = ctk.CTkLabel(card, text="系统代理：—", font=f(12), anchor="w")
        self.lbl_proxy.grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(10, 4))
        self.lbl_addr = ctk.CTkLabel(card, text="代理地址：—", font=f(11), anchor="w", text_color="gray")
        self.lbl_addr.grid(row=1, column=0, columnspan=2, sticky="w", padx=12, pady=2)
        self.lbl_ip = ctk.CTkLabel(card, text="出口 IP：查询中…", font=f(12), anchor="w")
        self.lbl_ip.grid(row=2, column=0, columnspan=2, sticky="w", padx=12, pady=(4, 4))
        self.lbl_region = ctk.CTkLabel(card, text="归属地：—", font=f(11), anchor="w", text_color="gray")
        self.lbl_region.grid(row=3, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 10))

        note = ctk.CTkFrame(right, corner_radius=8, fg_color="#fff6e8")
        note.grid(row=2, column=0, sticky="ew", padx=14, pady=8)
        ctk.CTkLabel(note, text="提示：本工具不提供代理节点，请添加你自己合法可用的代理。\n切换会真实改写系统代理，请确认节点可用后操作。",
                     font=f(11), justify="left", anchor="w", text_color="#8a5a00").pack(
            anchor="w", padx=12, pady=10)

        ops = ctk.CTkFrame(right, fg_color="transparent")
        ops.grid(row=3, column=0, sticky="ew", padx=14, pady=(4, 12))
        self.btn_use = ctk.CTkButton(ops, text="切换到选中节点", font=f(12, "bold"),
                                     height=38, command=self._use_selected)
        self.btn_use.pack(side="left", fill="x", expand=True)
        self.btn_off = ctk.CTkButton(ops, text="恢复直连", font=f(12), height=38,
                                     fg_color="gray", hover_color="darkgray", command=self._off)
        self.btn_off.pack(side="left", fill="x", expand=True, padx=(8, 0))

    # ---------------------------------------------------------------- 逻辑
    def _log(self, text):
        self.status.configure(text=text)

    def _refresh_nodes(self):
        for w in self.list_frame.winfo_children():
            w.destroy()
        nodes = self.config.get("nodes", [])
        if not nodes:
            ctk.CTkLabel(self.list_frame, text="暂无节点，请在下方添加",
                         font=f(11), text_color="gray").pack(pady=18)
            return
        active = self.config.get("active")
        for i, node in enumerate(nodes):
            row = ctk.CTkFrame(self.list_frame, corner_radius=6)
            row.pack(fill="x", padx=6, pady=4)
            row.grid_columnconfigure(0, weight=1)
            mark = "● " if node["name"] == active else "○ "
            ctk.CTkLabel(row, text=mark + node["name"] + "  " + node["region"],
                         font=f(12, "bold"), anchor="w").grid(row=0, column=0, sticky="w", padx=10, pady=(8, 2))
            ctk.CTkLabel(row, text="%s://%s:%d" % (node["protocol"], node["host"], node["port"]),
                         font=f(10), anchor="w", text_color="gray").grid(row=1, column=0, sticky="w", padx=10, pady=(0, 4))
            ctk.CTkButton(row, text="选择", font=f(10), width=52,
                          command=lambda n=node["name"]: self._select(n)).grid(row=0, column=1, rowspan=2, padx=8)

    def _select(self, name):
        self.selected_id = name
        self._log("已选中节点：" + name + "，点「切换到选中节点」生效")

    def _add_node(self):
        name = self.e_name.get().strip()
        host = self.e_host.get().strip()
        port = self.e_port.get().strip()
        if not (name and host and port.isdigit()):
            self._log("请填完整：名称、主机、端口（端口须为数字）")
            return
        if core.find_node(self.config, name):
            self._log("节点已存在：" + name)
            return
        self.config["nodes"].append({
            "name": name, "region": self.e_region.get().strip() or "未命名地区",
            "protocol": self.e_proto.get(), "host": host, "port": int(port)
        })
        core.save_config(self.config)
        self._refresh_nodes()
        for e in (self.e_name, self.e_region, self.e_host, self.e_port):
            e.delete(0, "end")
        self._log("已添加节点：" + name)

    def _set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.btn_use.configure(state=state)
        self.btn_off.configure(state=state)

    def _refresh_status_async(self):
        if self.busy:
            return
        self._set_busy(True)
        self.lbl_ip.configure(text="出口 IP：查询中…")
        self._log("正在查询出口 IP…")

        def work():
            try:
                state = core.win_read_state() if core.IS_WINDOWS else {"ProxyEnable": 0, "ProxyServer": ""}
                result = core.query_exit_ip()
                self.after(0, lambda: self._apply_status(state, result))
            except Exception as e:
                self.after(0, lambda: (self._log("查询失败：" + str(e)), self._set_busy(False)))

        threading.Thread(target=work, daemon=True).start()

    def _apply_status(self, state, result):
        self.lbl_proxy.configure(text="系统代理：" + ("开启" if state.get("ProxyEnable") else "关闭"))
        self.lbl_addr.configure(text="代理地址：" + (state.get("ProxyServer") or "无"))
        if result.get("error"):
            self.lbl_ip.configure(text="出口 IP：查询失败")
            self.lbl_region.configure(text=result["error"])
        else:
            self.lbl_ip.configure(text="出口 IP：" + result["ip"])
            self.lbl_region.configure(text="归属地：" + result["region"])
        self._set_busy(False)
        self._log("状态已刷新")

    def _use_selected(self):
        if not self.selected_id:
            self._log("请先在左侧节点列表点「选择」")
            return
        self._set_busy(True)
        self._log("正在切换出口到 " + self.selected_id + " …")

        def work():
            try:
                node = core.find_node(self.config, self.selected_id)
                if core.IS_WINDOWS:
                    if self.config.get("backup") is None:
                        self.config["backup"] = core.win_read_state()
                    core.win_write_state(True, core.build_windows_proxy_string(node),
                                         "<local>;localhost;127.0.0.1;*.local")
                elif core.IS_MAC:
                    core.mac_set_proxy(node)
                else:
                    raise RuntimeError("不支持的系统")
                self.config["active"] = self.selected_id
                core.save_config(self.config)
                result = core.query_exit_ip()
                self.after(0, lambda: (self._apply_status(
                    core.win_read_state() if core.IS_WINDOWS else {"ProxyEnable": 1, "ProxyServer": node["host"]},
                    result), self._refresh_nodes(), self._log("已切换到 " + self.selected_id)))
            except Exception as e:
                self.after(0, lambda: (self._log("切换失败：" + str(e)), self._set_busy(False)))

        threading.Thread(target=work, daemon=True).start()

    def _off(self):
        self._set_busy(True)
        self._log("正在恢复直连…")

        def work():
            try:
                if core.IS_WINDOWS:
                    backup = self.config.get("backup")
                    if backup:
                        core.win_write_state(backup["ProxyEnable"], backup["ProxyServer"], backup["ProxyOverride"])
                    else:
                        core.win_write_state(False, "")
                elif core.IS_MAC:
                    core.mac_clear_proxy()
                self.config["active"] = None
                core.save_config(self.config)
                result = core.query_exit_ip()
                self.after(0, lambda: (self._apply_status(
                    core.win_read_state() if core.IS_WINDOWS else {"ProxyEnable": 0, "ProxyServer": ""},
                    result), self._refresh_nodes(), self._log("已恢复直连")))
            except Exception as e:
                self.after(0, lambda: (self._log("恢复失败：" + str(e)), self._set_busy(False)))

        threading.Thread(target=work, daemon=True).start()


def main():
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
