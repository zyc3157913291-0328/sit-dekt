# -*- coding: utf-8 -*-
"""
深信服 EasyConnect —— 窗口结构诊断工具（只读，不做任何点击）。

用途
----
排查 vpn-autologin.py「为什么没点中」时用。它会把 Sangfor 系进程的所有可见顶层
窗口连同可见子控件（类名 / 文字 / 相对坐标）全列出来。

2026-09-23 靠它查清的两件事，都记在这里免得下次重新踩：
  * 登录框上的「登录」是标准 `Button` 类，而登录成功后「安全提示」上的
    「允许 / 阻止」是 MFC 自绘的 `MFCButton` 类 —— 按类名过滤时必须两者都收，
    只认 `Button` 会把安全提示整个漏掉。
  * 「安全提示」不是独立窗口，它就是 EasyConnect 主窗口换了内容（同一个 hwnd），
    所以别指望靠窗口标题去区分。

用法：
    python vpn-inspect.py            # 列出所有窗口和可见控件
    python vpn-inspect.py --all      # 连不可见控件一起列（看隐藏的对话框模板）
"""

import argparse
import ctypes
import ctypes.wintypes as w
import subprocess
import sys

u32 = ctypes.windll.user32
u32.SetProcessDPIAware()
WNDENUMPROC = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass


def text_of(h):
    n = u32.GetWindowTextLengthW(h)
    b = ctypes.create_unicode_buffer(n + 1)
    u32.GetWindowTextW(h, b, n + 1)
    return b.value


def cls_of(h):
    b = ctypes.create_unicode_buffer(256)
    u32.GetClassNameW(h, b, 256)
    return b.value


def rect_of(h):
    r = w.RECT()
    u32.GetWindowRect(h, ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def children(hwnd, root):
    """子控件清单，坐标换算成相对 root 左上角。"""
    acc = []

    def cb(h, l):
        r = rect_of(h)
        p1 = w.POINT(r[0], r[1]); u32.ScreenToClient(root, ctypes.byref(p1))
        p2 = w.POINT(r[2], r[3]); u32.ScreenToClient(root, ctypes.byref(p2))
        acc.append((h, cls_of(h), text_of(h), bool(u32.IsWindowVisible(h)),
                    (p1.x, p1.y, p2.x, p2.y)))
        return True

    u32.EnumChildWindows(hwnd, WNDENUMPROC(cb), 0)
    return acc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--all', action='store_true', help='连不可见控件一起列')
    args = ap.parse_args()

    procs = {}
    r = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'],
                       capture_output=True, text=True, errors='ignore')
    for line in r.stdout.splitlines():
        p = [x.strip('"') for x in line.split('","')]
        if len(p) >= 2 and p[1].isdigit():
            procs[int(p[1])] = p[0]

    sangfor = {k for k, v in procs.items() if v.lower().startswith('sangfor')}
    print('Sangfor 系进程:', {k: procs[k] for k in sorted(sangfor)} or '无')

    mains = []

    def cb(h, l):
        if u32.IsWindowVisible(h):
            p = w.DWORD()
            u32.GetWindowThreadProcessId(h, ctypes.byref(p))
            if p.value in sangfor:
                r = rect_of(h)
                if r[2] - r[0] > 150:
                    mains.append((h, p.value, text_of(h), r))
        return True

    u32.EnumWindows(WNDENUMPROC(cb), 0)

    if not mains:
        print('没有可见的 Sangfor 窗口（客户端可能已收到托盘，或根本没运行）')
        return

    for h, pid, t, r in mains:
        print(f'\n=== hwnd={h} pid={pid}({procs[pid]}) title={t!r} '
              f'rect={r} size={r[2]-r[0]}x{r[3]-r[1]} ===')
        for ch, c, ct, vis, cr in children(h, h):
            if args.all or (vis and ct.strip()):
                mark = '[vis]' if vis else '[hid]'
                print(f'   {mark} cls={c!r:14} rect={cr} text={ct!r}')


if __name__ == '__main__':
    main()
