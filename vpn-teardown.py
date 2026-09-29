# -*- coding: utf-8 -*-
"""
门户浏览器清扫 / 任务收尾：关掉本次运行留在机器上的 VPN 客户端与它拉起的门户浏览器。

用法
----
    python vpn-teardown.py --keep 1234,5678 [--dry-run] [--keep-vpn]
                           [--browsers-only] [--exclude-cmd <子串>]...
                           [--keep-windows <基线文件>]

    python vpn-teardown.py --dump-windows <基线文件>      # 只写窗口基线后退出

`--keep` 是**跑 VPN 之前**就已经存在的浏览器进程 PID 集合（基线），
由 `daily-dekt.js` 的 `snapshotBrowserPids()` 采集。没有基线时传空字符串，
但那样就没有「归因」依据，脚本会拒绝关任何浏览器。

`--dump-windows` / `--keep-windows` 是**窗口**基线：拉 VPN **之前**先把当前
可见窗口的句柄+标题 dump 出来，之后只对**新出现的**、标题像校内门户的
浏览器窗口发 `WM_CLOSE`。这是唯一能处理「VPN 把门户页交给已经在跑的浏览器」
的办法 —— 那种情况不产生新进程（按进程判看不见），而按进程杀又会连带杀掉
用户自己的窗口。没给 `--keep-windows` 时这条规则整个不启用。

**顺序要求（用户 2026-09-29 明确）**：先在 VPN 客户端自己的窗口消失，
**再**关门户浏览器 / 统一认证页面。客户端窗口还挂在屏幕上时，清扫阶段只推迟
（每 6 秒一轮，下一轮再看），收尾阶段最多等 15 秒；等不到就记
`window_gate_forced` 后照常放行 —— 不能因为客户端窗口赖着不走，就把那个页面
留在桌面上。这条闸门**对进程规则和窗口规则同时生效**：实测（18:54 那轮）
进程规则抢在闸门之前把门户 Edge 关了，紧接着 VPN 又把门户页开了一次，
说明关早了它真的会弹回来。关 VPN 客户端始终是最后一步（除非 `--keep-vpn`）。

两种调用姿态
------------
* **`--browsers-only`**：只清扫浏览器，绝不碰 VPN 客户端。
  `daily-dekt.js` 在「VPN 刚连上、浏览器刚被拉起」的那一刻就用它 ——
  用户要求**在浏览器打开的那一步就关掉**，而不是等整个任务收尾。
* 默认（不加该开关）：收尾，关浏览器**并**关 VPN 客户端。

`--exclude-cmd` 给出的子串只要出现在某浏览器的命令行里，就**绝不**动它。
用于排除脚本自己用 puppeteer 拉起的那个浏览器（命令行里有
`--user-data-dir=<profile>`）：它要去访问 xg.sit.edu.cn，命令行天然含校内主机名，
不排除就会被下面「命令行含校内主机名」这条规则误杀。

浏览器归因规则
--------------
只考虑**主进程**（命令行不含 `--type=`），且**不在基线里**（本次运行才出现）。
逐条判：

0. 命令行命中 `--exclude-cmd` → 跳过（是我们自己拉起来干活的浏览器）；
1. 父进程链里有 Sangfor 系进程 → **收**。归因最硬的一条：客户端这时还活着
   （我们只在收尾时才关它），被它亲手拉起来的必然是门户页。**不被窗口标题否决**；
2. **无窗口**时看命令行：
   * 含 `--no-startup-window` → **收**（VPN 拉起浏览器带这个参数；
     2026-09-29 那个挂了一下午的残留正是它）；
   * 含校内主机名（`sit.edu.cn`）→ **收**（页面还没画出来）；
3. **有可见窗口**且标题像校内门户 → **收**
   （`统一身份认证` / `vpn1.sit.edu.cn` / `xg.sit.edu.cn` / `上海应用技术大学` / `EasyConnect` / `SSL VPN`）；
4. **有可见窗口、标题却不像校内** → **放过**（只报告）。那多半是用户自己的窗口。

第 4 条是刻意的保守取舍：宁可暂时漏关，也不误杀用户正在看的页面 ——
任务收尾还会再扫一遍，那时门户页标题必然已加载出来，照样收得掉。

**为什么不拿父进程名当判据**（2026-09-29 试过一版，当天撤掉）：
父进程经常已经退出、PID 解析不出名字（那个残留 Edge 的父进程就是被收尾关掉的
客户端）。用它判定两头都会错，不如不用。

**无窗口的后台浏览器也关**（条件是命中上面第 1 条）。
2026-09-29 实测遗留佐证：一个 `msedge.exe --no-startup-window` 在 VPN 客户端
关掉之后继续挂着 160 MB（父进程已消失、无可见窗口）。旧版本对无窗口进程
「只报告不关」，于是它一直留在机器上 —— 用户要的是别留下它。

**绝不按标题单独特杀**：只有当整个进程都是本次新出现的，才会被关。
这样即使你自己正开着 Edge/Chrome 在看校内页面，也不会被误杀。

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
WM_CLOSE = 0x0010

# 实测被 VPN 拉起来过的浏览器：Edge 与 Chrome 都出现过，所以不能只认一个。
BROWSER_IMAGES = ('msedge.exe', 'chrome.exe')
SANGFOR_PREFIX = 'sangfor'

# 校内门户/SSO 页面标题的识别关键词
SIT_TITLE_HINTS = (
    '统一身份认证',
    'vpn1.sit.edu.cn',
    'xg.sit.edu.cn',
    '上海应用技术大学',
    'EasyConnect',
    'SSL VPN',
)

# 命令行里的校内主机名：VPN 用默认浏览器打开门户页时，URL 就在命令行上
SIT_CMD_HINTS = ('sit.edu.cn',)

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


def tasklist_pids(images):
    """两个浏览器镜像当前的全部 PID（含子进程）。tasklist 很快，用作「有没有新东西」的快筛。"""
    pids = set()
    for img in images:
        try:
            r = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq ' + img, '/FO', 'CSV', '/NH'],
                               capture_output=True, text=True, errors='ignore')
        except Exception:
            continue
        for line in (r.stdout or '').splitlines():
            parts = [x.strip('"') for x in line.split('","')]
            if len(parts) >= 2 and parts[1].isdigit():
                pids.add(int(parts[1]))
    return pids


def proc_tree():
    """{pid: (进程名, 父pid)} —— 只查这层关系，不带命令行，快。"""
    rows = ps_json("Get-CimInstance Win32_Process | "
                   "Select-Object ProcessId,ParentProcessId,Name | ConvertTo-Json -Compress")
    tree = {}
    for r in rows or []:
        if isinstance(r, dict) and isinstance(r.get('ProcessId'), int):
            tree[r['ProcessId']] = (r.get('Name') or '', r.get('ParentProcessId') or 0)
    return tree


def browser_main_processes():
    """[(pid, ppid, 进程名, 命令行)]，只要主进程（命令行不含 --type=）。"""
    flt = ' or '.join("Name='%s'" % i for i in BROWSER_IMAGES)
    rows = ps_json('Get-CimInstance Win32_Process -Filter "%s" | '
                   'Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Compress'
                   % flt)
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
        result.append((pid, r.get('ParentProcessId') or 0, r.get('Name') or '', cmd))
    return result


def ancestor_names(pid, tree, limit=6):
    """从 pid 往上走，返回进程名列表（含自身）。"""
    names = []
    seen = set()
    cur = pid
    for _ in range(limit):
        if cur in seen or cur not in tree:
            break
        seen.add(cur)
        name, ppid = tree[cur]
        names.append(name)
        cur = ppid
    return names


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


def cmd_has_sit_host(cmd):
    low = cmd.lower()
    return any(hint in low for hint in SIT_CMD_HINTS)


def kill(pid, force=False):
    args = ['taskkill', '/PID', str(pid), '/T']
    if force:
        args.append('/F')
    r = subprocess.run(args, capture_output=True, text=True, errors='ignore')
    return r.returncode == 0


# ------------------------------------------------- 窗口基线 / 窗口级清扫
def hwnd_int(h):
    """把回调拿到的 HWND 转成能写进 JSON 的整数。"""
    try:
        return int(h)
    except Exception:
        try:
            return int(getattr(h, 'value', 0) or 0)
        except Exception:
            return 0


def dump_windows(path):
    """把当前所有可见顶层窗口（句柄 + 标题）写成 JSON，供之后比对。"""
    data = [{'hwnd': hwnd_int(h), 'title': t} for h, _pid, t in visible_windows()]
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    out('window_baseline_dumped count=%d file=%s' % (len(data), path))
    return 0


def load_window_baseline(path):
    """读窗口基线，返回句柄集合；**读不到就返回 None**（宁可漏关也不误关）。

    返回 None 时窗口规则整个不启用 —— 没有基线就分不清「新窗口」和
    「用户本来就开着的窗口」，那还不如不动手。
    """
    if not path:
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        out('window_baseline_unreadable %s' % str(e).replace('\n', ' ')[:80])
        return None
    try:
        return {int(w['hwnd']) for w in data if isinstance(w, dict) and 'hwnd' in w}
    except Exception:
        return None


def visible_sangfor_titles():
    """当前可见的 Sangfor 系进程窗口标题（用来判断「VPN 客户端窗口还在不在」）。"""
    titles = []
    try:
        tree = proc_tree()
        for _h, pid, title in visible_windows():
            name = (tree.get(pid) or ('', 0))[0].lower()
            if name.startswith(SANGFOR_PREFIX):
                titles.append(title)
    except Exception:
        pass
    return titles


def close_new_sit_windows(baseline, excludes, dry_run, wait_sangfor=0):
    """关掉「本次新出现 **且** 标题像校内门户」的浏览器窗口。返回关掉的个数。

    这是唯一能处理「VPN 把门户页交给已经在跑的浏览器」的办法 —— 那种情况下
    不产生新的浏览器进程（按进程判根本看不见），而按进程杀又会连带杀掉用户
    自己的窗口。只对**新出现的**窗口发 WM_CLOSE，关掉的就只有 VPN 开的那一个。

    **顺序要求（用户 2026-09-29 明确）**：先在 VPN 客户端自己的窗口消失、
    再关统一认证页面。客户端窗口还挂在屏幕上时不动它 —— 那时候关页面，
    客户端很可能又把它弹回来。`wait_sangfor` 是「等客户端窗口消失」的上限秒数：
    * 0（清扫阶段）→ 只推迟，交给下一轮（每 6 秒一轮）；
    * >0（收尾阶段）→ 最多等这么久，等不到就记 `window_gate_forced` 后照关，
      绝不因为客户端窗口赖着不走就漏掉这个页面。

    两条自我约束：
    * **没有窗口基线就整个不启用**（见 load_window_baseline）；
    * 只关**浏览器进程**拥有的窗口，且命令行命中 `--exclude-cmd` 的一律放过
      （脚本自己 puppeteer 那个浏览器在无头模式下没有可见窗口，这里是双保险）。
    """
    if baseline is None:
        out('window_rule_skipped no_window_baseline')
        return 0

    # 先用最快的办法筛：纯 ctypes 枚举窗口，通常一个候选都没有
    cand = []
    for h, pid, title in visible_windows():
        if hwnd_int(h) in baseline:
            continue
        if looks_like_sit(title):
            cand.append((hwnd_int(h), pid, title))
    if not cand:
        out('windows_none_new')
        return 0

    procs = browser_main_processes() or []
    browser_pids = {p for (p, _pp, _img, _cmd) in procs}
    excluded = {p for (p, _pp, _img, cmd) in procs
                if any(x.lower() in (cmd or '').lower() for x in excludes)}

    # 闸门：VPN 客户端自己的窗口还没消失就先别动（用户要求的顺序）。
    # 与进程规则共用同一次评估（见 vpn_window_gate 的缓存）。
    gate_ok = vpn_window_gate(wait_sangfor, dry_run)

    closed = 0
    for h, pid, title in cand:
        if pid in excluded:
            out('skipped_window hwnd=%d pid=%d reason=excluded title=%r' % (h, pid, title[:60]))
            continue
        if pid not in browser_pids:
            out('skipped_window hwnd=%d pid=%d reason=not_a_browser title=%r' % (h, pid, title[:60]))
            continue
        if dry_run:
            out('would_close_window hwnd=%d pid=%d title=%r' % (h, pid, title[:60]))
            continue
        if not gate_ok:
            out('skipped_window hwnd=%d pid=%d reason=vpn_window_visible' % (h, pid))
            continue
        u32.PostMessageW(h, WM_CLOSE, 0, 0)
        out('closed_window hwnd=%d pid=%d title=%r' % (h, pid, title[:60]))
        closed += 1
    return closed


# ---------------------------------------------------------------- 顺序闸门
# 单次调用内缓存：两条规则（进程级、窗口级）共用一次评估 ——
# 免得各等一遍、也省掉一次全进程枚举。
_GATE_RESULT = None


def vpn_window_gate(wait_sangfor, dry_run):
    """VPN 客户端自己的窗口是否已经消失 —— 只有消失后才允许动门户浏览器/统一认证页。

    这是用户 2026-09-29 明确要求的**顺序**：客户端窗口还挂在屏幕上时先别关页面。
    实测依据（18:54 那轮）：进程规则抢在闸门之前把门户 Edge 关了，
    紧接着 VPN 又把门户页开了一次 —— 关早了它会弹回来。

    * `wait_sangfor = 0`（清扫阶段）→ 只推迟，决定权交给下一轮（每 6 秒一轮）；
    * `wait_sangfor > 0`（收尾阶段）→ 最多等这么久；等不到就记
      `window_gate_forced` 后**照常放行** —— 不能因为客户端窗口赖着不走，
      就把那个页面永远留在桌面上。

    返回 True 表示「现在可以动手」。
    """
    global _GATE_RESULT
    if _GATE_RESULT is not None:
        return _GATE_RESULT

    sangfor = visible_sangfor_titles()
    if not sangfor:
        _GATE_RESULT = True
        return True

    if dry_run or wait_sangfor <= 0:
        out('close_deferred vpn_window_visible count=%d title=%r'
            % (len(sangfor), sangfor[0][:60]))
        _GATE_RESULT = False
        return False

    deadline = time.time() + wait_sangfor
    while time.time() < deadline:
        time.sleep(1)
        sangfor = visible_sangfor_titles()
        if not sangfor:
            break
    if sangfor:
        out('window_gate_forced vpn_window_still_visible count=%d' % len(sangfor))
    else:
        out('window_gate_opened vpn_window_gone')
    _GATE_RESULT = True
    return True


# ---------------------------------------------------------------- 浏览器清扫
def sweep_browsers(baseline, excludes, dry_run, wait_sangfor=0):
    """返回关掉的浏览器个数。逻辑见模块头部「浏览器归因规则」。"""
    if not (tasklist_pids(BROWSER_IMAGES) - baseline):
        out('browsers_none_new')
        return 0

    procs = browser_main_processes()
    if procs is None:
        out('browser_enum_failed')
        return 0

    tree = proc_tree()
    wins = visible_windows()
    wins_of = {}
    for _h, pid, title in wins:
        wins_of.setdefault(pid, []).append(title)

    targets = []
    protected = []
    for pid, ppid, image, cmd in procs:
        if pid in baseline:
            continue                        # 早就开着，不是我们开的
        low_cmd = cmd.lower()
        if any(x.lower() in low_cmd for x in excludes):
            protected.append((pid, image, 'excluded'))
            continue

        titles = wins_of.get(pid, [])
        sit_titles = [t for t in titles if looks_like_sit(t)]
        ancestors = ancestor_names(pid, tree)
        via_sangfor = any(n.lower().startswith(SANGFOR_PREFIX) for n in ancestors)
        has_window = bool(titles)

        # 归因刻意保守：**有可见窗口、标题却不像校内门户的，一律放过** ——
        # 那多半是用户自己的浏览器窗口。宁可暂时漏关，也不误杀用户正在看的页面：
        # 任务收尾还会再扫一遍，那时门户页标题必然已经加载出来，照样能收掉。
        #
        # 为什么不拿父进程名当判据（2026-09-29 试过一版，当天撤掉）：
        # 父进程经常已经退出、PID 解析不出名字（那个挂了一下午的残留 Edge，
        # 父进程就是被收尾关掉的客户端）。拿它判定两头都会错，不如不用。
        reason = None
        if via_sangfor:
            # 归因最硬的一条：客户端进程这时还活着（我们只在收尾时才关它）。
            # 不被窗口标题否决 —— 既然是客户端亲手拉起来的，那就是它开的门户页。
            reason = 'sangfor-parent'
        elif not has_window:
            # 无窗口时只能看命令行。VPN 拉起浏览器用的是 `--no-startup-window`
            # （2026-09-29 那个挂了一下午的残留正是它），或命令行里直接带门户 URL。
            if '--no-startup-window' in cmd:
                reason = 'no-startup-window'
            elif cmd_has_sit_host(cmd):
                reason = 'sit-url'
        elif sit_titles:
            reason = 'sit-title'
        else:
            protected.append((pid, image, 'visible-non-sit'))

        if reason:
            targets.append((pid, image, reason, titles, ppid, ancestors, cmd))

    for pid, image, why in protected:
        out('skipped_browser pid=%d image=%s reason=%s' % (pid, image, why))

    if not targets:
        out('browsers_none_to_close')
        return 0

    # 闸门：VPN 客户端自己的窗口还没消失就先别动（用户要求的顺序）
    gate_ok = vpn_window_gate(wait_sangfor, dry_run)

    closed = 0
    for pid, image, why, titles, ppid, ancestors, cmd in targets:
        # 证据行：把父进程链和命令行都打出来。下次「为什么关了/没关」不必再猜。
        out('found_browser pid=%d image=%s reason=%s parent=%d(%s) ancestors=%s windows=%d title=%r cmd=%r'
            % (pid, image, why, ppid,
               (tree.get(ppid) or ('?', 0))[0],
               '>'.join(ancestors[:4]),
               len(titles), (titles[0][:60] if titles else ''),
               cmd_brief(cmd)))
        if dry_run:
            out('would_close_browser pid=%d image=%s reason=%s' % (pid, image, why))
            continue
        if not gate_ok:
            out('skipped_browser pid=%d image=%s reason=vpn_window_visible' % (pid, image))
            continue
        ok = kill(pid)
        if not ok:
            ok = kill(pid, force=True)
        out('closed_browser pid=%d image=%s reason=%s ok=%s' % (pid, image, why, ok))
        if ok:
            closed += 1
    return closed


def cmd_brief(cmd, limit=180):
    cmd = cmd or ''
    return cmd if len(cmd) <= limit else cmd[:limit] + '…'


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--keep', default='',
                    help='基线：跑 VPN 之前已存在的浏览器进程 PID，逗号分隔')
    ap.add_argument('--dry-run', action='store_true', help='只报告，不关任何东西')
    ap.add_argument('--keep-vpn', action='store_true',
                    help='只收浏览器，不动 VPN 客户端（SIT_KEEP_VPN=1 时由 Node 侧传入）')
    ap.add_argument('--browsers-only', action='store_true',
                    help='只清扫浏览器，绝不碰 VPN 客户端（「打开那一刻就关」用这个）')
    ap.add_argument('--exclude-cmd', action='append', default=[],
                    help='命令行含该子串的浏览器一律不碰（可重复；用于排除脚本自己的 puppeteer 浏览器）')
    ap.add_argument('--dump-windows', default='', metavar='FILE',
                    help='只做一件事：把当前可见窗口（句柄+标题）写进 FILE，然后退出。'
                         '要在拉 VPN **之前**调用，作为「哪些窗口本来就是开着的」基线')
    ap.add_argument('--keep-windows', default='', metavar='FILE',
                    help='--dump-windows 产出的窗口基线；给了才会启用「关掉新建的校内窗口」规则')
    args = ap.parse_args()

    if args.dump_windows:
        return dump_windows(args.dump_windows)

    baseline = set()
    for tok in args.keep.replace(' ', '').split(','):
        if tok.isdigit():
            baseline.add(int(tok))
    excludes = [x for x in args.exclude_cmd if x]
    win_baseline = load_window_baseline(args.keep_windows)
    out('baseline_browser_pids=%s' % (sorted(baseline) or 'none'))
    if excludes:
        out('exclude_cmd=%s' % ','.join(excludes))
    out('window_baseline=%s' % ('none' if win_baseline is None
                                else '%d windows' % len(win_baseline)))

    # ---------- 1) 浏览器进程 ----------
    # 清扫阶段闸门只推迟（0）；收尾阶段最多等 15 秒客户端窗口消失，再放行。
    gate_wait = 0 if args.browsers_only else 15
    sweep_browsers(baseline, excludes, args.dry_run, wait_sangfor=gate_wait)

    # ---------- 1.5) 新建的校内标题窗口 ----------
    # 必须独立于上面的进程规则：VPN 把门户页交给已经在跑的浏览器时，
    # **不会产生新的浏览器进程**，只看进程根本发现不了。
    close_new_sit_windows(win_baseline, excludes, args.dry_run, wait_sangfor=gate_wait)

    # ---------- 2) VPN 客户端 ----------
    if args.browsers_only:
        out('vpn_kept_by_mode browsers_only')
    elif args.keep_vpn:
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
    if not args.browsers_only:
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
