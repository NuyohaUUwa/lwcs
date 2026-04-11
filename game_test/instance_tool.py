"""
多实例工具：新建 / 启动 / 停止 / 删除 / 命名；启动后显示可点击的完整地址，状态带颜色区分。

新建时复用 data 下已有数据：优先从 accounts 模板目录复制，并补拷 data 根目录下的实例 JSON、
必要时从根目录补全 shared/；删除实例时同时删除 accounts/<slug> 目录。

配置为 data/instances.json：首次在界面点「新建」会自动创建，无需复制任何模板文件。

用法（在 game_test 目录下）:
  python instance_tool.py           图形界面（默认）
  python instance_tool.py gui     同上
  python instance_tool.py cli     命令行：按 instances.json 启动全部子进程
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk
from typing import Any
from urllib.parse import urlparse

_SLUG_OK = re.compile(r"^[a-zA-Z0-9_-]+$")
_BASE_PORT = 9510
_LAUNCH_HOST = "127.0.0.1"

_INSTANCE_JSON_NAMES = (
    "quick_logins.json",
    "buy_items.json",
    "auto_use_rules.json",
    "annotations.json",
)
_SHARED_JSON_NAMES = (
    "monsters.json",
    "fingerprints.json",
    "teleport_destination.json",
)


def game_test_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def data_root_path() -> str:
    return os.path.normpath(os.path.abspath(os.path.join(game_test_dir(), "data")))


def instances_json_path() -> str:
    return os.path.join(game_test_dir(), "data", "instances.json")


def account_dir(data_root: str, slug: str) -> str:
    return os.path.join(data_root, "accounts", slug)


def shared_dir(data_root: str) -> str:
    return os.path.join(data_root, "shared")


def launch_url(port: int, host: str = _LAUNCH_HOST) -> str:
    return f"http://{host}:{int(port)}/"


def port_from_launch_url(text: Any) -> int | None:
    """从完整 URL 或纯数字字符串解析端口。"""
    if text is None:
        return None
    if isinstance(text, int):
        return text if 1 <= text <= 65535 else None
    s = str(text).strip()
    if s.isdigit():
        p = int(s)
        return p if 1 <= p <= 65535 else None
    if "://" in s:
        try:
            u = urlparse(s)
            if u.port is not None:
                p = int(u.port)
                return p if 1 <= p <= 65535 else None
        except ValueError:
            pass
    m = re.search(r":(\d{2,5})(?:/|\s*$)", s)
    if m:
        p = int(m.group(1))
        return p if 1 <= p <= 65535 else None
    return None


def row_tree_iid(port: int) -> str:
    """Treeview 行 iid，与端口一一对应（不依赖「启动地址」列是否为空）。"""
    return f"r{int(port)}"


def port_from_tree_iid(iid: str) -> int | None:
    s = str(iid)
    if len(s) >= 2 and s[0] == "r" and s[1:].isdigit():
        p = int(s[1:])
        if 1 <= p <= 65535:
            return p
    return None


def status_row_tag(status_text: str) -> str:
    if status_text.startswith("运行中(本工具"):
        return "t_run_self"
    if status_text.startswith("运行中(端口"):
        return "t_run_ext"
    if status_text.startswith("已退出"):
        return "t_exit"
    return "t_idle"


def _dir_has_files(path: str) -> bool:
    if not os.path.isdir(path):
        return False
    try:
        return len(os.listdir(path)) > 0
    except OSError:
        return False


def template_account_dir(data_root: str, exclude_slug: str | None = None) -> str | None:
    acc_root = os.path.join(data_root, "accounts")
    if not os.path.isdir(acc_root):
        return None
    default = os.path.join(acc_root, "default")
    if exclude_slug != "default" and _dir_has_files(default):
        return default
    for name in sorted(os.listdir(acc_root)):
        if name == exclude_slug:
            continue
        p = os.path.join(acc_root, name)
        if os.path.isdir(p) and _dir_has_files(p):
            return p
    return None


def _copy2_if_exists(src: str, dst: str) -> bool:
    if os.path.isfile(src):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        return True
    return False


def ensure_shared_from_root(data_root: str) -> None:
    """若 shared 中缺少公共 JSON，从 data 根目录复制（沿用旧扁平布局文件）。"""
    sh = shared_dir(data_root)
    os.makedirs(sh, exist_ok=True)
    for name in _SHARED_JSON_NAMES:
        root_f = os.path.join(data_root, name)
        sh_f = os.path.join(sh, name)
        if os.path.isfile(root_f) and not os.path.isfile(sh_f):
            shutil.copy2(root_f, sh_f)


def _overlay_instance_json_from_data_root(data_root: str, dst_account: str) -> int:
    """把 data 根目录下的实例级 JSON 覆盖复制到账号目录，返回拷贝文件数。"""
    n = 0
    for name in _INSTANCE_JSON_NAMES:
        src = os.path.join(data_root, name)
        dst = os.path.join(dst_account, name)
        if _copy2_if_exists(src, dst):
            n += 1
    return n


def _ensure_empty_packet_logs(dst_account: str) -> None:
    """实例目录下 packet_logs 仅保留空目录，不沿用模板或根目录下的历史报文文件。"""
    pl = os.path.join(dst_account, "packet_logs")
    if os.path.isdir(pl):
        shutil.rmtree(pl, ignore_errors=True)
    os.makedirs(pl, exist_ok=True)


def seed_new_instance_data(data_root: str, new_slug: str) -> tuple[bool, str]:
    """
    创建 accounts/<new_slug> 并尽可能复用 data 下已有文件。
    """
    ensure_shared_from_root(data_root)

    dst = account_dir(data_root, new_slug)
    if os.path.isdir(dst):
        n_json = _overlay_instance_json_from_data_root(data_root, dst)
        msg = "目录已存在，已从 data 根目录补拷实例配置" if n_json else "目录已存在"
        if n_json:
            msg += f"（{n_json} 个文件）"
        return True, msg
    src = template_account_dir(data_root, exclude_slug=new_slug)

    parts: list[str] = []
    try:
        if src and os.path.isdir(src):
            shutil.copytree(src, dst, symlinks=False)
            parts.append(f"已从 accounts/{os.path.basename(src)} 复制")
        else:
            os.makedirs(dst, exist_ok=True)
            parts.append("已创建空账号目录")

        n_json = _overlay_instance_json_from_data_root(data_root, dst)
        if n_json:
            parts.append(f"从 data 根目录补拷实例配置 {n_json} 个文件")

        _ensure_empty_packet_logs(dst)
        parts.append("packet_logs 已置为空目录")

        return True, "；".join(parts)
    except OSError as e:
        return False, str(e)


def delete_account_folder(data_root: str, slug: str) -> None:
    path = account_dir(data_root, slug)
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)


def move_account_dir(data_root: str, old_slug: str, new_slug: str) -> str | None:
    if old_slug == new_slug:
        return None
    if not _SLUG_OK.match(new_slug):
        return "slug 仅允许字母、数字、下划线、连字符"
    old_p = account_dir(data_root, old_slug)
    new_p = account_dir(data_root, new_slug)
    if not os.path.isdir(old_p):
        return None
    if os.path.exists(new_p):
        return f"目录已存在: accounts/{new_slug}"
    try:
        shutil.move(old_p, new_p)
    except OSError as e:
        return str(e)
    return None


def load_instances_from_path(path: str) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        root = json.load(f)
    if not isinstance(root, dict):
        raise ValueError("instances.json 根对象必须是 JSON 对象")
    items = root.get("instances")
    if not isinstance(items, list):
        raise ValueError("instances.json 必须包含 instances 数组")
    return [x for x in items if isinstance(x, dict)]


def validate_row(index: int, row: dict[str, Any]) -> str | None:
    slug = str(row.get("slug", "")).strip()
    label = row.get("label")
    if label is None or not isinstance(label, str):
        return f"第 {index} 条缺少必填 label（可为空字符串）"
    port = port_from_launch_url(row.get("port"))
    if port is None:
        return f"第 {index} 条无法解析启动地址中的端口: {row.get('port')!r}"
    if not slug or not _SLUG_OK.match(slug):
        return f"第 {index} 条 slug 非法（仅允许 [a-zA-Z0-9_-]+）: {slug!r}"
    return None


def validate_all(rows: list[dict[str, Any]]) -> str | None:
    for i, row in enumerate(rows):
        err = validate_row(i, row)
        if err:
            return err
    ports = [port_from_launch_url(r.get("port")) for r in rows]
    if any(p is None for p in ports):
        return "存在无效端口"
    if len(ports) != len(set(ports)):
        return "存在重复端口，请删除多余项后重试"
    slugs = [str(row.get("slug", "")).strip() for row in rows]
    if len(slugs) != len(set(slugs)):
        return "存在重复 slug，请删除多余项后重试"
    return None


def launch_instances(rows: list[dict[str, Any]]) -> tuple[dict[int, subprocess.Popen], list[str]]:
    base = game_test_dir()
    data_root = os.path.abspath(os.path.join(base, "data"))
    run_py = os.path.join(base, "run.py")
    by_port: dict[int, subprocess.Popen] = {}
    lines: list[str] = []

    for row in rows:
        slug = str(row.get("slug", "")).strip()
        label = str(row.get("label", ""))
        port = port_from_launch_url(row.get("port"))
        assert port is not None
        env = os.environ.copy()
        env["LWCS_API_PORT"] = str(port)
        env["LWCS_INSTANCE_SLUG"] = slug
        env["LWCS_DATA_ROOT"] = data_root

        proc = subprocess.Popen(
            [sys.executable, run_py],
            cwd=base,
            env=env,
        )
        by_port[port] = proc
        url = launch_url(port)
        disp = label.strip() if label.strip() else slug
        lines.append(f"{disp} -> {url}")

    lines.append(f"[instance_tool] 已启动 {len(by_port)} 个子进程。")
    return by_port, lines


def _port_listening(host: str, port: int, timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _rows_from_tree(tree: ttk.Treeview) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for iid in tree.get_children():
        port = port_from_tree_iid(str(iid))
        if port is None:
            continue
        vals = tree.item(iid, "values")
        slug = str(vals[0]).strip() if len(vals) > 0 else ""
        label = str(vals[1]) if len(vals) > 1 and vals[1] is not None else ""
        rows.append({"slug": slug, "port": port, "label": label})
    return rows


def _load_tree(tree: ttk.Treeview, rows: list[dict[str, Any]]) -> None:
    for i in tree.get_children():
        tree.delete(i)
    for row in rows:
        p = port_from_launch_url(row.get("port"))
        if p is None:
            try:
                p = int(row.get("port"))
            except (TypeError, ValueError):
                p = _BASE_PORT
        slug = row.get("slug", "")
        label = row.get("label", "") if row.get("label") is not None else ""
        listening = _port_listening(_LAUNCH_HOST, p)
        url_col = launch_url(p) if listening else ""
        st = "运行中(端口占用)" if listening else "未启动"
        tag = status_row_tag(st)
        tree.insert(
            "",
            "end",
            iid=row_tree_iid(p),
            values=(slug, label, url_col, st),
            tags=(tag,),
        )


def _normalize_rows(raw: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    out: list[dict[str, Any]] = []
    for row in raw:
        port = port_from_launch_url(row.get("port"))
        if port is None:
            return None
        label = row.get("label", "")
        if not isinstance(label, str):
            label = str(label)
        out.append({"slug": str(row["slug"]).strip(), "port": port, "label": label})
    return out


def _allocate_next(rows: list[dict[str, Any]]) -> tuple[str, int, str]:
    used_slugs = {str(r.get("slug", "")).strip() for r in rows}
    used_ports: set[int] = set()
    for r in rows:
        p = port_from_launch_url(r.get("port"))
        if p is not None:
            used_ports.add(p)

    n = 1
    while True:
        slug = f"slot{n}"
        if slug not in used_slugs:
            break
        n += 1

    port = _BASE_PORT
    while port in used_ports:
        port += 1

    label = f"实例{n}"
    return slug, port, label


def _save_instances(path: str, rows: list[dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"instances": rows}, f, ensure_ascii=False, indent=2)


class MultiLaunchApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("LWCS 多开")
        self.geometry("720x420")
        self.minsize(560, 340)

        self._path = instances_json_path()
        self._procs: dict[int, subprocess.Popen] = {}
        self._data_root = data_root_path()

        bar = ttk.Frame(self, padding=10)
        bar.pack(fill=tk.X)
        ttk.Button(bar, text="新建", command=self._on_new).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(bar, text="启动", command=self._on_start).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(bar, text="停止", command=self._on_stop).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(bar, text="命名", command=self._on_rename).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(bar, text="删除", command=self._on_delete).pack(side=tk.LEFT)

        list_frame = ttk.LabelFrame(self, text="实例（启动后显示地址，点击地址在浏览器打开）", padding=6)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 6))

        cols = ("slug", "name", "url", "status")
        self._tree = ttk.Treeview(list_frame, columns=cols, show="headings", height=9, selectmode="browse")
        self._tree.heading("slug", text="目录 slug")
        self._tree.heading("name", text="名称")
        self._tree.heading("url", text="启动地址")
        self._tree.heading("status", text="状态")
        self._tree.column("slug", width=100)
        self._tree.column("name", width=120)
        self._tree.column("url", width=260)
        self._tree.column("status", width=120)
        sb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=sb.set, cursor="")
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._tree.tag_configure("t_run_self", background="#c8e6c9", foreground="#1b5e20")
        self._tree.tag_configure("t_run_ext", background="#fff9c4", foreground="#e65100")
        self._tree.tag_configure("t_exit", background="#eeeeee", foreground="#616161")
        self._tree.tag_configure("t_idle", background="", foreground="")
        self._tree.bind("<Button-1>", self._on_tree_click, add=True)
        self._tree.bind("<Motion>", self._on_tree_motion)

        self._status = tk.StringVar(value="")
        ttk.Label(self, textvariable=self._status, foreground="#444", wraplength=680).pack(
            fill=tk.X, padx=10, pady=(0, 10)
        )

        self._reload_from_disk()
        self._schedule_status_refresh()

    def _tree_port(self, iid: str) -> int | None:
        return port_from_tree_iid(str(iid))

    def _on_tree_motion(self, event: tk.Event) -> None:
        col = self._tree.identify_column(event.x)
        if col == "#3":
            self._tree.config(cursor="hand2")
        else:
            self._tree.config(cursor="")

    def _on_tree_click(self, event: tk.Event) -> None:
        col = self._tree.identify_column(event.x)
        row = self._tree.identify_row(event.y)
        if col != "#3" or not row:
            return
        vals = self._tree.item(row, "values")
        if len(vals) < 3:
            return
        url = str(vals[2]).strip()
        if url.startswith("http://") or url.startswith("https://"):
            webbrowser.open(url)

    def _schedule_status_refresh(self) -> None:
        self._refresh_row_display()
        self.after(700, self._schedule_status_refresh)

    def _compute_status_text(self, port: int) -> str:
        port = int(port)
        proc = self._procs.get(port)
        if proc is not None:
            code = proc.poll()
            if code is None:
                return "运行中(本工具)"
            if code == 0:
                return "已退出(0)"
            return f"已退出({code})"
        if _port_listening(_LAUNCH_HOST, port):
            return "运行中(端口占用)"
        return "未启动"

    def _refresh_row_display(self) -> None:
        for iid in self._tree.get_children():
            port = self._tree_port(iid)
            vals = list(self._tree.item(iid, "values"))
            if port is None or len(vals) < 2:
                continue
            proc = self._procs.get(port)
            if proc is not None and proc.poll() is not None:
                del self._procs[port]
            st = self._compute_status_text(port)
            proc_alive = port in self._procs and self._procs[port].poll() is None
            listening = _port_listening(_LAUNCH_HOST, port)
            url_text = launch_url(port) if (proc_alive or listening) else ""
            while len(vals) < 4:
                vals.append("")
            vals[2] = url_text
            vals[3] = st
            tag = status_row_tag(st)
            self._tree.item(iid, values=tuple(vals[:4]), tags=(tag,))

    def _reload_from_disk(self) -> None:
        path = self._path
        if not os.path.isfile(path):
            _load_tree(self._tree, [])
            self._status.set("尚无 instances.json，点「新建」将自动创建。")
            return
        try:
            rows = load_instances_from_path(path)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            messagebox.showerror("读取失败", str(e))
            rows = []
        _load_tree(self._tree, rows)
        self._status.set(f"已加载 {len(rows)} 条 · {path}")

    def _persist_tree(self) -> bool:
        raw = _rows_from_tree(self._tree)
        norm = _normalize_rows(raw)
        if norm is None:
            messagebox.showerror("错误", "列表数据异常，无法解析端口")
            return False
        err = validate_all(norm)
        if err:
            messagebox.showerror("校验失败", err)
            return False
        try:
            _save_instances(self._path, norm)
        except OSError as e:
            messagebox.showerror("保存失败", str(e))
            return False
        self._status.set(f"已保存 {len(norm)} 条")
        return True

    def _on_new(self) -> None:
        rows = _rows_from_tree(self._tree)
        norm = _normalize_rows(rows)
        if norm is None:
            messagebox.showerror("错误", "当前列表中存在非法端口，请先删除异常行。")
            return
        slug, port, label = _allocate_next(norm)
        ok, msg = seed_new_instance_data(self._data_root, slug)
        if not ok:
            messagebox.showerror("复制数据失败", msg)
            return
        self._tree.insert(
            "",
            "end",
            iid=row_tree_iid(port),
            values=(slug, label, "", "—"),
            tags=("t_idle",),
        )
        if self._persist_tree():
            self._status.set(f"已新建 {slug}，端口 {port}（启动后将显示地址）。{msg}")

    def _on_start(self) -> None:
        raw = _rows_from_tree(self._tree)
        norm = _normalize_rows(raw)
        if norm is None:
            messagebox.showerror("错误", "列表数据异常，无法解析端口")
            return
        if not norm:
            messagebox.showwarning("提示", "列表为空，请先点「新建」。")
            return
        err = validate_all(norm)
        if err:
            messagebox.showerror("校验失败", err)
            return
        if not self._persist_tree():
            return
        for row in norm:
            port = int(row["port"])
            p = self._procs.get(port)
            if p is not None and p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=2.5)
                except subprocess.TimeoutExpired:
                    p.kill()
            self._procs.pop(port, None)
        try:
            by_port, lines = launch_instances(norm)
        except Exception as e:
            messagebox.showerror("启动失败", str(e))
            return
        self._procs.update(by_port)
        self._status.set(" · ".join(lines))

    def _on_stop(self) -> None:
        sel = self._tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选中要停止的一行。")
            return
        port = self._tree_port(sel[0])
        if port is None:
            messagebox.showerror("错误", "无法解析该行端口")
            return
        proc = self._procs.get(port)
        if proc is None or proc.poll() is not None:
            messagebox.showinfo("提示", "本工具未持有该端口的运行中进程（可能已退出或未通过此处启动）。")
            if proc is not None:
                self._procs.pop(port, None)
            return
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        self._procs.pop(port, None)
        self._status.set(f"已停止 {launch_url(port)}")

    def _on_rename(self) -> None:
        sel = self._tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选中一行。")
            return
        iid = sel[0]
        vals = list(self._tree.item(iid, "values"))
        if len(vals) < 3:
            return
        old_slug = str(vals[0]).strip()
        old_label = str(vals[1]) if vals[1] is not None else ""
        port = port_from_tree_iid(str(iid))

        dlg = tk.Toplevel(self)
        dlg.title("命名")
        dlg.transient(self)
        dlg.grab_set()
        ttk.Label(dlg, text="目录 slug（对应 data/accounts/ 下文件夹名）").grid(row=0, column=0, columnspan=2, sticky=tk.W, padx=10, pady=(10, 4))
        e_slug = ttk.Entry(dlg, width=32)
        e_slug.insert(0, old_slug)
        e_slug.grid(row=1, column=0, columnspan=2, sticky=tk.EW, padx=10)
        ttk.Label(dlg, text="显示名").grid(row=2, column=0, columnspan=2, sticky=tk.W, padx=10, pady=(10, 4))
        e_label = ttk.Entry(dlg, width=32)
        e_label.insert(0, old_label)
        e_label.grid(row=3, column=0, columnspan=2, sticky=tk.EW, padx=10)
        dlg.columnconfigure(0, weight=1)

        def on_ok() -> None:
            new_slug = e_slug.get().strip()
            new_label = e_label.get()
            if not new_slug or not _SLUG_OK.match(new_slug):
                messagebox.showerror("错误", "slug 不能为空且仅允许字母、数字、下划线、连字符", parent=dlg)
                return
            if new_slug != old_slug:
                err = move_account_dir(self._data_root, old_slug, new_slug)
                if err:
                    messagebox.showerror("重命名目录失败", err, parent=dlg)
                    return
            vals[0] = new_slug
            vals[1] = new_label
            while len(vals) < 4:
                vals.append("")
            self._tree.item(iid, values=tuple(vals[:4]))
            dlg.destroy()
            if not self._persist_tree():
                return
            self._status.set(f"已更新命名: {new_slug}")

        def on_cancel() -> None:
            dlg.destroy()

        bf = ttk.Frame(dlg, padding=(10, 14))
        bf.grid(row=4, column=0, columnspan=2)
        ttk.Button(bf, text="确定", command=on_ok).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(bf, text="取消", command=on_cancel).pack(side=tk.LEFT)

    def _on_delete(self) -> None:
        sel = self._tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先在列表中选中一行再删除。")
            return
        for iid in sel:
            vals = self._tree.item(iid, "values")
            slug = str(vals[0]).strip() if vals else ""
            port = self._tree_port(iid)
            if port is not None:
                p = self._procs.pop(port, None)
                if p is not None and p.poll() is None:
                    p.terminate()
                    try:
                        p.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        p.kill()
            if slug:
                delete_account_folder(self._data_root, slug)
            self._tree.delete(iid)
        if self._persist_tree():
            self._status.set("已删除选中项、对应目录及配置")


def main_cli() -> int:
    inst_path = instances_json_path()
    if not os.path.isfile(inst_path):
        print(f"[instance_tool] 未找到: {inst_path}", file=sys.stderr)
        print("[instance_tool] 请先运行图形界面并点「新建」，或自行创建该 JSON。", file=sys.stderr)
        return 1
    try:
        rows = load_instances_from_path(inst_path)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        print(f"[instance_tool] 读取失败: {e}", file=sys.stderr)
        return 1
    err = validate_all(rows)
    if err:
        print(f"[instance_tool] {err}", file=sys.stderr)
        return 1
    _, lines = launch_instances(rows)
    for line in lines:
        print(line)
    return 0


def main_gui() -> None:
    MultiLaunchApp().mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(description="LWCS 多开")
    parser.add_argument("mode", nargs="?", default="gui", choices=("gui", "cli"))
    args = parser.parse_args()
    if args.mode == "cli":
        raise SystemExit(main_cli())
    main_gui()


if __name__ == "__main__":
    main()
