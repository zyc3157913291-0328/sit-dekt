# -*- coding: utf-8 -*-
"""
深信服 EasyConnect —— 自动点掉「登录」按钮。

背景
----
客户端带 /ShortCutAutoLogin 启动时，只会把已保存的账号密码**填进输入框**，
不会自己点登录（服务器下发 g_bAllowAutoLogin=0，但 g_bAllowSavePwd=1）。
结果就是登录框静静摆在那里等人点，定时任务于是拿不到校园网。

做法
----
「登录」是货真价实的 Win32 Button 控件（不是自绘），所以直接给它发 BM_CLICK 即可。
相比模拟鼠标点击，这一路有三个好处：
  * 不依赖窗口位置和尺寸，窗口挪了、缩了都不影响；
  * 不需要移动真实鼠标，不打扰正在用电脑的人；
  * 屏幕锁定 / 无人在前台时同样有效（发的是窗口消息，不走输入队列）。

安全
----
点击前必须同时满足：进程名是 SangforCSClient.exe、窗口标题是 EasyConnect、
窗口内存在「服务器地址」标签。避免误点其它程序里恰好叫「登录」的按钮。
连续点击次数有上限（默认 3），防止密码错误时反复重试把账号试锁。

输出
----
每条机器可读状态都以 VPNHELPER 开头（纯 ASCII），便于 Node 侧直接过滤。
  0 = 隧道已就绪        2 = 超时未就绪
  3 = --check-only 且当前不可达
"""

import argparse
import ctypes
import ctypes.wintypes as w
import socket
import ssl
import subprocess
import sys
import time

u32 = ctypes.windll.user32
u32.SetProcessDPIAware()  # 让 GetWindowRect 与屏幕坐标同一空间

WNDENUMPROC = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
BM_CLICK = 0x00F5
CLIENT_EXE = 'SangforCSClient.exe'
# 安全提示的两个按钮：不同版本用词不一，允许侧和拒绝侧都列全
ALLOW_LABELS = ('允许', '同意', '是')
REJECT_LABELS = ('阻止', '拒绝', '不允许', '否')

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass


def out(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------- 窗口小工具
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


def visible(h):
    return bool(u32.IsWindowVisible(h))


def children(hwnd):
    acc = []

    def cb(h, l):
        acc.append(h)
        return True

    u32.EnumChildWindows(hwnd, WNDENUMPROC(cb), 0)
    return acc


def norm(s):
    """去掉普通空格和全角空格："登 录" 和 "登录" 归一。"""
    return s.replace(' ', '').replace('\u3000', '')


def proc_names():
    """pid -> 进程名。一次 tasklist 拿到全表，比逐个查快得多。"""
    m = {}
    try:
        r = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'],
                           capture_output=True, text=True, errors='ignore')
        for line in r.stdout.splitlines():
            parts = [x.strip('"') for x in line.split('","')]
            if len(parts) >= 2 and parts[1].isdigit():
                m[int(parts[1])] = parts[0]
    except Exception:
        pass
    return m


def top_windows(procs):
    """所有可见、标题非空的顶层窗口。"""
    acc = []

    def cb(h, l):
        if visible(h):
            t = text_of(h)
            if t:
                p = w.DWORD()
                u32.GetWindowThreadProcessId(h, ctypes.byref(p))
                acc.append(dict(hwnd=h, pid=p.value, pname=procs.get(p.value, '?'),
                                title=t, cls=cls_of(h), rect=rect_of(h)))
        return True

    u32.EnumWindows(WNDENUMPROC(cb), 0)
    return acc


def visible_buttons(hwnd):
    """{按钮文字: hwnd}，只取可见的按钮。

    类名要放宽：登录框上的「登录」是标准 `Button`，
    而安全提示上的「允许/阻止」是 MFC 自绘的 `MFCButton`。
    实测 BM_CLICK 对两者都有效。
    """
    d = {}
    for c in children(hwnd):
        if visible(c) and 'Button' in cls_of(c):
            t = norm(text_of(c))
            if t:
                d[t] = c
    return d


WM_GETTEXT = 0x000D
BM_GETCHECK = 0x00F0


def checkbox_checked(hwnd):
    """复选框是否被勾上（BM_GETCHECK 对标准复选框跨进程有效）。"""
    try:
        return u32.SendMessageW(hwnd, BM_GETCHECK, 0, 0) == 1
    except Exception:
        return None


def edit_text(hwnd, maxlen=256):
    """取输入框里的文字。

    坑：跨进程用 GetWindowText 读深信服这些输入框只能拿到空串（界面上明明
    填着学号密码）。GetWindowText 对别的进程只读窗口结构里的缓存；
    WM_GETTEXT 会真正走控件的消息处理，才读得到真实内容。
    """
    buf = ctypes.create_unicode_buffer(maxlen)
    n = u32.SendMessageW(hwnd, WM_GETTEXT, maxlen, ctypes.byref(buf))
    return buf.value if n > 0 else ''


def login_field_texts(win):
    """登录框里的输入框内容，按纵向位置排列：[服务器地址, 用户名, 密码]。"""
    rows = []
    for c in children(win['hwnd']):
        if not visible(c) or cls_of(c) != 'Edit':
            continue
        r = w.RECT()
        u32.GetWindowRect(c, ctypes.byref(r))
        rows.append((r.top, c))
    rows.sort()
    return [edit_text(c) for _t, c in rows]


def is_sit_login_window(win):
    """严格判定：必须是深信服客户端本体的 EasyConnect 登录框。"""
    if win['pname'] != CLIENT_EXE:
        return False
    if win['title'] != 'EasyConnect':
        return False
    if win['rect'][2] - win['rect'][0] < 200:
        return False
    labels = [text_of(c) for c in children(win['hwnd'])
              if visible(c) and cls_of(c) == 'Static']
    return any('服务器地址' in t for t in labels)


def is_security_prompt(win):
    """登录后弹的「安全提示：是否允许启动 msedge.exe」。

    按钮文字不同版本可能是「允许/阻止」或「同意/拒绝」，两边都认。
    """
    btns = visible_buttons(win['hwnd'])
    if not any(k in btns for k in REJECT_LABELS):
        return False
    if not any(k in btns for k in ALLOW_LABELS):
        return False
    return win['pname'].lower().startswith('sangfor') or '安全提示' in win['title']


def reject_button(btns):
    """挑「拒绝」那一侧的按钮。"""
    for k in REJECT_LABELS:
        if k in btns:
            return btns[k]
    return None


def dismiss_prompts(procs, tag=''):
    """把所有安全提示点掉，返回点掉的数量。"""
    n = 0
    try:
        for win in top_windows(procs):
            if is_security_prompt(win):
                btn = reject_button(visible_buttons(win['hwnd']))
                if btn:
                    u32.SendMessageW(btn, BM_CLICK, 0, 0)
                    n += 1
                    out(f'VPNHELPER dismissed_security_prompt{tag}')
    except Exception:
        pass
    return n


# ---------------------------------------------------------------- 连通性
def reachable(host, port=443, timeout=3.0):
    """TLS 握手成功才算通 —— 只是网卡 Up 不算。"""
    try:
        ctx = ssl._create_unverified_context()
        with socket.create_connection((host, port), timeout=timeout) as s:
            with ctx.wrap_socket(s, server_hostname=host):
                return True
    except Exception:
        return False


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default='xg.sit.edu.cn', help='用于判断隧道是否就绪的主机')
    ap.add_argument('--timeout', type=int, default=300, help='总超时（秒）')
    ap.add_argument('--max-clicks', type=int, default=3, help='最多点几次「登录」')
    ap.add_argument('--grace', type=int, default=25,
                    help='隧道就绪后再守这么多秒，专门等「安全提示」冒出来并点掉')
    ap.add_argument('--check-only', action='store_true', help='只报可达性，不做任何点击')
    args = ap.parse_args()

    if args.check_only:
        ok = reachable(args.host)
        out('VPNHELPER reachable' if ok else 'VPNHELPER unreachable')
        return 0 if ok else 3

    start = time.time()
    clicks = 0
    said_connecting = False
    cred_settled = False    # 凭据检测是否已有结论
    empty_reads = 0         # 连续读到"用户名为空"的次数，满 2 次才报警
    login_first_seen = 0.0  # 登录框第一次出现的时间，给"等窗口长齐"设上限
    wait_said = False       # 等待/就绪的诊断信息只打一次
    procs = proc_names()

    while time.time() - start < args.timeout:
        if reachable(args.host):
            out('VPNHELPER reachable')
            # 隧道通了不等于客户端消停了：它紧接着会弹「安全提示」，问是否允许
            # 启动 msedge.exe（门户页 autoOpen）。该弹窗**不会自己消失**，会一直
            # 挂着等人选，所以这里留一段宽限期专门盯着它点「阻止」——
            # 既不额外开浏览器，也不影响已经建立的隧道。
            grace_end = time.time() + args.grace
            while time.time() < grace_end:
                procs = proc_names()
                dismiss_prompts(procs)
                time.sleep(1.5)
            out('VPNHELPER done')
            return 0

        try:
            procs = proc_names()
            wins = top_windows(procs)
        except Exception as e:
            out('VPNHELPER enum_error ' + str(e).replace('\n', ' ')[:120])
            time.sleep(2)
            continue

        handled = False

        # 1) 安全提示先关掉（点「阻止」，我们不需要它开浏览器）
        if dismiss_prompts(procs):
            handled = True

        # 2) 登录框：先看有没有记住账号密码，有可见的「登录」就点
        if not handled:
            for win in wins:
                if not is_sit_login_window(win):
                    continue

                # 凭据检测：登录框里没填账号密码时，客户端根本没开「记住密码」，
                # 我们再怎么点「登录」也登不进去 —— 早点报出来让人去开。
                # 凭据检测。
                #
                # 关键认识：**读空 ≠ 没记住密码**。客户端弹出登录框后还会有一两秒
                # 在填凭据（实测 +6 秒读出来还是空，+13 秒就有了）。而且这个登录框
                # 每次枚举到的 hwnd 都不一样（客户端在重建窗口），所以不能靠
                # "同一窗口观察两次"来判断稳定。
                #
                # 因此：点「登录」照旧不受影响，但**连续两次读空**才认定凭据缺失并报警。
                # 一次瞬时读空不会打扰任何人。
                try:
                    def read_user():
                        """(True/False, 字段数) = 用户名填没填；字段数<3 时结论不可信。"""
                        # 注意传的是窗口字典，不是 hwnd：login_field_texts 内部要取 win['hwnd']
                        ff = login_field_texts(win)
                        if len(ff) < 3:
                            return None, len(ff)
                        return bool(ff[1].strip()), len(ff)

                    has_user, nf = read_user()
                    if has_user is not True:
                        # 客户端刚弹出时字段可能还没长齐、或还没把凭据填上去
                        #（实测 +6 秒时字段数不足）。停一下再确认一次，
                        # 避免把"还没填好"误判成"没记住密码"。
                        time.sleep(1.5)
                        again, nf2 = read_user()
                        if again is not None:
                            has_user, nf = again, nf2
                        else:
                            nf = nf2
                except Exception as e:
                    has_user, nf = None, -1
                    if not wait_said:
                        wait_said = True
                        out('credentials_probe_failed %s: %s'
                            % (type(e).__name__, str(e)[:120]))

                if login_first_seen == 0.0:
                    login_first_seen = time.time()

                # 窗口还没长齐时先不点：这时候既读不准凭据，也没必要抢这一下。
                # 等它长齐（最多 20 秒），换取一个可靠的凭据读数。
                if has_user is None and (time.time() - login_first_seen) < 20:
                    if not wait_said:
                        wait_said = True
                        out('credentials_wait 登录框尚未长齐（可见输入框 %d 个）' % nf)
                    break
                if not cred_settled and not wait_said and has_user is not None:
                    wait_said = True
                    out('credentials_ready 等待 %.0f 秒后就读到确定值' %
                        (time.time() - login_first_seen))

                if has_user is True and not cred_settled:
                    cred_settled = True
                    b0 = visible_buttons(win['hwnd'])
                    rem = (checkbox_checked(b0['记住密码'])
                           if '记住密码' in b0 else None)
                    out('credentials user=yes remember=%s'
                        % {True: 'yes', False: 'no'}.get(rem, 'unknown'))
                elif has_user is False and not cred_settled:
                    empty_reads += 1
                    if empty_reads >= 2:
                        cred_settled = True
                        b0 = visible_buttons(win['hwnd'])
                        rem = (checkbox_checked(b0['记住密码'])
                               if '记住密码' in b0 else None)
                        out('credentials user=no remember=%s'
                            % {True: 'yes', False: 'no'}.get(rem, 'unknown'))
                        out('credentials_missing')

                btns = visible_buttons(win['hwnd'])
                if '登录' in btns:
                    if clicks < args.max_clicks:
                        clicks += 1
                        u32.SendMessageW(btns['登录'], BM_CLICK, 0, 0)
                        out(f'VPNHELPER clicked_login attempt={clicks}')
                        said_connecting = False
                    elif not said_connecting:
                        said_connecting = True
                        out('VPNHELPER click_limit_reached')
                else:
                    if not said_connecting:
                        said_connecting = True
                        out('VPNHELPER already_connecting')
                break

        time.sleep(2)

    out('VPNHELPER timeout')
    return 2


if __name__ == '__main__':
    sys.exit(main())
