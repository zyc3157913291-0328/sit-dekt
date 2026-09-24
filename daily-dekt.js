/**
 * SIT 第二课堂 × 课表 —— 每日空闲时段表生成器
 *
 * 用法：node daily-dekt.js [--no-open] [--from-file <活动列表JSON>]
 *
 * 环境变量：
 *   SIT_USER / SIT_PASS  学号与密码（也可留空，脚本会回退读 HKCU\Environment）
 *   SIT_HOME             数据目录，默认脚本所在目录（整个文件夹可直接搬走）
 *   SIT_CHROME           浏览器可执行文件（默认自动探测 Chrome/Edge）
 *   SIT_PYTHON           Python 解释器（默认自动探测 Anaconda/PATH）
 *   SIT_FIREFOX          Firefox 可执行文件（默认自动探测）
 *   SIT_SANGFOR          深信服 VPN 客户端（默认自动探测）
 *   SIT_VPN_WAIT_MS      等 VPN 就绪的上限，默认 6 分钟
 *   SIT_VPN_HELPER       vpn-autologin.py 路径（默认脚本同目录）
 *   SIT_VPN_TEARDOWN     vpn-teardown.py 路径（默认脚本同目录）
 *   SIT_KEEP_VPN         置 1 则收尾时保留 VPN 客户端（仍会收 Edge）
 *   NODE_PATH / PUPPETEER_CORE_ROOT  puppeteer-core 所在 node_modules（默认自动回退查找）
 *
 * 流程：
 *   1. 打开 xg.sit.edu.cn，若跳到 CAS 则截验证码 → 等 code.txt → 提交登录
 *   2. 打开 /hdgl/hdydlist，截获 getHdgcHdList.zf 的活动列表
 *   3. 过滤：已过期、报名已结束的一律丢弃
 *   4. 对保留的活动，从列表页 DOM 里读详情页 href，逐条进去取「活动地点」
 *   5. 读取本周课表（本地快照 CSV），与活动按「星期 × 节次」求交
 *   6. 渲染彩色 HTML 表格 → tables/第二课堂空闲-<时间戳>.html
 *   7. 清理 7 天前的旧表
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const { spawn, execSync } = require('child_process');

/**
 * 稳健加载 puppeteer-core：不依赖调用方注入 NODE_PATH，
 * 便于把这套东西搬到别的机器/别的 runner。
 * 解析顺序：常规 require（含本目录 node_modules）→ PUPPETEER_CORE_ROOT
 *          → NODE_PATH → 同机 DSH 各 profile 的 node_modules。
 *
 * 注意这里**不写死任何用户名或盘符**：DSH 的位置由 os.homedir() 推出来。
 */
function dshProfileNodeModules() {
  const roots = [];
  try {
    const base = path.join(os.homedir(), '.dsh', 'profiles');
    for (const name of fs.readdirSync(base)) roots.push(path.join(base, name, 'node_modules'));
  } catch (e) { /* 没装 DSH 就没有，属正常 */ }
  return roots;
}

function loadPuppeteer() {
  try { return require('puppeteer-core'); } catch (e) { if (e.code !== 'MODULE_NOT_FOUND') throw e; }
  const { createRequire } = require('module');
  const roots = [
    process.env.PUPPETEER_CORE_ROOT,
    ...(process.env.NODE_PATH || '').split(path.delimiter),
    ...dshProfileNodeModules(),
  ].filter(Boolean);
  for (const root of roots) {
    try { return createRequire(path.join(root, '_resolve_.js'))('puppeteer-core'); } catch (e) { /* 换下一个 */ }
  }
  throw new Error('未找到 puppeteer-core：在本目录执行 npm install，'
    + '或设置 NODE_PATH / PUPPETEER_CORE_ROOT 指向含它的 node_modules。');
}
const puppeteer = loadPuppeteer();

/** 第一个存在的路径；都没有则返回 null（便于自动探测本机浏览器/解释器） */
function firstExisting(paths) {
  for (const p of paths) {
    if (!p) continue;
    try { if (p.indexOf('\\') === -1 && p.indexOf('/') === -1) return p; // 交给 PATH 解析
          if (fs.existsSync(p)) return p; } catch { /* 忽略 */ }
  }
  return null;
}

// 数据目录：默认脚本所在目录，可用 SIT_HOME 覆盖 → 整个文件夹可整体搬走
const DIR = (process.env.SIT_HOME || __dirname).replace(/[\\/]+$/, '') + path.sep;
const PROFILE = DIR + 'profile';
const TABLES = DIR + 'tables';
const CAPTCHA = DIR + 'captcha.png';
const CODE_FILE = DIR + 'code.txt';
const FLAG = DIR + 'need-code.flag';
const RESULT = DIR + 'daily-result.txt';
// 本机私有配置（学号密码、各程序路径等）。不进版本库。
const CONFIG_LOCAL = DIR + 'config.local.json';
let LOCAL_CONFIG = null;
function localConfig() {
  if (LOCAL_CONFIG !== null) return LOCAL_CONFIG;
  try { LOCAL_CONFIG = JSON.parse(fs.readFileSync(CONFIG_LOCAL, 'utf8')); }
  catch (e) { LOCAL_CONFIG = {}; }
  return LOCAL_CONFIG;
}

/**
 * 选一个外部程序：环境变量 → config.local.json → 常见安装位置（→ PATH）。
 *
 * 机器相关的路径一律只放进 config.local.json（它已被 .gitignore 挡住），
 * 代码里只保留各台 Windows 上都一样的默认安装位置。换电脑只要改本地配置，
 * 既不必动代码，也不会把自己的盘符和用户名带进仓库。
 */
function pick(envName, cfgKey, candidates) {
  if (process.env[envName]) return process.env[envName];
  const cfg = localConfig();
  if (cfgKey && cfg[cfgKey]) return String(cfg[cfgKey]);
  return firstExisting(candidates);
}

const CHROME = pick('SIT_CHROME', 'chrome', [
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
]);
const FIREFOX = pick('SIT_FIREFOX', 'firefox', [
  'C:\\Program Files\\Mozilla Firefox\\firefox.exe',
  'C:\\Program Files (x86)\\Mozilla Firefox\\firefox.exe',
]);
const PYTHON = pick('SIT_PYTHON', 'python', [
  'python.exe',                                  // 交给 PATH 解析
  'python3.exe',
  'C:\\ProgramData\\Anaconda3\\python.exe',
  'C:\\ProgramData\\miniconda3\\python.exe',
  path.join(os.homedir(), 'anaconda3', 'python.exe'),
  path.join(os.homedir(), 'miniconda3', 'python.exe'),
  'C:\\Python313\\python.exe',
  'C:\\Python312\\python.exe',
]);
const OCR_PY = DIR + 'ocr-captcha.py';
const OCR_OUT = DIR + 'ocr-code.txt';
const NO_OPEN = process.argv.includes('--no-open');
// 校园 VPN（深信服）：不可达时由脚本拉起客户端，再自动点「登录」
const SANGFOR = pick('SIT_SANGFOR', 'sangfor', [
  'C:\\Program Files (x86)\\Sangfor\\SSL\\SangforCSClient\\SangforCSClient.exe',
  'C:\\Program Files\\Sangfor\\SSL\\SangforCSClient\\SangforCSClient.exe',
]);
const VPN_WAIT_MS = Number(process.env.SIT_VPN_WAIT_MS || 6 * 60 * 1000);
// 点「登录」助手：客户端自己只填账号密码不点登录，这个脚本负责补那一击
const VPN_HELPER = process.env.SIT_VPN_HELPER || (DIR + 'vpn-autologin.py');
// 收尾脚本：任务结束后关掉 VPN 与本次带出来的 Edge 门户页
const VPN_TEARDOWN = process.env.SIT_VPN_TEARDOWN || (DIR + 'vpn-teardown.py');
// 置 1 则收尾时保留 VPN（只收 Edge）；默认一律关掉
const KEEP_VPN = !!process.env.SIT_KEEP_VPN;

// 本学期课表快照（含调休后的实际日期）
const KB_CSV = DIR + '课表-2026-2027第一学期-按日期.csv';
// 课表刷新的原料与展开器：每次跑都重新抓一遍正方教务，避免课表变成死快照
const KB_RAW = DIR + 'kb-raw.json';
const KB_BUILD = DIR + 'build_kebiao.py';
// 正方 xnm/xqm：xqm 3=第一学期 12=第二学期
const KB_SEMS = [
  ['2025', '3', '2025-2026 第一学期'],
  ['2026', '3', '2026-2027 第一学期'],
  ['2025', '12', '2025-2026 第二学期'],
  ['2026', '12', '2026-2027 第二学期'],
];
// 校历锚点：2026-2027 秋季学期 第 n 周周一 = 2026-09-07 + 7n 天
const FALL_START = new Date(2026, 8, 7);
const FALL_FROM = new Date(2026, 8, 7);
const FALL_TO = new Date(2027, 0, 24);
const TERM_NAME = '2026-2027 学年第一学期';

// 本机私有配置（学号密码等）不进版本库；读取逻辑见文件上方的 localConfig()。

/**
 * 读凭据。优先级：环境变量 → config.local.json → HKCU\Environment。
 *
 * 为什么要落到文件：每个人用自己的学号密码，凭据不该写死在代码里，也不该混进
 * 要上传 GitHub 的仓库 —— config.local.json 已列入 .gitignore。
 * 保留注册表兜底是因为定时任务由 DSH host 派生，继承的是 host 启动时的环境块；
 * 若变量是 host 启动后才写入的，子进程根本看不到，脚本会误报"缺少环境变量"。
 */

const CRED_FROM = {};   // 变量名 -> 来源，分别记录，别让密码那次覆盖学号那次
function readSecret(name) {
  if (process.env[name]) { CRED_FROM[name] = '环境变量'; return process.env[name]; }
  const key = name === 'SIT_USER' ? 'user' : (name === 'SIT_PASS' ? 'pass' : null);
  const cfg = localConfig();
  if (key && cfg[key]) { CRED_FROM[name] = 'config.local.json'; return String(cfg[key]).trim(); }
  try {
    const out = require('child_process').execSync(
      `reg query "HKCU\\Environment" /v ${name}`,
      { encoding: 'utf8', windowsHide: true, stdio: ['ignore', 'pipe', 'ignore'] },
    );
    const m = out.match(new RegExp(`${name}\\s+REG_[A-Z_]+\\s+(.*)`));
    if (m) { CRED_FROM[name] = '注册表 HKCU\\Environment'; return m[1].trim(); }
    return '';
  } catch { return ''; }
}

const USER = readSecret('SIT_USER');
const PASS = readSecret('SIT_PASS');

/** 凭据缺失时给出的可操作指引，登录失败与启动检查共用同一段话。 */
function credentialHelp() {
  return [
    '缺少校园网账号密码，无法登录学工系统。任选一种方式提供：',
    '  A) 新建 ' + CONFIG_LOCAL + '，内容：{"user":"你的学号","pass":"你的密码"}',
    '  B) 设置环境变量 SIT_USER / SIT_PASS',
    '（该文件已在 .gitignore 中，不会被提交到 GitHub）',
  ];
}

// 离线自测：--from-file <活动列表JSON> 时跳过浏览器与登录
const FROM_FILE = (() => { const i = process.argv.indexOf('--from-file'); return i >= 0 ? process.argv[i + 1] : null; })();

/** 校园网可达性（校内系统必须挂校园 VPN） */
function reachable(host, ms = 10000) {
  return new Promise((resolve) => {
    const req = require('https').request(
      { host, port: 443, path: '/', method: 'HEAD', timeout: ms, rejectUnauthorized: false },
      (r) => { resolve(true); req.destroy(); });
    req.on('timeout', () => { req.destroy(); resolve(false); });
    req.on('error', () => resolve(false));
    req.end();
  });
}

/** 深信服客户端是否已经在运行（托盘待机、停在登录框、已连接，都算）。 */
function vpnClientRunning() {
  try {
    const out = execSync('tasklist /FI "IMAGENAME eq SangforCSClient.exe" /FO CSV /NH',
      { encoding: 'utf8', windowsHide: true, stdio: ['ignore', 'pipe', 'ignore'] });
    return /sangforcsclient\.exe/i.test(out);
  } catch { return false; }
}

/**
 * 确保校园网可达。不可达时拉起深信服客户端，并另起一个助手去点登录框上的「登录」。
 *
 * 为什么需要这个助手（2026-09-23 实测确认）：
 *   客户端带 /ShortCutAutoLogin 启动，只会把已保存的账号密码**填进输入框**
 *   （服务器下发 g_bAllowAutoLogin=0，但 g_bAllowSavePwd=1），然后就不动了，
 *   登录框会一直摆在那里等人点。定时任务撞上这种情况就是 VPN_DOWN。
 *   实测：登录框静置数分钟无任何自动动作，人工点击「登录」后 1 秒进入
 *   「正在登录，请稍候…」，随后日志出现 ProcessLoginSuccess、隧道网卡转 Up。
 *
 * 助手用的是 BM_CLICK 窗口消息，不是模拟鼠标：不依赖窗口位置、不动真实鼠标、
 * 屏幕锁定时也有效。详见 vpn-autologin.py 头部注释。
 */
async function ensureVpn() {
  const HOST = 'xg.sit.edu.cn';
  if (await reachable(HOST)) return true;
  if (!SANGFOR) { log('校园网不可达，且未找到深信服客户端（可用 SIT_SANGFOR 指定路径）'); return false; }

  // 已经在跑就别再拉一个：多开会堆出第二个客户端和第二个登录框，
  // 助手的「点登录」还可能点到废弃的那个窗口上。客户端在跑时只需叫起助手补点登录。
  if (vpnClientRunning()) {
    log('VPN 客户端已在运行，不再重复启动（只补点「登录」）');
  } else {
    log('校园网不可达，拉起 VPN 客户端: ' + SANGFOR);
    try {
      spawn(SANGFOR, ['/ShortCutAutoLogin'], { detached: true, stdio: 'ignore' }).unref();
    } catch (e) { log('拉起 VPN 客户端失败: ' + e.message); return false; }
  }

  // 助手自己会轮询：找到登录框就点，出现安全提示就关，隧道通了就退出。
  let helper = null;
  if (PYTHON && fs.existsSync(VPN_HELPER)) {
    log('启动点登录助手: ' + VPN_HELPER);
    try {
      helper = spawn(PYTHON, [VPN_HELPER, '--timeout', String(Math.round(VPN_WAIT_MS / 1000) + 30)],
        { stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
      const relay = (buf) => {
        String(buf).split(/\r?\n/).forEach((l) => {
          const t = l.trim();
          if (!t) return;
          log('  [helper] ' + t);
          // 把助手的机器可读标记翻译成给人看的可操作提示
          if (/credentials_missing/.test(t)) {
            log('  !! VPN 登录框里没有账号/密码 —— 说明客户端没开「记住密码」');
            log('     请打开深信服客户端，勾选「记住密码」并成功登录一次；');
            log('     之后本程序才能自动替你点「登录」。');
          } else if (/click_limit_reached/.test(t)) {
            log('  !! 已点满登录次数仍未连上，可能是密码已变更，或账号被锁定。');
          }
        });
      };
      helper.stdout.on('data', relay);
      helper.stderr.on('data', relay);
      helper.on('error', (e) => log('  [helper] 启动失败: ' + e.message));
    } catch (e) { log('启动点登录助手失败: ' + e.message); helper = null; }
  } else {
    log('未找到 Python 或 ' + VPN_HELPER + '，只能靠人工点「登录」');
  }

  const stopHelper = () => { if (helper) { try { helper.kill(); } catch { /* 已退出 */ } helper = null; } };
  try {
    const deadline = Date.now() + VPN_WAIT_MS;
    let waited = 0;
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 10000));
      waited += 10;
      if (await reachable(HOST)) {
        log('VPN 已就绪（共等待 ' + waited + ' 秒）');
        // 先别急着收掉助手：客户端登录成功后紧接着会弹「安全提示」，问是否允许
        // 启动 msedge.exe 打开门户页。那个弹窗不会自己消失，得由助手点掉「阻止」，
        // 否则它会一直挂在屏幕上（也会多开一个浏览器）。等它自己收尾。
        if (helper) {
          log('等点登录助手收尾（关掉安全提示）…');
          // 兜底计时器必须清掉/不持有事件循环，否则它会白白拖住进程几分钟
          await new Promise((resolve) => {
            if (helper.exitCode !== null || helper.signalCode !== null) { resolve(); return; }
            const guard = setTimeout(resolve, 40000);
            guard.unref?.();
            helper.once('exit', () => { clearTimeout(guard); resolve(); });
          });
        }
        return true;
      }
    }
    log('VPN 等待超时（' + Math.round(VPN_WAIT_MS / 1000) + ' 秒）仍未可达');
    return false;
  } finally { stopHelper(); }
}

const SLOTS = [
  { key: '1-2', label: '第1-2节', clock: '08:20-09:55', from: 0, to: 10 * 60 + 15 },
  { key: '3-4', label: '第3-4节', clock: '10:15-11:50', from: 10 * 60 + 15, to: 12 * 60 },
  { key: '5-6', label: '第5-6节', clock: '13:00-14:35', from: 12 * 60, to: 14 * 60 + 55 },
  { key: '7-8', label: '第7-8节', clock: '14:55-16:30', from: 14 * 60 + 55, to: 18 * 60 },
  { key: '9-11', label: '第9-11节', clock: '18:00-20:25', from: 18 * 60, to: 24 * 60 },
];
const WD = ['周一', '周二', '周三', '周四', '周五'];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const lines = [];
// 日志时间戳用本地时间，与表格文件名/生成时间保持一致（原先用 UTC，差 8 小时，会让人和 agent 都看糊涂）
const log = (m) => {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  const s = p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds()) + ' ' + m;
  console.log(s); lines.push(s);
};

function parseCSV(text) {
  const rows = [];
  let row = [], cur = '', q = false;
  text = text.replace(/^\ufeff/, '');
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"') { if (text[i + 1] === '"') { cur += '"'; i++; } else q = false; }
      else cur += c;
    } else if (c === '"') q = true;
    else if (c === ',') { row.push(cur); cur = ''; }
    else if (c === '\n') { row.push(cur); rows.push(row); row = []; cur = ''; }
    else if (c !== '\r') cur += c;
  }
  if (cur !== '' || row.length) { row.push(cur); rows.push(row); }
  return rows.filter((r) => r.length > 1 || (r[0] || '').trim() !== '');
}

const pad = (n) => String(n).padStart(2, '0');
const ymd = (d) => d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
const hm = (s) => { const d = new Date(String(s).replace(' ', 'T')); return isNaN(d) ? null : d.getHours() * 60 + d.getMinutes(); };
const slotOf = (min) => { for (const s of SLOTS) if (min >= s.from && min < s.to) return s.key; return null; };

/** 本周周一 */
function mondayOf(d) { const x = new Date(d.getFullYear(), d.getMonth(), d.getDate()); const w = (x.getDay() + 6) % 7; x.setDate(x.getDate() - w); return x; }

/** 截取验证码元素到 CAPTCHA 文件 */
async function grabCaptcha(page) {
  const sel = await page.evaluate(() => {
    const c = [...document.querySelectorAll('img')].find((i) => /captcha/i.test(i.src || '') || /captcha/i.test(i.id || ''));
    if (!c) return null; if (!c.id) c.id = 'auto_cap'; return '#' + c.id;
  });
  if (!sel) return false;
  const el = await page.$(sel);
  if (!el) return false;
  await el.screenshot({ path: CAPTCHA });
  return true;
}

/** 本地 OCR（离线，零 token）。用 stdio:'ignore' 规避管道限制，结果走文件。 */
function localOCR() {
  try { fs.unlinkSync(OCR_OUT); } catch (e) {}
  try {
    require('child_process').spawnSync(PYTHON, [OCR_PY, CAPTCHA, OCR_OUT], { stdio: 'ignore', timeout: 60000 });
  } catch (e) { return ''; }
  if (!fs.existsSync(OCR_OUT)) return '';
  const t = fs.readFileSync(OCR_OUT, 'utf8').trim();
  return /^[A-Za-z0-9]{3,6}$/.test(t) ? t : '';
}

/** 填表并提交，返回是否已离开 CAS 登录页 */
async function submitLogin(page, code) {
  await page.click('#username', { clickCount: 3 });
  await page.type('#username', USER, { delay: 40 });
  await page.click('#captchaResponse', { clickCount: 3 });
  await page.type('#captchaResponse', code, { delay: 60 });
  const pw = await page.$('#password');
  if (pw) { await pw.click({ clickCount: 3 }); await pw.type(PASS, { delay: 40 }); }
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'networkidle2', timeout: 60000 }).catch(() => null),
    page.evaluate(() => {
      const b = [...document.querySelectorAll('button,a,input')].find((x) => (x.innerText || x.value || '').trim() === '登录');
      if (b) b.click(); else { const f = document.querySelector('form'); if (f) f.submit(); }
    }),
  ]);
  await sleep(4500);
  return !/authserver/.test(page.url());
}

/** 兜底：等大模型把验证码写进 code.txt */
async function waitForHumanCode() {
  try { fs.unlinkSync(CODE_FILE); } catch (e) {}
  fs.writeFileSync(FLAG, '1');
  log('NEED_HUMAN_CODE（本地 OCR 连续失败；请在 5 分钟内把 4 位验证码写入 ' + CODE_FILE + '）');
  let code = null;
  for (let i = 0; i < 150; i++) {
    if (fs.existsSync(CODE_FILE)) { code = fs.readFileSync(CODE_FILE, 'utf8').trim(); if (code) break; }
    await sleep(2000);
  }
  try { fs.unlinkSync(FLAG); } catch (e) {}
  return code;
}

async function login(page) {
  log('打开学工系统');
  await page.goto('https://xg.sit.edu.cn/', { waitUntil: 'networkidle2', timeout: 90000 });
  await sleep(2500);
  if (!/authserver/.test(page.url())) { log('已有会话，无需登录（跳过验证码，零 token）'); return true; }

  // 既然跳到了认证页，就必须有账号密码 —— 这里明确报出来，别让它闷头试三轮再超时
  if (!USER || !PASS) {
    log('!! 需要重新登录，但本地没有保存校园网账号密码');
    credentialHelp().forEach((l) => log('   ' + l));
    return false;
  }

  // ---- 1) 本地 OCR，最多 3 轮，每轮失败都换一张新验证码 ----
  for (let i = 1; i <= 3; i++) {
    if (!await grabCaptcha(page)) { log('未找到验证码图片，转兜底'); break; }
    const code = localOCR();
    log('第 ' + i + ' 轮本地 OCR: ' + (code || '(识别失败)'));
    if (!code) {
      await page.reload({ waitUntil: 'networkidle2', timeout: 60000 }).catch(() => null);
      await sleep(2500);
      continue;
    }
    if (await submitLogin(page, code)) { log('登录成功（本地 OCR，零 token）'); return true; }
    log('第 ' + i + ' 轮登录失败，换验证码重试');
    await page.reload({ waitUntil: 'networkidle2', timeout: 60000 }).catch(() => null);
    await sleep(3000);
  }
  if (!/authserver/.test(page.url())) return true;
  log('本地 OCR 连续 3 轮未成功 —— 可能是验证码识别问题，也可能是学号/密码已变更。');

  // ---- 2) 兜底：交给大模型读图 ----
  if (!await grabCaptcha(page)) { log('兜底也拿不到验证码图，放弃'); return false; }
  const code = await waitForHumanCode();
  if (!code) { log('NO_CODE_TIMEOUT'); return false; }
  log('收到兜底验证码: ' + code);
  if (await submitLogin(page, code)) { log('登录成功（模型兜底）'); return true; }
  log('兜底登录仍失败');
  return false;
}


/**
 * 刷新课表快照。
 *
 * 为什么需要：原来课表是一份 2026-09-21 的死快照，老师临时调课、换教室都不会反映，
 * 「空闲」判断于是会出错 —— 轻则错过空出来的时段，重则把实际有课的格子报成空闲
 * 并推荐你去报名。
 *
 * 数据源：正方教务 v9 学生课表接口
 *   POST /jwglxt/kbcx/xskbcx_cxXsKb.html?gnmkdm=N2151&su=<学号>
 *   body: xnm=<学年>&xqm=<学期码>
 * 该响应除 kbList（课表）外还带 sjkList（调课/实际调整），两者都原样存进
 * kb-raw.json，交给 build_kebiao.py 展开成「按日期」CSV。
 *
 * 登录：CAS cookie 在 *.sit.edu.cn 之间共享，xg 登录过之后直接访问教务的
 * 单点登录入口即可，不必再输一次密码、也不会再要验证码。
 *
 * 失败不致命：抓不到就沿用旧快照，只告警，不让整个取数任务失败。
 */
async function refreshKb(page) {
  log('刷新课表：进入教务系统');
  try {
    await page.goto('https://jwxt.sit.edu.cn/sso/jziotlogin', { waitUntil: 'networkidle2', timeout: 90000 });
    await sleep(9000);
    if (/authserver/.test(page.url())) { log('课表刷新失败：单点登录跳回了认证页，沿用旧快照'); return false; }
    // 「已阅读」提示会盖住页面，先点掉
    await page.evaluate(() => {
      const b = [...document.querySelectorAll('a,button,span,div')]
        .find((e) => (e.innerText || '').trim() === '已阅读');
      if (b) b.click();
    }).catch(() => null);
    await sleep(2500);
  } catch (e) { log('课表刷新失败：进入教务系统异常 ' + e.message); return false; }

  // 诊断开关：把教务菜单树拉下来找「调课/停课」模块入口。
  // 正方把调课信息放在哪个 gnmkdm 各校不同，靠这份菜单定位最靠谱。
  if (process.env.SIT_KB_DEBUG) {
    try {
      const menu = await page.evaluate(async () => {
        const res = await fetch('/jwglxt/xtgl/index_initMenu.html', { credentials: 'include' });
        return { status: res.status, body: (await res.text()).slice(0, 400000) };
      });
      fs.writeFileSync(DIR + 'jw-menu.html', menu.body, 'utf8');
      log('  菜单已保存 jw-menu.html（HTTP ' + menu.status + '，' + menu.body.length + ' 字符）');
    } catch (e) { log('  菜单抓取失败: ' + e.message); }

    // 菜单里没有独立的「调课」模块，说明调课显示在课表视图内部。
    // 探一下「学生课表查询（按周次）」N2154 自己的数据接口。
    try {
      const probe = await page.evaluate(async () => {
        const r = await fetch('/jwglxt/kbcx/xskbcxZccx_cxXskbcxIndex.html?gnmkdm=N2154',
          { credentials: 'include' });
        const html = await r.text();
        const srcs = [...new Set([...html.matchAll(/src=["']([^"']+\.js[^"']*)["']/g)].map((m) => m[1]))];
        const texts = [html];
        for (const s of srcs.slice(0, 30)) {
          try {
            const rr = await fetch(s, { credentials: 'include' });
            texts.push(await rr.text());
          } catch (e) { /* 单个 js 拿不到不影响 */ }
        }
        const re = /\/[A-Za-z0-9_]+\/[A-Za-z0-9_]+_cx[A-Za-z0-9_]+\.html/g;
        const found = [];
        for (const t of texts) for (const m of t.matchAll(re)) found.push(m[0]);
        return { status: r.status, pageLen: html.length, jsCount: srcs.length,
                 endpoints: [...new Set(found)] };
      });
      log('  N2154 探针：HTTP ' + probe.status + '，页面 ' + probe.pageLen +
          ' 字符，' + probe.jsCount + ' 个 js');
      log('  N2154 页内接口: ' + (probe.endpoints.join('  ') || '(无)'));
    } catch (e) { log('  N2154 探针失败: ' + e.message); }
  }

  let results = {};
  if (fs.existsSync(KB_RAW)) {
    try { results = JSON.parse(fs.readFileSync(KB_RAW, 'utf8')); } catch (e) { results = {}; }
  }

  let got = 0;
  for (const [xnm, xqm, label] of KB_SEMS) {
    try {
      const r = await page.evaluate(async (xnm, xqm, user) => {
        const url = '/jwglxt/kbcx/xskbcx_cxXsKb.html?gnmkdm=N2151&su=' + encodeURIComponent(user);
        const res = await fetch(url, {
          method: 'POST', credentials: 'include',
          headers: {
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'X-Requested-With': 'XMLHttpRequest',
          },
          body: 'xnm=' + xnm + '&xqm=' + xqm,
        });
        return { status: res.status, body: (await res.text()).slice(0, 400000) };
      }, xnm, xqm, USER);

      let n = -1, extra = '';
      try {
        const j = JSON.parse(r.body);
        n = (j.kbList || []).length;
        // 正方把「调课/换教室」放在 sjkList 里，一并报出来便于确认拿没拿到
        const sj = j.sjkList || j.tkjgList || null;
        const sample = sj && sj.length ? JSON.stringify(sj[0]).slice(0, 500) : '';
        if (sj) extra = ' 调课=' + sj.length + (sample ? ' 样本:' + sample : '');
        else extra = ' 顶层字段:' + Object.keys(j).join(',');
      } catch (e) { /* 非 JSON，多半是没登录 */ }

      if (r.status === 200 && n >= 0) {
        results[label] = r;
        got++;
        log('  ' + label + '：课表 ' + n + ' 条' + extra);
      } else {
        log('  ' + label + '：HTTP ' + r.status + (n < 0 ? '（响应非 JSON，可能未登录）' : ''));
      }
    } catch (e) { log('  ' + label + ' 失败: ' + e.message); }
  }

  if (!got) { log('课表刷新失败：一学期都没拿到，沿用旧快照'); return false; }
  fs.writeFileSync(KB_RAW, JSON.stringify(results, null, 1), 'utf8');

  // 用 Python 把「周次课表」展开成「按日期」，并套用校历调休
  try {
    const out = execSync('"' + PYTHON + '" "' + KB_BUILD + '"',
      { encoding: 'utf8', windowsHide: true, timeout: 180000 });
    const tail = String(out).trim().split(/\r?\n/).filter(Boolean).slice(-2).join(' | ');
    if (tail) log('  ' + tail);
    log('课表已刷新: ' + path.basename(KB_CSV));
    return true;
  } catch (e) {
    log('课表展开失败（沿用旧 CSV）: ' + String(e.message || '').split('\n')[0]);
    return false;
  }
}

/** 用 Firefox 打开生成的表格 */
function openFirefox(p) {
  try {
    if (!FIREFOX || !fs.existsSync(FIREFOX)) { log('未找到 firefox，跳过打开表格'); return; }
    spawn(FIREFOX, [p], { detached: true, stdio: 'ignore' }).unref();
    log('已用 Firefox 打开表格');
  } catch (e) { log('Firefox 打开失败: ' + e.message); }
}

/** 自归档：脚本直接归档自己这次运行的会话（省掉 agent 编排，零 token） */
async function selfArchive() {
  const sid = process.env.DSH_SESSION_ID || '';
  const base = process.env.DSH_WEB_URL || 'http://127.0.0.1:3083';
  // 定时会话前缀：automation 用 dsh-automation-session-，内置 scheduler 用 task-
  if (!/^(dsh-automation-session-|task-)/.test(sid)) { log('自归档: 非定时任务会话，跳过'); return; }
  try {
    const res = await fetch(base + '/api/desktop/dsh-tauri-session/session/archive', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sessionId: sid }),
    });
    log('自归档: HTTP ' + res.status);
  } catch (e) { log('自归档失败: ' + e.message); }
}

/**
 * 记录**跑 VPN 之前**就已存在的 Edge 主进程 PID，作为收尾时的基线。
 *
 * 这是「不误杀用户浏览器」的关键：收尾脚本只关不在基线里的 msedge 主进程，
 * 也就是只关本次运行期间才被拉起来的那些。基线必须在拉起客户端**之前**取。
 */
function snapshotEdgePids() {
  try {
    const out = execSync('tasklist /FI "IMAGENAME eq msedge.exe" /FO CSV /NH',
      { encoding: 'utf8', windowsHide: true, stdio: ['ignore', 'pipe', 'ignore'] });
    const pids = [];
    String(out).split(/\r?\n/).forEach((line) => {
      const m = line.match(/^"msedge\.exe","(\d+)"/i);
      if (m) pids.push(m[1]);
    });
    return pids;
  } catch { return []; }
}

const EDGE_BASELINE = snapshotEdgePids();

/**
 * 收尾：关掉 VPN 客户端，以及本次运行带出来的 Edge 门户页。
 *
 * Edge 部分刻意保守 —— 只有「不在基线里」且「带 VPN 特征」的进程才会被关，
 * 详细归因规则见 vpn-teardown.py。宁可漏关，也不误杀用户自己开的浏览器。
 */
async function teardown() {
  if (FROM_FILE) { log('收尾: 离线自测模式，跳过'); return; }
  if (!PYTHON || !fs.existsSync(VPN_TEARDOWN)) {
    log('收尾: 未找到 Python 或 ' + VPN_TEARDOWN + '，跳过');
    return;
  }
  log('收尾: 关闭 VPN 与 Edge 门户页（基线 Edge 进程 ' + EDGE_BASELINE.length + ' 个）');
  const argv = [VPN_TEARDOWN, '--keep', EDGE_BASELINE.join(',')];
  if (KEEP_VPN) argv.push('--keep-vpn');
  await new Promise((resolve) => {
    let done = false;
    const finish = () => { if (!done) { done = true; resolve(); } };
    const p = spawn(PYTHON, argv, { stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
    const relay = (buf) => String(buf).split(/\r?\n/)
      .forEach((l) => { if (l.trim()) log('  [teardown] ' + l.trim()); });
    p.stdout.on('data', relay);
    p.stderr.on('data', relay);
    p.on('error', (e) => { log('  [teardown] 启动失败: ' + e.message); finish(); });
    // 兜底：收尾最长 2 分钟，绝不把整个任务卡死。
    // 必须 clearTimeout + unref：否则这个计时器会拖住事件循环，
    // 让「日志已经 done」的任务进程白活两分钟才退出。
    const guard = setTimeout(() => { try { p.kill(); } catch { /* 已退出 */ } finish(); }, 120000);
    guard.unref?.();
    p.on('close', () => { clearTimeout(guard); finish(); });
  });
}

(async () => {
  fs.mkdirSync(TABLES, { recursive: true });

  // 凭据来源要显式报出来：登录失败时第一件要确认的就是它。
  if (USER && PASS) {
    const u = CRED_FROM.SIT_USER || '?', p = CRED_FROM.SIT_PASS || '?';
    log('凭据来源: ' + (u === p ? u : ('学号=' + u + ' 密码=' + p)));
  } else {
    // 注意：这里**不能直接退出**。CAS 会话可能还缓存着（profile/ 里的 cookie），
    // 那种情况下根本不需要账号密码。真正需要时由 login() 兜住并给出指引。
    log('提示: 本地没有校园网账号密码（缓存会话若仍有效则不影响本次运行）');
    credentialHelp().forEach((l) => log('   ' + l));
  }

  let browser = null, page = null, listBody = null, tablePath = '';

  if (FROM_FILE) {
    log('离线自测模式：活动列表来自 ' + FROM_FILE);
    const rawTxt = fs.readFileSync(FROM_FILE, 'utf8');
    const parsed = JSON.parse(rawTxt);
    listBody = Array.isArray(parsed) ? JSON.stringify({ data: { resultList: parsed } }) : rawTxt;
  } else {
    log('检查校园网可达性 xg.sit.edu.cn');
    if (!await ensureVpn()) {
      const msg = 'VPN_DOWN: xg.sit.edu.cn 不可达，自动拉起 VPN 并等待超时。请手动连接校园 VPN 后重试。';
      log(msg); fs.writeFileSync(RESULT, lines.join('\n') + '\n' + msg); process.exit(3);
    }
    log('校园网可达');

    if (!CHROME) {
      const msg = '未找到 Chrome/Edge。请安装 Chrome，或设置环境变量 SIT_CHROME 指向浏览器可执行文件。';
      log(msg); fs.writeFileSync(RESULT, lines.join('\n') + '\n' + msg); process.exit(4);
    }

    browser = await puppeteer.launch({
      executablePath: CHROME, headless: true, userDataDir: PROFILE,
      args: ['--no-sandbox', '--disable-dev-shm-usage', '--ignore-certificate-errors', '--window-size=1600,1000'],
    });
    page = (await browser.pages())[0] || await browser.newPage();
    await page.setViewport({ width: 1600, height: 1000 });
    page.on('response', async (res) => {
      const u = res.url();
      if (!/getHdgcHdList\.zf/.test(u)) return;
      try { const t = await res.text(); if (t && t.length < 500000) listBody = t; } catch (e) {}
    });

    if (!await login(page)) { fs.writeFileSync(RESULT, lines.join('\n')); await browser.close(); return; }

    log('打开活动报名页');
    await page.goto('https://xg.sit.edu.cn/hdgl/hdydlist', { waitUntil: 'networkidle2', timeout: 60000 });
    await sleep(8000);
    if (!listBody) { log('未截获活动列表接口，刷新重试'); await page.reload({ waitUntil: 'networkidle2' }); await sleep(10000); }
    if (!listBody) {
      log('!! 活动列表依然为空');
      fs.writeFileSync(RESULT, lines.join('\n')); await browser.close(); return;
    }
  }

  try {
    const j = JSON.parse(listBody);
    const d = j.data || {};
    const now = new Date();
    const all = [];
    (d.resultList || []).forEach((dl) => (dl.lblist || []).forEach((lb) => (lb.hdlist || []).forEach((h) => {
      all.push({
        id: h.id, dlmc: dl.dlmc, lbmc: lb.lbmc, hdmc: (h.hdmc || '').trim(), xmmc: h.xmmc || '',
        zbfmc: h.zbfmc || '', hdkssj: h.hdkssj || '', hdjssj: h.hdjssj || '',
        hdbmkssj: h.hdbmkssj || '', hdbmjzsj: h.hdbmjzsj || '', hdbmsfjs: String(h.hdbmsfjs || ''),
      });
    })));
    log('活动总数: ' + all.length);

    const kept = all.filter((a) => {
      const st = a.hdkssj ? new Date(a.hdkssj.replace(' ', 'T')) : null;
      const due = a.hdbmjzsj ? new Date(a.hdbmjzsj.replace(' ', 'T')) : null;
      if (st && st < now) return false;                        // 已过期
      if (a.hdbmsfjs === '1') return false;                     // 报名已结束
      if (due && due < now) return false;                       // 报名已截止
      return true;
    });
    log('过滤后（未过期且可报名）: ' + kept.length);

    // 详情页：这个 SPA 用 history 模式，列表里没有可扫的 href。
    // 详情路由是 /hdgl/hdxq/<活动id>，可直接 goto。
    // 注意页面把「活动地点\n:\n值」渲染成三行，要先折叠再匹配。
    for (const a of kept) {
      if (!page) { a.hddd = ''; continue; }
      try {
        await page.goto('https://xg.sit.edu.cn/hdgl/hdxq/' + a.id, { waitUntil: 'networkidle2', timeout: 60000 });
        await sleep(4500);
        const info = await page.evaluate(() => {
          const t = (document.body.innerText || '').replace(/\n{2,}/g, '\n');
          const flat = t.replace(/\n[ \t]*[:：][ \t]*\n/g, ': ');
          const m = flat.match(/活动地点[:：]\s*([^\n]+)/);
          return { hddd: m ? m[1].trim() : '' };
        });
        a.hddd = info.hddd;
        if (!a.hddd) log('  未取到地点: ' + a.hdmc.slice(0, 24));
      } catch (e) { a.hddd = ''; }
    }
    log('详情抓取完成，拿到地点的: ' + kept.filter((a) => a.hddd).length + '/' + kept.length);

    // ---- 刷新课表（含调课 / 换教室）----
    // 放在活动抓完之后再切到教务系统：两者不同域，先把学工这边的活干完更稳。
    // 离线自测模式（--from-file）没有浏览器，跳过。
    if (page) await refreshKb(page); else log('离线自测模式：跳过课表刷新');

    // ---- 本周课表 ----
    const mon = mondayOf(now), sun = new Date(mon); sun.setDate(mon.getDate() + 6);
    const inTerm = now >= FALL_FROM && now <= FALL_TO;
    // 第0周 = 2026-09-07 那一周
    const weekNo = inTerm ? Math.round((mon - mondayOf(FALL_START)) / 86400000 / 7) : null;

    let courses = [];
    let kbWarn = '';
    if (!inTerm) {
      kbWarn = '当前日期不在 ' + TERM_NAME + ' 范围内（' + ymd(FALL_FROM) + ' ~ ' + ymd(FALL_TO) + '），本地课表快照无对应数据。';
    } else if (!fs.existsSync(KB_CSV)) {
      kbWarn = '课表快照不存在: ' + KB_CSV;
    } else {
      const rows = parseCSV(fs.readFileSync(KB_CSV, 'utf8'));
      const head = rows.shift();
      const ix = {}; head.forEach((h, i) => ix[h.trim()] = i);
      courses = rows.map((r) => ({
        date: (r[ix['日期']] || '').trim(), wd: (r[ix['星期']] || '').trim(),
        jc: (r[ix['节次']] || '').trim(), kcmc: (r[ix['课程名称']] || '').trim(),
        xm: (r[ix['教师']] || '').trim(), cdmc: (r[ix['场地']] || '').trim(),
        slot: null,
      })).filter((c) => c.date >= ymd(mon) && c.date <= ymd(sun));
      courses.forEach((c) => { const n = parseInt(c.jc.split('-')[0], 10); c.slot = SLOTS.find((s) => s.key === c.jc) ? c.jc : (SLOTS.find((s) => parseInt(s.key.split('-')[0], 10) === n) || {}).key || null; });
    }
    log('本周 ' + ymd(mon) + ' ~ ' + ymd(sun) + '（第 ' + weekNo + ' 周）课程数: ' + courses.length + (kbWarn ? '  ⚠ ' + kbWarn : ''));

    // ---- 活动归位 ----
    const inWeek = [], later = [];
    for (const a of kept) {
      const st = new Date(a.hdkssj.replace(' ', 'T'));
      const wdIdx = (st.getDay() + 6) % 7;                     // 0=周一
      const slot = slotOf(st.getHours() * 60 + st.getMinutes());
      if (wdIdx > 4 || !slot) { later.push(a); continue; }      // 周末或无对应节次
      if (ymd(st) < ymd(mon) || ymd(st) > ymd(sun)) { later.push(a); continue; }
      a._wd = wdIdx; a._slot = slot;
      a._conflict = courses.some((c) => c.wd === WD[wdIdx] && c.slot === slot);
      inWeek.push(a);
    }
    log('本周可去活动: ' + inWeek.length + '（其中与课程冲突 ' + inWeek.filter((a) => a._conflict).length + '），本周外/周末: ' + later.length);

    // ---- 渲染 HTML ----
    const stamp = ymd(now).replace(/-/g, '') + '-' + pad(now.getHours()) + pad(now.getMinutes());
    const out = path.join(TABLES, '第二课堂空闲-' + stamp + '.html');
    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    const cellCourses = (wd, slot) => courses.filter((c) => c.wd === wd && c.slot === slot);
    const cellActs = (wd, slot) => inWeek.filter((a) => a._wd === WD.indexOf(wd) && a._slot === slot);

    // 每列状态：已过 / 今天 / 未来 —— 表头与整列底色据此区分。
    // 按本地日期比较（ymd 用本地时区），所以「今天」与用户看到的一致。
    const todayStr = ymd(now);
    const dayDate = WD.map((_, i) => ymd(new Date(mon.getTime() + i * 86400000)));
    const dayKind = dayDate.map((d) => (d < todayStr ? 'past' : (d === todayStr ? 'today' : 'future')));

    let h = [];
    h.push('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">');
    h.push('<title>第二课堂空闲时段表 ' + stamp + '</title><style>');
    h.push(`body{font-family:"Microsoft YaHei",system-ui,sans-serif;margin:24px;background:#f1f5f9;color:#0f172a}
h1{font-size:20px;margin:0 0 4px}
.sub{color:#475569;font-size:13px;margin-bottom:16px}
table{border-collapse:collapse;width:100%;background:#fff;box-shadow:0 1px 4px rgba(15,23,42,.12)}
th,td{border:1px solid #cbd5e1;padding:8px 10px;vertical-align:top;font-size:13px}
th{background:#1e293b;color:#fff;font-weight:600;text-align:center}
td.slot{background:#e2e8f0;font-weight:600;text-align:center;white-space:nowrap;width:118px}
.course{background:#dbeafe;border-left:4px solid #2563eb;border-radius:4px;padding:5px 7px;margin:3px 0}
.act{background:#dcfce7;border-left:4px solid #16a34a;border-radius:4px;padding:5px 7px;margin:3px 0}
.act.bad{background:#ffedd5;border-left-color:#ea580c}
.free{color:#94a3b8;font-size:12px}
.b{font-weight:600}
.meta{color:#475569;font-size:12px}
.legend span{display:inline-block;margin-right:14px;font-size:13px}
.sw{display:inline-block;width:12px;height:12px;border-radius:3px;vertical-align:-1px;margin-right:4px}
.note{margin-top:16px;background:#fff;border:1px solid #cbd5e1;border-radius:6px;padding:12px 16px;font-size:13px}
.note li{margin:4px 0}
.warn{background:#fef3c7;border-color:#f59e0b}
th .meta{color:rgba(255,255,255,.72)}
th.past{background:#94a3b8}
th.today{background:#4338ca}
td.past{background:#e5e7eb}
td.today{background:#e0e7ff}
td.past .course,td.past .act,td.past .free{opacity:.6}
.badge{display:inline-block;margin-top:3px;padding:0 6px;border-radius:8px;font-size:11px;font-weight:400;background:rgba(255,255,255,.28)}`);
    h.push('</style></head><body>');
    h.push('<h1>第二课堂空闲时段表</h1>');
    h.push('<div class="sub">生成时间 ' + now.toLocaleString('zh-CN') + ' · 学期 ' + (inTerm ? TERM_NAME : '（学期外）') +
      (inTerm ? ' · 第 ' + weekNo + ' 周' : '') + ' · 覆盖 ' + ymd(mon) + ' ~ ' + ymd(sun) + '</div>');
    // 图例：颜色含义必须自己说清楚，否则灰列/靛列会被当成随机样式
    h.push('<div class="legend"><span><i class="sw" style="background:#dbeafe;border-left:3px solid #2563eb"></i>本周课程</span>' +
      '<span><i class="sw" style="background:#dcfce7;border-left:3px solid #16a34a"></i>可去活动（无冲突）</span>' +
      '<span><i class="sw" style="background:#ffedd5;border-left:3px solid #ea580c"></i>活动与课程时间冲突</span>' +
      '<span><i class="sw" style="background:#fff;border:1px solid #cbd5e1"></i>空闲</span>' +
      '<span><i class="sw" style="background:#e5e7eb;border:1px solid #cbd5e1"></i>已过日期</span>' +
      '<span><i class="sw" style="background:#e0e7ff;border:1px solid #cbd5e1"></i>今天</span></div>');

    if (kbWarn) h.push('<div class="note warn">⚠ ' + esc(kbWarn) + '</div>');

    h.push('<table><thead><tr><th>时间段</th>');
    WD.forEach((w, i) => {
      const k = dayKind[i];
      h.push('<th class="' + k + '">' + w + '<div class="meta">' + dayDate[i] + '</div>' +
        (k === 'past' ? '<div class="badge">已过</div>'
          : k === 'today' ? '<div class="badge">今天</div>' : '') + '</th>');
    });
    h.push('</tr></thead><tbody>');
    for (const s of SLOTS) {
      h.push('<tr><td class="slot">' + s.label + '<div class="meta">' + s.clock + '</div></td>');
      for (let wi = 0; wi < WD.length; wi++) {
        const w = WD[wi];
        const cs = cellCourses(w, s.key), as = cellActs(w, s.key);
        h.push('<td class="' + dayKind[wi] + '">');
        cs.forEach((c) => h.push('<div class="course"><div class="b">' + esc(c.kcmc) + '</div>' +
          '<div class="meta">' + esc(c.jc) + ' 节 · ' + esc(c.cdmc) + (c.xm ? ' · ' + esc(c.xm) : '') + '</div></div>'));
        as.forEach((a) => h.push('<div class="act' + (a._conflict ? ' bad' : '') + '"><div class="b">' + esc(a.hdmc) + '</div>' +
          '<div class="meta">' + esc(a.hdkssj.slice(11, 16)) + '-' + esc((a.hdjssj || '').slice(11, 16)) + ' · ' + esc(a.hddd || '地点学校另行安排') + '</div>' +
          '<div class="meta">' + esc(a.dlmc + '/' + a.lbmc) + ' · 主办 ' + esc(a.zbfmc) + ' · 报名截止 ' + esc((a.hdbmjzsj || '').slice(5, 16)) + '</div></div>'));
        if (!cs.length && !as.length) h.push('<div class="free">空闲</div>');
        h.push('</td>');
      }
      h.push('</tr>');
    }
    h.push('</tbody></table>');

    h.push('<div class="note"><div class="b">本周之外 / 周末的可报名活动（' + later.length + '）</div><ul>');
    if (!later.length) h.push('<li>无</li>');
    later.slice(0, 40).forEach((a) => h.push('<li>' + esc(a.hdkssj.slice(5, 16)) + ' · ' + esc(a.hdmc) +
      ' · ' + esc(a.hddd || '地点学校另行安排') + ' · ' + esc(a.dlmc + '/' + a.lbmc) + ' · 报名截止 ' + esc((a.hdbmjzsj || '').slice(5, 16)) + '</li>'));
    h.push('</ul></div>');

    h.push('<div class="note"><div class="b">口径说明</div><ul>' +
      '<li>活动已过滤：活动时间早于当前、报名已结束、报名已截止的均不列出。</li>' +
      '<li>「可去活动」指落在我空闲时段（该格无课）的活动；与课程同格的活动标为冲突色，仅供参考。</li>' +
      '<li>只排周一至周五；周末与下周及以后的活动列在下方。</li>' +
      '<li>列底色：<b>淡灰 = 已过去的日期</b>，<b>靛蓝 = 今天</b>，白色 = 本周尚未到来的日期。</li>' +
      '<li>课表来自本地快照（含国庆调休后的实际日期），活动数据实时抓取。</li></ul></div>');
    h.push('</body></html>');

    fs.writeFileSync(out, h.join('\n'), 'utf8');
    log('已生成: ' + out);

    // ---- 清理 7 天前 ----
    const cutoff = Date.now() - 7 * 86400000;
    let removed = 0;
    fs.readdirSync(TABLES).forEach((f) => {
      if (!/^第二课堂空闲-.*\.html$/.test(f)) return;
      const p = path.join(TABLES, f);
      try { if (fs.statSync(p).mtimeMs < cutoff) { fs.unlinkSync(p); removed++; } } catch (e) {}
    });
    log('清理 7 天前的旧表: ' + removed + ' 个');

    fs.writeFileSync(path.join(TABLES, 'last-table.txt'), out, 'utf8');
    tablePath = out;
    // ASCII 摘要：控制台常把中文输出弄成乱码，这行保证 agent 能稳定拿到数字
    const conflict = inWeek.filter((a) => a._conflict).length;
    log('SUMMARY kept=' + kept.length + ' inweek=' + inWeek.length + ' conflict=' + conflict +
        ' free_inweek=' + (inWeek.length - conflict) + ' later=' + later.length + ' removed=' + removed);
    fs.writeFileSync(RESULT, lines.join('\n') + '\nTABLE=' + out);
    if (!NO_OPEN) openFirefox(out);
    console.log('TABLE=' + out);
  } catch (e) {
    log('ERROR: ' + e.message);
    fs.writeFileSync(RESULT, lines.join('\n') + '\nERROR ' + e.stack);
  } finally {
    if (browser) await browser.close();
    await selfArchive();
    // 收尾：把 VPN 与本次带出来的 Edge 门户页清掉（放在重写 RESULT 之前，
    // 这样收尾日志也会进 daily-result.txt，明早排查看得到）
    await teardown();
    // 重写结果文件：把自归档结论也带上，让 daily-result.txt 自身完整
    try { fs.writeFileSync(RESULT, lines.join('\n') + (tablePath ? '\nTABLE=' + tablePath : '')); } catch (e) {}
  }
})();
