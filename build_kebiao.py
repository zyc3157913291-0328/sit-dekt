# -*- coding: utf-8 -*-
"""重建课表 CSV（修正周次含逗号导致的列错位），并按校历把调休落到具体日期。

输入输出都在**脚本自己所在目录**，不依赖任何绝对路径；
重新找一个目录放这套程序，直接就能跑。
"""
import json, csv, re, os
from datetime import date, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(BASE, 'kb-raw.json')
OUT = BASE

SEM_ORDER = ['2025-2026 第一学期', '2026-2027 第一学期',
             '2025-2026 第二学期', '2026-2027 第二学期']

WEEKDAY_CN = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']
WK_SHORT = {'星期一': '周一', '星期二': '周二', '星期三': '周三', '星期四': '周四',
            '星期五': '周五', '星期六': '周六', '星期日': '周日'}

# 奉贤校区上课时间（2026-2027 校历，2026年3月2日起执行）
PERIOD_TIME = {
    1: '08:20-09:05', 2: '09:10-09:55', 3: '10:15-11:00', 4: '11:05-11:50',
    5: '13:00-13:45', 6: '13:50-14:35', 7: '14:55-15:40', 8: '15:45-16:30',
    9: '18:00-18:45', 10: '18:50-19:35', 11: '19:40-20:25',
}

# 2026-2027 学年第一学期（秋季学期）：校历第 n 周的周一 = 2026-09-07 + 7n 天
# 校历确认：第0周 = 9/7-9/13，第19周 = 2027/1/18-1/24，寒假 1/25-2/21
FALL_2026_TERM_START_MON = date(2026, 9, 7)


def week_monday(term_start_mon, n):
    return term_start_mon + timedelta(days=7 * n)


def parse_weeks(s):
    """按“段”解析周次；单双标记只作用于它所在的那一段。
       '1-5周,14周'            -> [1,2,3,4,5,14]
       '1-8周,10-12周(双)'     -> [1..8] + [10,12]
       '9-11周(单)'            -> [9,11]
       返回 (周次列表, 是否含单双标记)"""
    s = (s or '').strip()
    has_parity = ('单' in s) or ('双' in s)
    weeks = []
    for part in re.split(r'[,，、]', s):
        part = part.strip()
        if not part:
            continue
        parity = None
        if '双' in part:
            parity = 'even'
        elif '单' in part:
            parity = 'odd'
        part = re.sub(r'[（(]\s*[双单]\s*[)）]', '', part)
        part = part.replace('第', '').replace('周', '').strip()
        if not part:
            continue
        seg = []
        m = re.match(r'^(\d+)\s*[-~—]\s*(\d+)$', part)
        if m:
            seg = list(range(int(m.group(1)), int(m.group(2)) + 1))
        elif re.match(r'^\d+$', part):
            seg = [int(part)]
        if parity == 'even':
            seg = [w for w in seg if w % 2 == 0]
        elif parity == 'odd':
            seg = [w for w in seg if w % 2 == 1]
        weeks.extend(seg)
    return sorted(set(weeks)), has_parity


def load():
    raw = json.load(open(RAW, encoding='utf-8'))
    data = {}
    for sem in SEM_ORDER:
        obj = raw.get(sem) or {}
        body = obj.get('body')
        rows = []
        if isinstance(body, str) and body.strip():
            try:
                d = json.loads(body)
                rows = d.get('kbList') or []
            except Exception:
                rows = []
        data[sem] = rows
    return data


def norm(r):
    jc = (r.get('jcs') or '').strip()
    a = int(jc.split('-')[0]) if jc.split('-')[0].isdigit() else 0
    return {
        'kcmc': (r.get('kcmc') or '').strip(),
        'xm': (r.get('xm') or '').strip(),
        'xqjmc': (r.get('xqjmc') or '').strip(),
        'jcs': jc,
        'jc_start': a,
        'zcd': (r.get('zcd') or '').strip(),
        'cdmc': (r.get('cdmc') or '').strip(),
        'jxbmc': (r.get('jxbmc') or '').strip(),
        'kch': (r.get('kch') or '').strip(),
        'xf': (r.get('xf') or '').strip(),
        'zxs': (r.get('zxs') or '').strip(),
        'xqmc': (r.get('xqmc') or '').strip(),
    }


def main():
    data = load()

    # ---------- 1) 修正版总表 ----------
    p1 = os.path.join(OUT, '课表-2025-2027第一学期.csv')
    with open(p1, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['学期', '课程名称', '教师', '星期', '节次', '周次',
                    '场地', '教学班', '课程号', '学分', '总学时', '校区'])
        for sem in ['2025-2026 第一学期', '2026-2027 第一学期']:
            for r in sorted(data[sem], key=lambda r: (WK_SHORT.get(r.get('xqjmc'), ''),
                                                      int((r.get('jcs') or '0').split('-')[0])
                                                      if (r.get('jcs') or '0').split('-')[0].isdigit() else 0)):
                n = norm(r)
                w.writerow([sem, n['kcmc'], n['xm'], n['xqjmc'], n['jcs'], n['zcd'],
                            n['cdmc'], n['jxbmc'], n['kch'], n['xf'], n['zxs'], n['xqmc']])

    # ---------- 2) 全学期总表（四个学期） ----------
    p2 = os.path.join(OUT, '课表-全部学期.csv')
    with open(p2, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['学期', '课程名称', '教师', '星期', '节次', '周次', '场地', '教学班', '课程号', '学分', '总学时'])
        for sem in SEM_ORDER:
            for r in data[sem]:
                n = norm(r)
                w.writerow([sem, n['kcmc'], n['xm'], n['xqjmc'], n['jcs'], n['zcd'],
                            n['cdmc'], n['jxbmc'], n['kch'], n['xf'], n['zxs']])

    # ---------- 3) 2026-2027 第一学期 按日期展开 + 调休 ----------
    SEM = '2026-2027 第一学期'
    START = FALL_2026_TERM_START_MON

    # 调休规则（上海应用技术大学 2026-2027 学年校历 说明第4条）
    #   国庆：10/1(四)-10/7(三) 放假调休
    #   9/20(日) 上 10/7(三) 的课；10/9(五) 上 10/5(一) 的课；10/10(六) 上 10/9(五) 的课
    MOVE = {
        date(2026, 10, 5): (date(2026, 10, 9), '国庆调休：10/5(周一)的课挪到 10/9(周五)上'),
        date(2026, 10, 7): (date(2026, 9, 20), '国庆调休：10/7(周三)的课挪到 9/20(周日)上'),
        date(2026, 10, 9): (date(2026, 10, 10), '国庆调休：10/9(周五)的课挪到 10/10(周六)上'),
    }
    # 放假/停课日（校历未安排补课）
    OFF = {
        date(2026, 9, 25): '中秋节放假（9/25-9/27），校历未安排补课',
        date(2026, 10, 1): '国庆节放假（10/1-10/7），校历未安排补课',
        date(2026, 10, 2): '国庆节放假（10/1-10/7），校历未安排补课',
        date(2026, 10, 6): '国庆节放假（10/1-10/7），校历未安排补课',
        date(2026, 10, 23): '全校运动会（10/23-10/24）',
        date(2026, 10, 24): '全校运动会（10/23-10/24）',
        date(2027, 1, 1): '元旦休假',
    }

    occ = []      # 实际发生的课
    off_hit = []  # 被放假/调休影响的课
    for r in data[SEM]:
        n = norm(r)
        if n['xqjmc'] not in WEEKDAY_CN:
            continue
        if not n['jcs'] or '-' not in n['jcs']:
            continue
        wd = WEEKDAY_CN.index(n['xqjmc'])
        weeks, parity = parse_weeks(n['zcd'])
        for wk in weeks:
            d = week_monday(START, wk) + timedelta(days=wd)
            note = ''
            actual = d
            if d in MOVE:
                actual, note = MOVE[d]
                note = note
            elif d in OFF:
                off_hit.append({
                    '原定日期': d.isoformat(), '星期': WK_SHORT[n['xqjmc']],
                    '节次': n['jcs'], '课程名称': n['kcmc'], '教师': n['xm'],
                    '场地': n['cdmc'], '周次': '第%d周' % wk, '说明': OFF[d],
                })
                continue
            occ.append({
                '日期': actual.isoformat(), '星期': WK_SHORT[WEEKDAY_CN[actual.weekday()]],
                '节次': n['jcs'],
                '时间(奉贤)': PERIOD_TIME.get(n['jc_start'], ''),
                '课程名称': n['kcmc'], '教师': n['xm'], '场地': n['cdmc'],
                '学分': n['xf'], '原周次': '第%d周' % wk,
                '是否调休': '调休' if note else '',
                '说明': note,
            })

    occ.sort(key=lambda x: (x['日期'], int(x['节次'].split('-')[0])))

    p3 = os.path.join(OUT, '课表-2026-2027第一学期-按日期.csv')
    cols = ['日期', '星期', '节次', '时间(奉贤)', '课程名称', '教师', '场地', '学分', '原周次', '是否调休', '说明']
    with open(p3, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for o in occ:
            w.writerow(o)

    p4 = os.path.join(OUT, '调休停课-2026-2027第一学期.csv')
    with open(p4, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['原定日期', '星期', '节次', '课程名称', '教师', '场地', '周次', '说明'])
        w.writeheader()
        for o in sorted(off_hit, key=lambda x: (x['原定日期'], x['节次'])):
            w.writerow(o)

    # ---------- 4) 人类可读：按周的课表 ----------
    lines = []
    lines.append('2026-2027 学年第一学期 课表（上海应用技术大学 · 奉贤校区）')
    lines.append('校历：秋季学期 第0周=2026/9/7-9/13，第1周=9/14-9/20，…，第19周=2027/1/18-1/24；寒假 1/25-2/21')
    lines.append('说明：本科老生自第1周起上课。上课时间按奉贤校区作息。')
    lines.append('')
    lines.append('=' * 78)
    lines.append('一、每周固定课表（周次为校历周次）')
    lines.append('=' * 78)
    for wd in WEEKDAY_CN:
        rows = [norm(r) for r in data[SEM] if (r.get('xqjmc') or '').strip() == wd]
        if not rows:
            continue
        rows.sort(key=lambda n: n['jc_start'])
        lines.append('')
        lines.append('【%s】' % WK_SHORT[wd])
        for n in rows:
            lines.append('  %-6s %-34s %-6s %-14s %s' % (
                n['jcs'], n['kcmc'][:34], n['xm'], n['cdmc'], n['zcd']))
    lines.append('')
    lines.append('=' * 78)
    lines.append('二、秋季学期日历（含节假日与调休，逐周）')
    lines.append('=' * 78)
    for wk in range(0, 20):
        mon = week_monday(START, wk)
        sun = mon + timedelta(days=6)
        lines.append('')
        lines.append('第%d周  %s ~ %s' % (wk, mon.isoformat(), sun.isoformat()))
        dayrows = {}
        for o in occ:
            od = date.fromisoformat(o['日期'])
            if mon <= od <= sun:
                dayrows.setdefault(o['日期'], []).append(o)
        for dstr in sorted(dayrows):
            d = date.fromisoformat(dstr)
            tag = '  ← 调休补课日' if d.weekday() >= 5 else ''
            lines.append('  %s %s%s' % (dstr, WK_SHORT[WEEKDAY_CN[d.weekday()]], tag))
            for o in sorted(dayrows[dstr], key=lambda x: int(x['节次'].split('-')[0])):
                lines.append('      %-6s %-34s %-6s %-16s %s' % (
                    o['节次'], o['课程名称'][:34], o['教师'], o['场地'],
                    o['说明'] if o['说明'] else o['原周次']))
        for o in off_hit:
            od = date.fromisoformat(o['原定日期'])
            if mon <= od <= sun:
                lines.append('  %s %s  【停课】%s %s（%s）' % (
                    o['原定日期'], o['星期'], o['节次'], o['课程名称'], o['说明']))
    lines.append('')
    lines.append('=' * 78)
    lines.append('三、调休/停课汇总')
    lines.append('=' * 78)
    lines.append('1) 国庆调休（校历说明第4条）：10/1(周四)-10/7(周三)放假调休；')
    lines.append('   9/20(周日) 上 10/7(周三) 的课；10/9(周五) 上 10/5(周一) 的课；10/10(周六) 上 10/9(周五) 的课。')
    lines.append('2) 中秋节：9/25(周五)-9/27(周日)放假，未安排补课。')
    lines.append('3) 全校运动会：10/23(周五)、10/24(周六)。')
    lines.append('4) 元旦：2027/1/1(周五)休假。')
    lines.append('')
    lines.append('受影响的课（共 %d 条）：' % len(off_hit))
    for o in sorted(off_hit, key=lambda x: (x['原定日期'], x['节次'])):
        lines.append('  %s %s 第%s节 %s（%s）— %s' % (
            o['原定日期'], o['星期'], o['节次'], o['课程名称'], o['场地'], o['说明']))

    p5 = os.path.join(OUT, '课表-2026-2027第一学期-含调休.txt')
    open(p5, 'w', encoding='utf-8-sig').write('\n'.join(lines))

    print('总表(2025-2027第一学期) rows =', sum(len(data[s]) for s in ['2025-2026 第一学期', '2026-2027 第一学期']))
    print('总表(全部学期) rows =', sum(len(data[s]) for s in SEM_ORDER))
    print('2026-2027 第一学期 按日期展开 =', len(occ), '条')
    print('  其中调休改期 =', sum(1 for o in occ if o['是否调休']))
    print('  停课 =', len(off_hit))
    print('files:', p1, p2, p3, p4, p5, sep='\n  ')


main()
