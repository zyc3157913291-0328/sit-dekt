# -*- coding: utf-8 -*-
"""
任务收尾：关掉本次运行留在机器上的 VPN 客户端与 Edge 门户页。

用法
----
    python vpn-teardown.py --keep 1234,5678 [--dry-run]

`--keep` 是**跑 VPN 之前**就已经存在的 msedge 进程 PID 集合（基线）。
没有基线时传空字符串，但那样就没有「归因」依据，脚本会拒绝关 Edge。

Edge 归因规则（**三条同时满足**才动手）
--------------------------------------
1. 该 msedge **主进程**不在基线里 —— 说明它是本次运行期间才出现的；
2. 它有**可见顶层窗口** —— 无窗口的后台进程不构成「界面残留」，只报告不关；
3. 且 满足下面任一条：
   * 命令行含 `--no-startup-window`（实测：VPN 拉起 Edge 时带这个参数）
   * 窗口标题像校内门户

**绝不按标题单独特杀**：只有当整个进程都是本次新出现的，才会被关。
这样即使你自己正开着 Edge 在看校内页面，也不会被误杀。

退出码永远是 0 —— 收尾失败不该把一个已经成功的取数任务判成失败。
"""

import argparse
import ctypes
import ctypes.wintypes as w
import json
import subprocess
import sys
import time

u32 = ctypes.windll.user32
u32.SetProcessDPIAware()
WNDENUMPROC = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

VPN_IMAGE = 'SangforCSClient.exe'

# 校内门户/SSO 页面标题的识别关键词
SIT_TITLE_HINTS = (
    '统一身份认证',
    'vpn1.sit.edu.cn',
    'xg.sit.edu.cn',
    '上海应用技术大学',
    'EasyConnect',
    'SSL VPN',
)

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass


def out(msg):
    print('VPNTEARDOWN ' + msg, flush=True)


# ---------------------------------------------------------------- 进程 / 窗口
def ps_json(command):
    """跑一段 PowerShell 并解析它输出的 JSON（命令本身纯 ASCII，无编码坑）。"""
    script = ('[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false);'
              + command)
    try:
        r = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=60,
        )
    except Exception as e:
        out('powershell_failed ' + str(e).replace('\n', ' ')[:120])
        return None
    text = (r.stdout or '').strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except Exception:
        return None
    if isinstance(data, dict):
        return [data]
    return data


def edge_main_processes():
    """msedge 主进程（命令行不含 --type=）。返回 [(pid, cmdline)]。"""
    rows = ps_json(
        "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
        "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
    )
    if rows is None:
        return None
    result = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        pid = r.get('ProcessId')
        cmd = r.get('CommandLine') or ''
        if not isinstance(pid, int):
            continue
        if '--type=' in cmd:          # 渲染/工具子进程，跟着主进程一起走
            continue
        result.append((pid, cmd))
    return result


def visible_windows():
    """所有可见且标题非空的顶层窗口：[(hwnd, pid, title)]。"""
    acc = []

    def cb(h, l):
        if u32.IsWindowVisible(h):
            n = u32.GetWindowTextLengthW(h)
            b = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(h, b, n + 1)
            t = b.value
            if t:
                p = w.DWORD()
                u32.GetWindowThreadProcessId(h, ctypes.byref(p))
                acc.append((h, p.value, t))
        return True

    u32.EnumWindows(WNDENUMPROC(cb), 0)
    return acc


def looks_like_sit(title):
    low = title.lower()
    return any(hint.lower() in low for hint in SIT_TITLE_HINTS)


def alive(pid):
    r = subprocess.run(['tasklist', '/FI', 'PID eq %d' % pid, '/FO', 'CSV', '/NH'],
                       capture_output=True, text=True, errors='ignore')
    return ('"%d"' % pid) in r.stdout


def kill(pid, force=False):
    args = ['taskkill', '/PID', str(pid), '/T']
    if force:
        args.append('/F')
    r = subprocess.run(args, capture_output=True, text=True, errors='ignore')
    return r.returncode == 0


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--keep', default='',
                    help='基线：跑 VPN 之前已存在的 msedge 主进程 PID，逗号分隔')
    ap.add_argument('--dry-run', action='store_true', help='只报告，不关任何东西')
    ap.add_argument('--keep-vpn', action='store_true',
                    help='只收 Edge，不动 VPN 客户端（SIT_KEEP_VPN=1 时由 Node 侧传入）')
    args = ap.parse_args()

    baseline = set()
    for tok in args.keep.replace(' ', '').split(','):
        if tok.isdigit():
            baseline.add(int(tok))
    out('baseline_edge_pids=%s' % (sorted(baseline) or 'none'))

    # ---------- 1) Edge：只关能归因给 VPN 的 ----------
    procs = edge_main_processes()
    if procs is None:
        out('edge_enum_failed')
    else:
        wins = visible_windows()
        wins_of = {}
        for _h, pid, title in wins:
            wins_of.setdefault(pid, []).append(title)

        targets = []
        windowless = []
        for pid, cmd in procs:
            if pid in baseline:
                continue                       # 早就开着，不是我们开的
            titles = wins_of.get(pid, [])
            # 只关**有可见窗口**的进程 —— 用户要清的是界面上残留的页面。
            # 实测 VPN 也会拉起无窗口的后台 Edge（09-24 12:13:37 那组就是），
            # 那种不构成界面残留；而 `--no-startup-window` 同样可能是 Edge 自身的
            # 后台启动，所以无窗口时一律只报告、不动手。
            if not titles:
                if '--no-startup-window' in cmd:
                    windowless.append(pid)
                continue
            if '--no-startup-window' in cmd:
                targets.append((pid, 'no-startup-window', titles))
                continue
            hit = [t for t in titles if looks_like_sit(t)]
            if hit:
                targets.append((pid, 'sit-title', hit))

        if windowless:
            out('notice_new_windowless_edge pid=%s (无窗口后台进程，不关)'
                % ','.join(str(p) for p in windowless))

        # 有窗口但不是我们开的、标题又像校内 —— 只报告，不动手
        for pid in list(wins_of):
            if pid in baseline:
                hit = [t for t in wins_of[pid] if looks_like_sit(t)]
                if hit:
                    out('notice_sit_window_in_preexisting_edge pid=%d title=%r'
                        % (pid, hit[0][:60]))

        if not targets:
            out('edge_none_to_close')
        for pid, why, titles in targets:
            if args.dry_run:
                out('would_close_edge pid=%d reason=%s title=%r'
                    % (pid, why, (titles[0][:60] if titles else '')))
                continue
            ok = kill(pid)
            if not ok:
                ok = kill(pid, force=True)
            out('closed_edge pid=%d reason=%s ok=%s' % (pid, why, ok))

    # ---------- 2) VPN 客户端 ----------
    if args.keep_vpn:
        out('vpn_kept_by_option')
    elif args.dry_run:
        r = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq ' + VPN_IMAGE, '/FO', 'CSV', '/NH'],
                           capture_output=True, text=True, errors='ignore')
        out('would_close_vpn running=%s' % (VPN_IMAGE.lower() in r.stdout.lower()))
    else:
        r = subprocess.run(['taskkill', '/IM', VPN_IMAGE],
                           capture_output=True, text=True, errors='ignore')
        if r.returncode != 0:
            out('vpn_not_running')
        else:
            out('vpn_close_signalled')
            time.sleep(5)
            # 托盘程序常忽略 WM_CLOSE，确认后再强制
            r2 = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq ' + VPN_IMAGE, '/FO', 'CSV', '/NH'],
                                capture_output=True, text=True, errors='ignore')
            if VPN_IMAGE.lower() in r2.stdout.lower():
                subprocess.run(['taskkill', '/IM', VPN_IMAGE, '/F'],
                               capture_output=True, text=True, errors='ignore')
                out('vpn_force_killed')

    # ---------- 3) 复查 ----------
    r = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq ' + VPN_IMAGE, '/FO', 'CSV', '/NH'],
                       capture_output=True, text=True, errors='ignore')
    out('vpn_still_running=%s' % (VPN_IMAGE.lower() in r.stdout.lower()))
    left = [t for _h, pid, t in visible_windows()
            if pid not in baseline and looks_like_sit(t)]
    out('sit_windows_left=%d' % len(left))
    out('done')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:      # 收尾绝不让任务失败
        out('error ' + str(e).replace('\n', ' ')[:200])
        sys.exit(0)
