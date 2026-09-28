# SIT 第二课堂空闲时段表

上海应用技术大学（SIT）学生用的小工具：每天自动连上校园 VPN，把**第二课堂可报名活动**和你**本周课表**求一次交集，生成一张彩色 HTML 表，告诉你**哪几个时段既没课又有活动可以报**。

全程无人值守，适合做成每日定时任务。

---

## 它解决什么问题

第二课堂活动要抢，但"什么时候有空去"这件事很烦：要同时看课表、看活动时间、还不能和报名截止撞上。这个工具把这件事自动化：

- 自动连校园 VPN（很多人卡在这一步）
- 自动登录学工系统（含验证码离线识别，**不消耗任何大模型 token**）
- 抓活动 → 过滤掉已过期/报名已结束的 → 逐条进详情页取活动地点
- 从教务系统**实时刷新课表**（老师调课、换教室都会反映进来）
- 求交集，渲染成一张表：课程蓝色、可去活动绿色、与课冲突的活动橙色、空闲灰色
- 表格里**已过去的日期用淡灰列底、今天用靛蓝列底**，一眼看清哪天是哪天
- 跑完自己收尾：关掉 VPN、关掉它自己拉起来的 Edge 门户页

---

## 运行原理

```
① 校园网
   探测 xg.sit.edu.cn
   不可达 → 检查 VPN 客户端是否已在运行（在运行就不重复启动）
          → 拉起客户端 + 助手脚本自动点「登录」
          → 自动拒绝「安全提示」（不额外开浏览器）

② 取活动（学工系统 xg.sit.edu.cn）
   CAS 登录：本地 ddddocr 识别验证码，最多 3 轮
   抓活动列表 → 过滤（已过期 / 报名已结束 / 报名已截止）
   → 逐条 goto 详情页取「活动地点」

③ 刷新课表（教务系统 jwxt.sit.edu.cn，正方 v9）
   复用 CAS 单点登录（*.sit.edu.cn 共享 cookie，不用再输密码）
   POST /jwglxt/kbcx/xskbcx_cxXsKb.html?gnmkdm=N2151
   → kb-raw.json → build_kebiao.py 展开成「按日期」并套用校历调休

④ 求交集 + 渲染
   课表 × 活动 → 彩色 HTML；列底标注 已过 / 今天 / 未到
   → tables/第二课堂空闲-<YYYYMMDD-HHmm>.html

⑤ 收尾
   Firefox 打开表格 → 关闭 VPN 客户端 → 关闭本次带出的 Edge 门户页
```

---

## 环境要求

| 组件 | 说明 |
|---|---|
| Windows 10 / 11 | 目前只在 Windows 上验证过（依赖 Win32 窗口消息） |
| Node.js ≥ 18 | 主程序 |
| Python 3 | 助手脚本与课表展开 |
| Google Chrome | 抓取用（无头模式） |
| Mozilla Firefox | 展示用（可选，缺失只跳过"打开表格"） |
| 深信服 EasyConnect | 校园 VPN 客户端 |
| Python 包 `ddddocr` | 验证码离线识别 |

---

## 安装

```powershell
# 1) Node 依赖
npm install

# 2) Python 验证码识别库
pip install ddddocr

# 3) 配置账号密码
Copy-Item config.example.json config.local.json
notepad config.local.json      # 填入你的学号与密码
```

`config.local.json` 已在 `.gitignore` 中，**不会被提交到 GitHub**。凭据也可以改用环境变量 `SIT_USER` / `SIT_PASS` 提供。

---

## 第一次运行前：让 VPN 记住密码（**唯一必须人工做一次的事**）

深信服客户端在服务器不允许"自动登录"（`g_bAllowAutoLogin=0`），但允许**保存密码**。程序能自动点「登录」，前提是登录框里**已经填好了账号密码**。

请手动做一次：

1. 打开深信服 EasyConnect 客户端
2. 填写服务器地址、学号、密码
3. **勾选「记住密码」**
4. 点「登录」，成功连上一次

之后程序就能自动完成登录。程序每次运行都会检测登录框：

- `credentials user=yes remember=yes` → 正常，凭据已保存
- `credentials_wait 登录框尚未长齐` → 正常现象：客户端弹出登录框后还要一两秒才把凭据填上去，程序会等它（**不会**误判成"没记住密码"）
- `credentials_missing` → 客户端确实没保存凭据，会打印提示告诉你怎么做

> 密码框读不到内容是**正常的**：客户端对密码框屏蔽了 `WM_GETTEXT`（防读密码的标准做法）。程序只按用户名判据，不会误报。

---

## 运行

```powershell
node daily-dekt.js              # 正常跑，结束后用 Firefox 打开表格
node daily-dekt.js --no-open    # 不打开浏览器（定时任务用）
node daily-dekt.js --from-file 活动列表.json --no-open   # 离线自测，跳过网络
```

结果写在 `daily-result.txt`（UTF-8，含逐行日志与末尾的 ASCII 摘要行）：

```
SUMMARY kept=7 inweek=4 conflict=0 free_inweek=4 weekend=2 conflict_weekend=1 free_weekend=1 later=3 removed=0
TABLE=<程序目录>\tables\第二课堂空闲-20260924-1230.html
```

---

## 做成每日定时任务

**Windows 任务计划程序**

```
程序：   node.exe
参数：   <程序目录>\daily-dekt.js --no-open
起始于： <程序目录>
```

（`<程序目录>` 就是你放这套程序的地方，例如 `D:\tools\sit-dekt`。
程序内部所有读写都在这个目录下，不依赖任何绝对路径。）

**DSH 内置 scheduler**：新建任务，提示词里让 agent 运行 `node daily-dekt.js --no-open`，然后读 `daily-result.txt` 贴出 `SUMMARY` 行（**不要**解析 pwsh 控制台输出，中文会乱码）。

定时的几点经验：

- 到点时 VPN 大概率是断的，程序会自己连——实测从拉起客户端到隧道就绪约 20 秒
- 程序跑完会**自动关掉 VPN**。如果那个时间你正用校园网做别的事，设环境变量 `SIT_KEEP_VPN=1`，就只收 Edge 不动 VPN
- 整轮耗时约 2.5 分钟

---

## 配置项（环境变量）

| 变量 | 作用 | 默认 |
|---|---|---|
| `SIT_USER` / `SIT_PASS` | 学号密码（优先于 `config.local.json`） | 读文件 |
| `SIT_HOME` | 数据目录 | 脚本所在目录 |
| `SIT_CHROME` | Chrome 可执行文件 | 自动探测 |
| `SIT_FIREFOX` | Firefox 可执行文件 | 自动探测 |
| `SIT_PYTHON` | Python 解释器 | 自动探测 Anaconda / PATH |
| `SIT_SANGFOR` | 深信服客户端路径 | 自动探测 |
| `SIT_VPN_WAIT_MS` | 等 VPN 就绪上限（毫秒） | 360000（6 分钟） |
| `SIT_KEEP_VPN` | 置 1 则收尾时保留 VPN | 关 |
| `SIT_KB_DEBUG` | 置 1 则额外抓教务菜单树与按周次接口，用于排查 | 关 |
| `NODE_PATH` / `PUPPETEER_CORE_ROOT` | puppeteer-core 位置（一般不用设） | 自动回退查找 |

---

## 目录说明

```
daily-dekt.js          主程序：VPN → 取活动 → 刷新课表 → 渲染 → 收尾
vpn-autologin.py       助手：检测登录框凭据、点「登录」、关「安全提示」
vpn-teardown.py        收尾：关 VPN 与本次带出的 Edge 门户页
vpn-inspect.py         诊断：只读列出 VPN 客户端所有窗口与可见控件
build_kebiao.py        把正方「周次课表」展开成「按日期」并套用校历调休
ocr-captcha.py         用 ddddocr 离线识别验证码
config.example.json    配置模板 → 复制成 config.local.json
课表-*.csv / *.txt      课表快照（每次运行自动刷新）
kb-raw.json            正方课表接口的原始响应
调休停课-*.csv          校历调休/停课推算结果
tables/                生成的 HTML 表格（不进版本库）
profile/               Chrome 用户目录，保存 CAS 登录态（不进版本库）
_archive/              开发期的探索脚本与截图（不进版本库）
```

---

## 常见问题

### 日志提示「VPN 登录框里没有账号/密码」

客户端没开「记住密码」。按上面「第一次运行前」那一节手动登录一次即可。

### 日志提示「缺少校园网账号密码」

程序需要重新登录（缓存会话过期了），但本地没有凭据。创建 `config.local.json`：

```json
{ "user": "你的学号", "pass": "你的密码" }
```

### 课表会不会更新？老师临时调课怎么办？

**每次运行都会重新抓一遍课表**，不是死快照。

数据来自正方教务 v9 的**学生课表查询**接口——也就是系统里学生自己看的课表。学校录入的调课、换教室会反映在这个接口的返回里，因此会被自动带上。程序同时保存完整的原始响应（`kb-raw.json`），便于事后核对。

需要注意的一点：教务系统的学生菜单里**没有独立的「调课」模块**（课表相关只有:个人课表查询 / 学生课表查询（按周次）/ 班级课表查询 / 实验课表查询）。也就是说调课是**显示在课表视图内部**的。如果某次调课只出现在「按周次」视图而不在课表接口里，程序可能抓不到——置 `SIT_KB_DEBUG=1` 跑一次会在日志里列出该视图用到的接口，方便进一步排查。

另外，老师只在群里口头说的调课，任何程序都拿不到。

### SUMMARY 各字段是什么意思

摘要里**周中与周末分开数**（表头已含周末，所以周末自成一个数，不去稀释 `inweek` 的历史含义）：

| 字段 | 含义 |
|---|---|
| `kept` | 过滤后保留的活动总数（未过期、未截止、未结束报名） |
| `inweek` | 落进表格**周一至周五**列的活动数 |
| `conflict` | 其中与课程时间冲突的条数 |
| `free_inweek` | 其中真正空闲可去的（= `inweek - conflict`） |
| `weekend` | 落进表格**周六/周日**列的活动数 |
| `conflict_weekend` | 其中与课程冲突的条数（**调休日的课也算**） |
| `free_weekend` | 其中空闲可去的（= `weekend - conflict_weekend`） |
| `later` | 未进表格的：下周及以后，或无对应节次 |
| `removed` | 清理掉的过期表格数 |

表格里的活动总数 = `inweek + weekend`。

### 表格里的列底色是什么意思

- **淡灰** = 已过去的日期（内容也做了淡化）
- **靛蓝** = 今天
- 白色 = 本周尚未到来的日期

表头是**周一至周日**共 7 列；**周六/周日**两列表头底色更深，并带「周末」徽标。

7 个日期列**等宽**（时间列固定 98px，其余等分剩余宽度）。单元格内容放不下时**换行把行撑高**，
不会把列压窄、也不截断文字 —— 所以某一行比较高是正常的，说明那一格信息多。

周末列和周一至周五**一样会查课表** —— 学期里有**调休上课**，周末确实可能真有课
（本地课表快照里就有周日课程），所以落在周末的活动也照常判冲突，不会因为「是周末」就默认空闲。

### 表格打开是乱码

用 UTF-8 读 `daily-result.txt`。Windows PowerShell 的控制台输出常把中文变成乱码，**以文件内容为准**。

### VPN 连上了但一会儿又断了

收尾会把 VPN 关掉，这是设计行为。不想让它关就设 `SIT_KEEP_VPN=1`。

### 想看看 VPN 客户端现在什么状态

```powershell
python vpn-inspect.py          # 列出所有窗口与可见控件
python vpn-inspect.py --all    # 连隐藏的一起列
python vpn-autologin.py --check-only   # 只报校园网是否可达
```

---

## 已知限制

- **学期相关数据是硬编码的**：`build_kebiao.py` 里的国庆/中秋/运动会调休规则、`daily-dekt.js` 里的学期起止日期、抓取的学期代码（`KB_SEMS`）都需要**每学期更新一次**。
- **只针对上海应用技术大学**：接口地址、SSO 路径、VPN 客户端都是这个学校的。
- **只在 Windows 上验证过**：VPN 自动登录用的是 Win32 窗口消息（`BM_CLICK`），依赖深信服客户端的具体控件结构。
- **调课捕获有残余不确定性**：见上面「老师临时调课怎么办」。
- 程序会**无条件关闭 VPN 客户端**（可用 `SIT_KEEP_VPN=1` 关闭该行为）。

---

## 打包发布

```powershell
powershell -File make-release.ps1
```

会在 `release/` 下生成 `sit-dekt-<版本>.zip`，只包含程序文件——**不含**凭据、运行产物、Chrome 用户目录与开发期遗留。

---

## 许可

MIT，见 `LICENSE`。
