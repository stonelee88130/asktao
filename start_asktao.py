# -*- coding: utf-8 -*-
"""
《问道》自动化脚本启动器 —— 适配雷电模拟器 (LDPlayer)

它做四件事:
  1. 自动找到雷电自带 adb, 连上模拟器, 检查《问道》是否在前台
  2. 把 task/ 下老脚本里的“电脑屏幕绝对坐标”换算成模拟器内坐标, 用 adb input tap 点击
  3. 给老脚本补上本机没装的依赖(autopy / win32api / 百度OCR / 截图),
     所以 masterTask.py、arena.py、joyByHelpingPeople.py、shuaDao.py 一行都不用改
  4. 提供 试点击 / 坐标网格 / 空跑 等校准工具, 运行中随时按 ESC 中断

用法:
    python start_asktao.py              # 交互菜单
    python start_asktao.py 1            # 直接跑 1 号任务(师门任务)
    python start_asktao.py --check      # 环境自检
    python start_asktao.py --grid       # 生成坐标网格预览图(用来重新校准坐标)
    python start_asktao.py --mark 771,203 --mark 1338,650   # 看某坐标落在画面哪里
    python start_asktao.py --tap 771,203                    # 试点击一个原始坐标
    python start_asktao.py --shot       # 截取模拟器当前画面
    python start_asktao.py --dry-run --time-scale 30 1      # 空跑冒烟测试(不真的点)
"""

import argparse
import configparser
import ctypes
import io
import os
import re
import runpy
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime

try:
    from PIL import Image, ImageDraw
except ImportError:                                     # pragma: no cover
    Image = ImageDraw = None

# ---------------------------------------------------------------- 常量与路径

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TASK_DIR = os.path.join(BASE_DIR, 'task')
RES_DIR = os.path.join(BASE_DIR, 'resource')
LOG_DIR = os.path.join(BASE_DIR, 'logs')
TMP_DIR = os.path.join(LOG_DIR, 'tmp')
# 任务运行时的截图存这里, 不写进 resource/ —— 免得覆盖作者留下的参考图
CAP_DIR = os.path.join(LOG_DIR, 'capture')
CONFIG_PATH = os.path.join(BASE_DIR, 'config.ini')
# 多开用: 当前实例的短标签(设备号), 用于分目录/分文件名
INSTANCE_TAG = u''

CREATE_NO_WINDOW = 0x08000000
IS_WIN = (os.name == 'nt')

# 真实 sleep 的引用: 脚本里的 time.sleep 会被换成可中断版本, 基础设施代码用这个
_real_sleep = time.sleep

# 雷电模拟器常见的 adb 位置(找不到再用 PATH 里的 adb)
ADB_CANDIDATES = [
    r'D:\leidian\LDPlayer14\adb.exe',
    r'D:\leidian\LDPlayer9\adb.exe',
    r'C:\leidian\LDPlayer14\adb.exe',
    r'C:\leidian\LDPlayer9\adb.exe',
    r'C:\Program Files\LDPlayer\LDPlayer9\adb.exe',
    r'D:\ChangZhi\LDPlayer\adb.exe',
    r'C:\LDPlayer\LDPlayer9\adb.exe',
]

# 任务清单: key 用于命令行和菜单, file 对应 task/ 目录下的原脚本
TASKS = [
    {'key': '1', 'name': '师门任务(新版)', 'native': 'shimen', 'ocr': True,
     'desc': '点活动→师门"前往"→反复点任务栏"下一步", 游戏自动跑图打怪自动战斗'},
    {'key': '5', 'name': '除暴任务(新版)', 'native': 'chubao', 'ocr': True,
     'desc': '日常活动里的除暴: 找李总兵接任务, 反复点任务栏"下一步"自动打强盗'},
    {'key': '9', 'name': '师门任务(2018旧脚本)', 'file': 'masterTask.py', 'ocr': False,
     'desc': '界面已改版, 该脚本结构失效, 仅作参考'},
    {'key': '7', 'name': '修炼·修行(新版)', 'native': 'xiulian', 'ocr': True, 'rounds': 80,
     'desc': '日常活动【修炼】→ 修行; 需要组队会自动匹配; 接完一直自动打, 只监控不点击'},
    {'key': '2', 'name': '竞技场(新版)', 'native': 'arena', 'ocr': True, 'rounds': 5,
     'desc': '活动→竞技场前往→竞技使者→连打对手, 打满挑战次数; 可选等级已解锁'},
    {'key': '10', 'name': '竞技场(2018旧脚本)', 'file': 'arena.py', 'ocr': False,
     'desc': '坐标已失效, 仅作参考'},
    {'key': '6', 'name': '助人为乐(新版)', 'native': 'zhuren', 'ocr': True,
     'desc': '白帮忙处领赏, 全屏OCR认委托人, 之后自动跑'},
    {'key': '8', 'name': '助人为乐(2018旧脚本)', 'file': 'joyByHelpingPeople.py', 'ocr': True,
     'desc': '截图区域写死, 新版已失效, 仅作参考'},
    {'key': '4', 'name': '刷道(伏魔)', 'file': 'shuaDao.py', 'ocr': True,
     'desc': '便捷组队匹配, 反复伏魔(队长模式)'},
]

DEFAULT_CONFIG = u"""\
; 《问道》自动化启动器配置 —— 文件必须保存为 UTF-8 编码
; 注释以 ; 开头, 值后面不要留多余空格

[emulator]
; 雷电自带 adb 的完整路径, 留空 = 自动查找常见位置
adb_path =
; 设备序列号, 留空 = 自动选第一台在线设备(雷电一般是 emulator-5554)
serial =
; 游戏包名(问道-雷霆)
package = com.gbits.atm.leiting
; 游戏主 Activity, 用于 --launch 拉起游戏
activity = org.cocos2dx.lua.AppActivity
; 点击方式:
;   adb   = 用 adb 注入触摸事件(推荐, 不依赖窗口位置/焦点, 模拟器最小化也能点)
;   mouse = 把坐标换算到模拟器窗口上用鼠标点(某些游戏屏蔽 adb 注入时改用这个)
input_mode = adb

; ---------- 坐标换算(重要) ----------
; task/ 里的老脚本用的是“电脑屏幕绝对坐标”, 下面四项描述当时游戏画面在屏幕上的位置。
; 默认值是从 resource/ 里的参考截图反推出来的:
;   夜神模拟器 6.2.2.3 + 1280x720 画质, 窗口在屏幕 (385,154), 安卓画面从 (388,194) 开始, 大小 1179x661
; 换成本机雷电(1600x900)后按比例缩放即可, 一般误差很小。
; 若点击位置整体偏移, 用  python start_asktao.py --grid  生成网格图核对后修改下面四项或微调 offset。
ref_x = 388        ; 安卓画面左上角在电脑屏幕上的 X
ref_y = 194        ; 安卓画面左上角在电脑屏幕上的 Y
ref_w = 1179       ; 安卓画面在电脑屏幕上的宽度
ref_h = 661        ; 安卓画面在电脑屏幕上的高度
offset_x = 0       ; 换算后再左右微调(像素)
offset_y = 0       ; 换算后再上下微调(像素)

[run]
; 启动任务前的倒计时秒数(期间可按 ESC 取消)
countdown = 5
; 每次点击后的间隔(秒), 游戏卡顿时可调大
tap_interval = 0.15
; 是否输出详细日志
verbose = true
; OCR 截图放大倍数(小图放大后识别率更高)
ocr_upscale = 1.5
; 助人为乐: 认出委托人名字后, 名字右边多少像素是"帮助"按钮(留 0 = 直接点名字本身)
; 如果选人点不中, 把这个值调大/调小试试
helper_button_dx = 0
; 助人为乐「扶危救困」遇到乞丐要钱时的答法:
;   fight = 「你是来敲诈的吧，看我收拾你」 不花钱, 打一场  (默认)
;   pay   = 「这么可怜，就给他吧」         掏 105,300 文
;   skip  = 「先看看再说」                 走开, 但任务不会推进
helper_beggar = fight
; 除暴等任务点"捉拿XXX"时会弹「是否前往组队？」, 怎么答:
;   cancel  = 取消, 做单人任务  (默认, 用户指定)
;   confirm = 确认, 去找队伍组队
team_prompt = cancel
; 文字识别引擎:
;   local = 本机 rapidocr 离线识别, 免费、不限量、不需要密钥(每次约 3 秒)
;   baidu = 调百度云接口, 需要下面的 [baiduAip] 密钥, 有每月免费额度
ocr_backend = baidu

[coords]
; ---------- 坐标修正表(关键) ----------
; task/ 里的脚本是 2018 年前后按夜神模拟器 + 1280x720 的游戏界面录的,
; 现在的《问道》界面布局已经改了, 所以那些写死的点位会点空。
; 在这里按 "老坐标 = 新坐标" 登记后, 运行时会自动替换, task/ 下的脚本不用改。
;
; 怎么量新坐标:
;   1) python start_asktao.py --coords          看有哪些点位要量
;   2) 在游戏里手动点开对应界面
;   3) python start_asktao.py --dump 界面名     导出这一屏所有文字的原坐标
;   4) 从导出结果里找到按钮文字, 把它的"原坐标"填到下面
;   也可以随时用 python start_asktao.py --find 活动  单独查某个词
;
; 已确认:
;   771,203 = 651,241    ; 活动按钮(原脚本第一步就点它, 老位置在新版里落到状态栏上了)
; 示例(取消注释并填上实测值即可生效):
;   1009,295 = ?,?       ; 活动面板里"师门任务"的"前往"

[baiduAip]
; 助人为乐 / 刷道 两个任务需要文字识别, 去 https://console.bce.baidu.com/ai/ 申请“文字识别”,
; 填好下面三项即可; 不填则这两个任务无法运行(其余任务不受影响)。
; 装依赖:  ..\\App\\Python\\python.exe -m pip install baidu-aip
;
; 用哪个接口(百度控制台里这几种是分开开通、分开算免费额度的, 没开通的调用会报 error_code 6):
;   general_basic  = 通用文字识别(标准版),       不带坐标 —— 原脚本用的就是这个
;   accurate_basic = 通用文字识别(高精度版),     不带坐标, 小字/花字识别更准
;   general        = 通用文字识别(含位置版),     带 location 坐标
;   accurate       = 通用文字识别(高精度含位置版), 带 location 坐标
ocr_api = general_basic
APPP_ID =
API_KEY =
SECRET_KEY =
"""


# ---------------------------------------------------------------- 异常与日志

class AutomationAborted(Exception):
    """用户按 ESC / Ctrl+C 主动中断。"""


class OcrUnavailable(Exception):
    """该任务需要文字识别, 但百度 OCR 没配好。"""


class Log(object):
    def __init__(self, path=None, verbose=True):
        self.verbose = verbose
        self.file = open(path, 'a', encoding='utf-8') if path else None

    def _write(self, tag, msg):
        line = u'[%s][%s] %s' % (datetime.now().strftime('%H:%M:%S'), tag, msg)
        try:
            print(line)
        except UnicodeEncodeError:
            # 控制台编码不是 UTF-8 时退化成可显示字符
            enc = sys.stdout.encoding or 'gbk'
            print(line.encode(enc, 'replace').decode(enc, 'replace'))
        if self.file:
            self.file.write(line + '\n')
            self.file.flush()

    def info(self, msg):
        self._write('信息', msg)

    def step(self, msg):
        self._write('步骤', msg)

    def warn(self, msg):
        self._write('警告', msg)

    def error(self, msg):
        self._write('错误', msg)

    def debug(self, msg):
        if self.verbose:
            self._write('调试', msg)

    def close(self):
        if self.file:
            self.file.close()
            self.file = None



def set_instance(serial):
    """多开时把临时截图/运行截图分到各自的子目录。

    两个实例同时跑时, 临时截图原本用固定文件名(init.png / tracker.png …),
    会互相覆盖 —— 结果 A 实例 OCR 读到的是 B 实例的画面, 点错地方。
    所以按设备号分子目录。返回用于文件名的短标签。
    """
    global TMP_DIR, CAP_DIR, INSTANCE_TAG
    INSTANCE_TAG = re.sub(u'[^0-9A-Za-z_.-]', u'_', serial or u'default')
    TMP_DIR = os.path.join(LOG_DIR, 'tmp', INSTANCE_TAG)
    CAP_DIR = os.path.join(LOG_DIR, 'capture', INSTANCE_TAG)
    for d in (TMP_DIR, CAP_DIR):
        os.makedirs(d, exist_ok=True)
    return INSTANCE_TAG


# ---------------------------------------------------------------- 配置

def load_config(log=None):
    """读取 config.ini, 不存在则用默认内容生成一份。"""
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            f.write(DEFAULT_CONFIG)
        if log:
            log.info(u'已生成默认配置: %s' % CONFIG_PATH)

    cfg = configparser.ConfigParser(inline_comment_prefixes=(';',))
    # 显式用 UTF-8 读, 避免中文 Windows 默认 GBK 读不了配置
    cfg.read(CONFIG_PATH, encoding='utf-8')
    for section in ('emulator', 'run', 'baiduAip'):
        if not cfg.has_section(section):
            cfg.add_section(section)
    return cfg


def cfg_get(cfg, section, option, fallback=''):
    try:
        return cfg.get(section, option, fallback=fallback)
    except (configparser.Error, AttributeError):
        return fallback


def load_coord_map(cfg, log=None):
    """读 config.ini 的 [coords]: 老坐标 = 新坐标。

    老脚本里的点位是按 2018 年的游戏界面录的, 现在的界面已经变了。
    在这里登记映射后, 运行时会把老坐标替换成实测的新坐标, task/ 下的脚本不用改。
    """
    def pair(text):
        x, y = [int(v) for v in text.replace(u'，', ',').split(',')]
        return (x, y)

    remap = {}
    if not cfg.has_section('coords'):
        return remap
    for key, value in cfg.items('coords'):
        try:
            old = pair(key)
        except ValueError:
            if log:
                log.warn(u'[coords] 键看不懂, 已忽略: %s' % key)
            continue
        # 值可以是一串坐标, 用 ; 分隔 —— 新版界面比老脚本多点一次时就这么写
        points = []
        try:
            for part in value.split(';'):
                part = part.strip()
                if part:
                    points.append(pair(part))
        except ValueError:
            if log:
                log.warn(u'[coords] 值看不懂, 已忽略: %s = %s' % (key, value))
            continue
        if points:
            remap[old] = tuple(points)
    return remap


# ---------------------------------------------------------------- 坐标换算

class Mapper(object):
    """把老脚本的屏幕绝对坐标换算成模拟器内像素坐标。"""

    def __init__(self, ref_x, ref_y, ref_w, ref_h, scr_w, scr_h, off_x=0.0, off_y=0.0):
        self.ref_x, self.ref_y = float(ref_x), float(ref_y)
        self.ref_w, self.ref_h = float(ref_w), float(ref_h)
        self.scr_w, self.scr_h = int(scr_w), int(scr_h)
        self.off_x, self.off_y = float(off_x), float(off_y)
        self.kx = self.scr_w / self.ref_w if self.ref_w else 1.0
        self.ky = self.scr_h / self.ref_h if self.ref_h else 1.0

    def point(self, x, y):
        """返回模拟器内坐标(已夹到屏幕范围内)。"""
        ex = (float(x) - self.ref_x) * self.kx + self.off_x
        ey = (float(y) - self.ref_y) * self.ky + self.off_y
        ex = max(0, min(self.scr_w - 1, int(round(ex))))
        ey = max(0, min(self.scr_h - 1, int(round(ey))))
        return ex, ey

    def rect(self, x1, y1, x2, y2):
        """把老脚本的截图区域换算成模拟器内的裁剪框。"""
        ex1, ey1 = self.point(x1, y1)
        ex2, ey2 = self.point(x2, y2)
        ex1, ex2 = sorted((ex1, ex2))
        ey1, ey2 = sorted((ey1, ey2))
        return ex1, ey1, max(ex1 + 1, ex2), max(ey1 + 1, ey2)

    def describe(self):
        return (u'参考画面 %.0fx%.0f @(%.0f,%.0f) -> 模拟器 %dx%d  '
                u'(缩放 x%.3f y%.3f, 偏移 %+.0f/%+.0f)'
                % (self.ref_w, self.ref_h, self.ref_x, self.ref_y,
                   self.scr_w, self.scr_h, self.kx, self.ky, self.off_x, self.off_y))


# ---------------------------------------------------------------- 模拟器

class Emulator(object):
    def __init__(self, cfg, log):
        self.cfg = cfg
        self.log = log
        self.adb = (cfg_get(cfg, 'emulator', 'adb_path') or '').strip() or self._find_adb()
        self.serial = (cfg_get(cfg, 'emulator', 'serial') or '').strip()
        self.package = (cfg_get(cfg, 'emulator', 'package') or 'com.gbits.atm.leiting').strip()
        self.activity = (cfg_get(cfg, 'emulator', 'activity') or '').strip()
        self.input_mode = (cfg_get(cfg, 'emulator', 'input_mode', 'adb') or 'adb').strip().lower()
        self.scr_w = self.scr_h = 0
        self.tap_interval = float(cfg_get(cfg, 'run', 'tap_interval', '0.15') or 0.15)
        self.hwnd = None

    # ---- adb 基础 ----

    def _find_adb(self):
        for p in ADB_CANDIDATES:
            if os.path.exists(p):
                return p
        # 雷电装在别处时, 扫一下几个盘的 leidian 目录
        for root in ('D:\\', 'C:\\', 'E:\\'):
            base = os.path.join(root, 'leidian')
            if not os.path.isdir(base):
                continue
            for name in sorted(os.listdir(base)):
                p = os.path.join(base, name, 'adb.exe')
                if os.path.exists(p):
                    return p
        return 'adb'

    def _cmd(self, args, serial=True):
        cmd = [self.adb]
        if serial and self.serial:
            cmd += ['-s', self.serial]
        return cmd + list(args)

    def run(self, args, timeout=20, binary=False, serial=True, check=False):
        cmd = self._cmd(args, serial)
        self.log.debug(u'$ ' + ' '.join(cmd))
        try:
            proc = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=timeout, creationflags=CREATE_NO_WINDOW if IS_WIN else 0)
        except FileNotFoundError:
            raise RuntimeError(u'找不到 adb: %s  (请在 config.ini 里填 emulator.adb_path)' % self.adb)
        except subprocess.TimeoutExpired:
            raise RuntimeError(u'adb 执行超时: %s' % ' '.join(cmd))
        out = proc.stdout if binary else proc.stdout.decode('utf-8', 'replace')
        err = proc.stderr.decode('utf-8', 'replace').strip()
        if check and proc.returncode != 0:
            raise RuntimeError(u'adb 返回 %d: %s' % (proc.returncode, err or out))
        return proc.returncode, out, err

    # ---- 设备 ----

    def ensure_device(self, auto_connect=True):
        code, out, err = self.run(['devices'], serial=False, check=False)
        if code != 0:
            raise RuntimeError(u'adb 无法执行: %s (若有其它 adb 服务器占用 5037 端口, '
                               u'请先结束所有 adb.exe 再试)' % (err or out))

        def list_devices(text):
            found = []
            for line in text.splitlines()[1:]:
                line = line.strip()
                if not line or line.startswith('*'):
                    continue
                parts = line.split()
                if len(parts) >= 2 and parts[1] == 'device':
                    found.append(parts[0])
            return found

        devices = list_devices(out)
        if devices and self.serial and self.serial not in devices:
            self.log.warn(u'配置的设备 %s 不在线, 改用 %s' % (self.serial, devices[0]))
            self.serial = devices[0]
        if devices and not self.serial:
            self.serial = devices[0]

        if not self.serial and auto_connect:
            # 雷电多开端口: 5555 起, 老版本 62001 起
            for port in (5555, 5557, 5559, 62001, 62025):
                self.run(['connect', '127.0.0.1:%d' % port], serial=False)
            code, out, err = self.run(['devices'], serial=False)
            devices = list_devices(out)
            if devices:
                self.serial = devices[0]

        if not self.serial:
            raise RuntimeError(u'没有找到在线的模拟器设备。请先启动雷电模拟器, '
                               u'或在 config.ini 里设置 emulator.serial')
        self.log.info(u'已连接设备: %s' % self.serial)
        return self.serial

    def get_screen_size(self):
        code, out, err = self.run(['shell', 'wm size'], check=True)
        self.log.debug(u'wm size -> %s' % out.strip().replace('\n', ' | '))
        w = h = 0
        for line in out.splitlines():
            line = line.strip()
            if ':' not in line:
                continue
            value = line.split(':', 1)[1].strip()
            if 'x' not in value:
                continue
            try:
                ww, hh = (int(v) for v in value.lower().split('x')[:2])
            except ValueError:
                continue
            # Override size(玩家改过分辨率)优先
            if line.lower().startswith('override'):
                w, h = ww, hh
            elif not w:
                w, h = ww, hh
        if not w or not h:
            raise RuntimeError(u'无法解析模拟器分辨率, wm size 输出: %r' % out)
        self.scr_w, self.scr_h = w, h
        self.log.info(u'模拟器分辨率: %dx%d' % (w, h))
        return w, h

    # ---- 截图 ----

    def screencap(self):
        """返回模拟器当前画面的 PIL.Image(RGB)。"""
        if Image is None:
            raise RuntimeError(u'缺少 Pillow, 请执行: python -m pip install pillow')
        code, data, err = self.run(['exec-out', 'screencap', '-p'], timeout=40, binary=True)
        if code == 0 and data[:4] == b'\x89PNG':
            return Image.open(io.BytesIO(data)).convert('RGB')
        # 有些 adb 版本 exec-out 会做换行转换, 退回 "截到手机再 pull"
        self.log.debug(u'exec-out 截图失败, 回退到 shell screencap + pull')
        remote = '/sdcard/_asktao_cap.png'
        os.makedirs(TMP_DIR, exist_ok=True)
        local = os.path.join(TMP_DIR, '_cap.png')
        self.run(['shell', 'screencap -p %s' % remote], timeout=40, check=True)
        self.run(['pull', remote, local], timeout=60, check=True)
        return Image.open(local).convert('RGB')

    # ---- 点击 / 按键 ----

    def tap(self, x, y):
        x, y = int(x), int(y)
        if self.input_mode == 'mouse':
            self._mouse_tap(x, y)
        else:
            self.run(['shell', 'input tap %d %d' % (x, y)], timeout=15)
        _real_sleep(self.tap_interval)

    def swipe(self, x1, y1, x2, y2, ms=300):
        self.run(['shell', 'input swipe %d %d %d %d %d' % (x1, y1, x2, y2, ms)], timeout=15)

    def keyevent(self, code):
        self.run(['shell', 'input keyevent %s' % code], timeout=15)

    # ---- 鼠标模式(把 adb 坐标换算到模拟器窗口) ----

    def _find_window(self):
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        found = []

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def cb(hwnd, lparam):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, buf, 256)
            cls = buf.value
            user32.GetWindowTextW(hwnd, buf, 256)
            title = buf.value
            if 'LDPlayerMainFrame' in cls or u'雷电模拟器' in title or 'LDPlayer' in title:
                found.append((hwnd, cls, title))
            return True

        user32.EnumWindows(WNDENUMPROC(cb), 0)
        return found

    def focus_window(self):
        """把模拟器窗口还原并置顶, 鼠标模式必须。返回窗口客户区 (left, top, w, h)。"""
        wins = self._find_window()
        if not wins:
            raise RuntimeError(u'没找到雷电模拟器窗口, 请确认模拟器已启动(鼠标模式需要窗口可见)')
        hwnd = wins[0][0]
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        user32.ShowWindow(hwnd, 9)          # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        _real_sleep(0.3)

        from ctypes import wintypes
        rect = wintypes.RECT()
        pt = wintypes.POINT(0, 0)
        user32.GetClientRect(hwnd, ctypes.byref(rect))
        user32.ClientToScreen(hwnd, ctypes.byref(pt))
        self.hwnd = hwnd
        return pt.x, pt.y, rect.right - rect.left, rect.bottom - rect.top

    def _mouse_tap(self, x, y):
        from ctypes import wintypes
        left, top, cw, ch = self.focus_window()
        sx = left + int(round(x * cw / float(self.scr_w)))
        sy = top + int(round(y * ch / float(self.scr_h)))
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        user32.SetCursorPos(sx, sy)
        _real_sleep(0.05)
        user32.mouse_event(0x0002, 0, 0, 0, 0)      # LEFTDOWN
        _real_sleep(0.05)
        user32.mouse_event(0x0004, 0, 0, 0, 0)      # LEFTUP
        self.log.debug(u'鼠标点击 (%d,%d)' % (sx, sy))

    # ---- 游戏进程 ----

    def current_focus(self):
        try:
            code, out, err = self.run(['shell', 'dumpsys window'], timeout=25)
        except RuntimeError as e:
            self.log.debug(u'取前台应用失败: %s' % e)
            return ''
        for line in out.splitlines():
            if 'mCurrentFocus' in line or 'mFocusedApp' in line:
                return line.strip()
        return ''

    def game_in_front(self):
        return self.package in self.current_focus()

    def launch_game(self):
        if self.activity:
            self.run(['shell', 'am start -n %s/%s' % (self.package, self.activity)], timeout=25)
        else:
            self.run(['shell', 'monkey -p %s -c android.intent.category.LAUNCHER 1' % self.package],
                     timeout=25)
        _real_sleep(3)


# ---------------------------------------------------------------- 控制器

class Controller(object):
    """所有 shim 都通过它点击/截图/等待, 统一做中断检查与日志。"""

    def __init__(self, emu, mapper, log, dry_run=False, time_scale=1.0, ocr=None, remap=None):
        self.emu = emu
        self.mapper = mapper
        self.log = log
        self.dry_run = dry_run
        self.time_scale = max(0.01, float(time_scale))
        self.ocr = ocr
        self.remap = remap or {}
        self.stop = threading.Event()
        self.tap_count = 0
        self.last_point = None

    # ---- 中断 ----

    def check(self):
        if self.stop.is_set():
            raise AutomationAborted()

    def sleep(self, seconds):
        """可中断的等待, 并支持 time_scale 加速(调试用)。"""
        seconds = float(seconds) / self.time_scale
        end = time.monotonic() + seconds
        while True:
            self.check()
            remain = end - time.monotonic()
            if remain <= 0:
                return
            _real_sleep(min(0.2, remain))

    # ---- 点击 ----

    def tap(self, hx, hy):
        """点一下。若该坐标在 [coords] 修正表里, 按表里的新坐标点(可能是一串坐标)。"""
        self.check()
        key = (int(hx), int(hy))
        targets = self.remap.get(key)
        if targets:
            if len(targets) > 1:
                self.log.info(u'坐标修正: 老脚本(%d,%d) -> %d 步 %s'
                              % (key[0], key[1], len(targets),
                                 u' -> '.join(u'(%d,%d)' % p for p in targets)))
            elif targets[0] != key:
                self.log.info(u'坐标修正: 老脚本(%d,%d) -> 实测(%d,%d)' % (key + targets[0]))
            for i, (nx, ny) in enumerate(targets):
                if i:
                    self.sleep(1.2)          # 留给界面弹出的时间
                self._tap_one(nx, ny)
            return
        self._tap_one(hx, hy)               # 没登记修正的坐标按原样点

    def _tap_one(self, hx, hy):
        ex, ey = self.mapper.point(hx, hy)
        self.tap_count += 1
        if self.dry_run:
            self.log.info(u'[空跑] 点击 原坐标(%s,%s) -> 模拟器(%d,%d)' % (hx, hy, ex, ey))
        else:
            self.log.info(u'点击 原坐标(%s,%s) -> 模拟器(%d,%d)' % (hx, hy, ex, ey))
            self.emu.tap(ex, ey)
        self.last_point = (ex, ey)

    # ---- 截图(供 OCR 用) ----

    def screenshot(self, x1, y1, x2, y2, name):
        """截图是只读操作, 空跑时也照常执行, 方便先验证 OCR 区域对不对。"""
        self.check()
        rect = self.mapper.rect(x1, y1, x2, y2)
        os.makedirs(CAP_DIR, exist_ok=True)
        out = os.path.join(CAP_DIR, '%s.png' % name)
        img = self.emu.screencap()
        img = img.crop(rect)
        if self.ocr and self.ocr.upscale != 1.0 and img.width < 800:
            img = img.resize((int(img.width * self.ocr.upscale), int(img.height * self.ocr.upscale)),
                             Image.LANCZOS)
        img.save(out)
        self.log.info(u'截图 %s 裁剪 %s 保存 %s (%dx%d)' % (x1, y1, out, img.width, img.height))
        return out


# ---------------------------------------------------------------- 百度 OCR

# 百度 OCR 可选接口: 前两个不带坐标, 后两个带 location
OCR_APIS = {
    'general_basic': ('basicGeneral', u'通用文字识别(标准版, 无坐标)'),
    'accurate_basic': ('basicAccurate', u'通用文字识别(高精度版, 无坐标)'),
    'general': ('general', u'通用文字识别(含位置版)'),
    'accurate': ('accurate', u'通用文字识别(高精度含位置版)'),
}


class LocalOcr(object):
    """本机离线 OCR(rapidocr-onnxruntime): 不联网、不要密钥、不限量, 还能给出文字坐标。"""

    def __init__(self, log):
        self.log = log
        self.engine = None
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError:
            return
        try:
            self.engine = RapidOCR()
        except Exception as e:                       # 模型缺失/依赖不全
            log.warn(u'本地 OCR 初始化失败: %s' % e)
            self.engine = None
            return
        log.info(u'本地 OCR(rapidocr) 已就绪: 离线、不限量、免密钥')

    def available(self):
        return self.engine is not None

    def boxes(self, path):
        """返回 [(文字, 置信度, 中心x, 中心y)], 坐标是图片像素。"""
        result, _ = self.engine(str(path))
        out = []
        for item in (result or []):
            box, text, score = item[0], item[1], float(item[2])
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            out.append((text, score, sum(xs) / 4.0, sum(ys) / 4.0))
        return out

    def recognize(self, path):
        return u''.join(b[0] for b in self.boxes(path))


class OcrEngine(object):
    def __init__(self, cfg, log):
        self.log = log
        self.client = None
        self.local = None
        self.backend = (cfg_get(cfg, 'run', 'ocr_backend', 'baidu') or 'baidu').strip().lower()
        if self.backend not in ('baidu', 'local'):
            log.warn(u'未知的 ocr_backend=%s, 回退到 baidu' % self.backend)
            self.backend = 'baidu'
        self.api = (cfg_get(cfg, 'baiduAip', 'ocr_api', 'general_basic') or 'general_basic').strip()
        if self.api not in OCR_APIS:
            log.warn(u'未知的 ocr_api=%s, 回退到 general_basic' % self.api)
            self.api = 'general_basic'
        self.method = OCR_APIS[self.api][0]
        try:
            self.upscale = float(cfg_get(cfg, 'run', 'ocr_upscale', '1.5') or 1.5)
        except ValueError:
            self.upscale = 1.5

        if self.backend == 'local':
            self.local = LocalOcr(log)
            if not self.local.available():
                self.log.warn(u'本地 OCR 不可用, 请执行: '
                              u'"%s" -m pip install rapidocr-onnxruntime' % sys.executable)
            return

        app_id = (cfg_get(cfg, 'baiduAip', 'APPP_ID') or '').strip()
        api_key = (cfg_get(cfg, 'baiduAip', 'API_KEY') or '').strip()
        secret_key = (cfg_get(cfg, 'baiduAip', 'SECRET_KEY') or '').strip()
        if not (app_id and api_key and secret_key):
            self.log.warn(u'未配置百度 OCR 密钥(config.ini 的 [baiduAip]), '
                          u'需要识别的任务(3 助人为乐 / 4 刷道)将无法运行')
            return
        try:
            from aip import AipOcr
        except ImportError as e:
            # 常见: 只装了 baidu-aip 没装它连带需要的 chardet, 报错会指向 aip 内部
            self.log.warn(u'导入 baidu-aip 失败: %s\n'
                          u'  请执行: "%s" -m pip install baidu-aip chardet'
                          % (e, sys.executable))
            return
        self.client = AipOcr(app_id, api_key, secret_key)
        try:
            self.client.setConnectionTimeoutInMillis(8000)
        except Exception:
            pass
        self.log.info(u'百度 OCR 已就绪: %s —— %s' % (self.api, OCR_APIS[self.api][1]))

    def available(self):
        if self.backend == 'local':
            return self.local is not None and self.local.available()
        return self.client is not None

    def describe(self):
        if self.backend == 'local':
            return u'本地 rapidocr(离线, 免密钥, 带坐标)'
        return u'百度 %s —— %s' % (self.api, OCR_APIS[self.api][1])

    def install_hint(self):
        if self.backend == 'local':
            return u'  执行: "%s" -m pip install rapidocr-onnxruntime' % sys.executable
        return (u'  1) 执行: "%s" -m pip install baidu-aip chardet\n'
                u'  2) 在 config.ini 的 [baiduAip] 填写 APPP_ID / API_KEY / SECRET_KEY'
                % sys.executable)

    def recognize(self, path):
        if not self.available():
            raise OcrUnavailable(
                u'该任务需要文字识别, 但 OCR 未就绪(当前引擎: %s)。\n%s'
                % (self.backend, self.install_hint()))
        if self.backend == 'local':
            text = self.local.recognize(path)
            self.log.debug(u'本地 OCR: %s' % text)
            return text
        with open(path, 'rb') as f:
            img = f.read()
        message = getattr(self.client, self.method)(img)
        if message.get('error_code'):
            code = message.get('error_code')
            hint = u''
            if str(code) == '6':
                hint = (u'\n  提示: error_code 6 = 没有该接口权限。百度控制台里"标准版/含位置版/高精度版"'
                        u'是分开开通的, 请确认已开通 %s, 或把 config.ini 的 ocr_api 改成已开通的那个'
                        % self.api)
            elif str(code) == '14':
                hint = (u'\n  提示: error_code 14 = 这套 API Key/Secret Key 本身没通过认证, 与OCR权限无关。'
                        u'常见原因: 三个值不是来自同一个应用 / 复制时抄错或漏字符 / '
                        u'应用建在了别的平台(要在 ai.baidu.com 的"文字识别"控制台建应用) / '
                        u'应用刚建好还没生效(等几分钟)')
            elif str(code) == '17':
                hint = u'\n  提示: 今日调用量已用完(免费额度一般 500~1000 次/天)'
            raise RuntimeError(u'百度 OCR 报错 %s: %s%s'
                               % (code, message.get('error_msg'), hint))
        text = u''
        for item in message.get('words_result', []) or []:
            loc = item.get('location') or {}
            if loc:
                self.log.debug(u'OCR: %s @(%s,%s %sx%s)'
                               % (item.get('words'), loc.get('left'), loc.get('top'),
                                  loc.get('width'), loc.get('height')))
            else:
                self.log.debug(u'OCR: %s' % item.get('words'))
            text += item.get('words', '')
        return text


# ---------------------------------------------------------------- 老脚本兼容层

def _resolve_resource(file_path):
    """老脚本里写死了 E:\\python_project\\... 的路径, 这里按文件名回落。

    优先找本次运行刚截的图(logs/capture), 再找 resource/ 里的参考图。
    """
    name = os.path.basename(str(file_path).replace('\\', '/'))
    for candidate in (os.path.join(CAP_DIR, name), os.path.join(RES_DIR, name), str(file_path)):
        if os.path.exists(candidate):
            return candidate
    raise IOError(u'找不到图片: %s (既没在 %s 也没在 %s 里找到)' % (file_path, CAP_DIR, RES_DIR))


def install_shims(ctrl, ocr, log):
    """往 sys.modules 里塞老脚本需要的模块, 让它们不用改代码就能跑。

    覆盖: autopy / win32api / win32con / imageGrabUtil / baiduAipUtil
    """
    import types

    # ---- autopy: 老脚本用 autopy.mouse.smooth_move(x,y) + autopy.mouse.click() ----
    autopy = types.ModuleType('autopy')
    mouse = types.ModuleType('autopy.mouse')

    state = {'x': None, 'y': None}

    def smooth_move(x, y, *a, **k):
        state['x'], state['y'] = x, y

    def click(*a, **k):
        if state['x'] is None:
            log.warn(u'autopy.mouse.click() 前没有移动鼠标, 已忽略')
            return
        ctrl.tap(state['x'], state['y'])

    def move(x, y, *a, **k):
        smooth_move(x, y)

    mouse.smooth_move = smooth_move
    mouse.move = move
    mouse.click = click
    autopy.mouse = mouse
    autopy.screen = types.SimpleNamespace(get_size=lambda: (1920, 1080))

    key = types.ModuleType('autopy.key')

    def _unsupported(*a, **k):
        raise NotImplementedError(u'本启动器未实现 autopy.key, 如需按键请用 emu.keyevent()')

    key.tap = key.type_string = key.toggle = _unsupported
    autopy.key = key

    # ---- win32api / win32con: 原脚本开头那段 Alt+Tab+Enter 只用于切到游戏窗口 ----
    win32api = types.ModuleType('win32api')

    def keybd_event(vk, scan, flags, extra, *a, **k):
        log.debug(u'忽略按键事件 vk=%s flags=%s (启动器已在任务开始前把模拟器切到前台)' % (vk, flags))

    win32api.keybd_event = keybd_event
    win32api.SetCursorPos = lambda x, y: None
    win32api.mouse_event = lambda *a, **k: None

    win32con = types.ModuleType('win32con')
    win32con.KEYEVENTF_KEYUP = 0x0002
    win32con.KEYEVENTF_EXTENDEDKEY = 0x0001
    win32con.VK_MENU = 18
    win32con.VK_TAB = 9
    win32con.VK_RETURN = 13
    win32con.MOUSEEVENTF_LEFTDOWN = 0x0002
    win32con.MOUSEEVENTF_LEFTUP = 0x0004

    # ---- imageGrabUtil: 截图改成从模拟器截 ----
    image_grab = types.ModuleType('imageGrabUtil')
    image_grab.screenshot = lambda x1, y1, x2, y2, filename: ctrl.screenshot(x1, y1, x2, y2, filename)

    def region_grab(x1, y1, x2, y2, *a, **k):
        rect = ctrl.mapper.rect(x1, y1, x2, y2)
        return ctrl.emu.screencap().crop(rect)

    image_grab.grab = region_grab

    # ---- baiduAipUtil: 文字识别 ----
    baidu = types.ModuleType('baiduAipUtil')

    def characterRecognition(file_path):
        path = _resolve_resource(file_path)
        ctrl.check()
        text = ocr.recognize(path)
        log.info(u'识别结果: %s' % text)
        return text

    baidu.characterRecognition = characterRecognition
    baidu.client = ocr.client

    for mod in (autopy, mouse, key, win32api, win32con, image_grab, baidu):
        sys.modules[mod.__name__] = mod

    log.debug(u'兼容层已注入: autopy / win32api / win32con / imageGrabUtil / baiduAipUtil')


def install_stoppable_sleep(ctrl):
    """把 time.sleep 换成可中断版本, 让按 ESC 后最多 ~0.2s 就能停下来。"""
    def patched(seconds):
        ctrl.sleep(seconds)

    patched.__wrapped__ = _real_sleep
    time.sleep = patched


# ---------------------------------------------------------------- 新版任务驱动

# 任务栏里表示"下一步动作"的行首关键词
# 内置驱动的任务种类: 面板上的卡片名 + 对话框里的选项关键词
QUEST_KINDS = {
    'shimen': {'title': u'师门', 'card': u'师门任务', 'word': u'师门'},
    'chubao': {'title': u'除暴', 'card': u'除暴任务', 'word': u'除暴'},
    'zhuren': {'title': u'助人为乐', 'card': u'助人为乐', 'word': u'助人'},
    # 面板上那张卡叫「【修炼】」, 接完任务后对话框里是「【修行】我这就去」, 跟踪栏也是"修行"
    'xiulian': {'title': u'修炼·修行', 'card': u'修炼', 'word': u'修行'},
    'arena': {'title': u'竞技场', 'card': u'竞技场', 'word': u'竞技场'},
}

# 队伍面板实测坐标(2026-09, 1600x900)
TEAM_PANEL_CLOSE = (1375, 278)      # 右上角关闭
TEAM_TAB_XIULIAN = (714, 560)       # 左列「修行」分类
TEAM_AUTO_MATCH = (1265, 744)       # 底部「自动匹配」

ACTION_WORDS = (u'前往', u'拜访', u'收集', u'回复', u'找到', u'询问', u'寻找',
                u'教训', u'对话', u'查看', u'交给', u'护送', u'击杀', u'挑战',
                u'捉拿', u'送回', u'求见', u'请教',
                # 助人为乐: 任务栏那行是"帮助杨镖头"
                u'帮助', u'协助', u'帮忙', u'领取', u'送达', u'上交', u'复命',
                u'传话', u'请月老', u'交给')
PROGRESS_RE = re.compile(u'(\\d+)\\s*/\\s*(\\d+)')
# 这些是别的任务线, 不能当成本任务 —— 踩过的坑: 关键词"师门"匹配到了
# 主线标题「主线—拜入师门」, 结果驱动去点了主线任务的"努力修炼至20级"。
NOT_OUR_QUEST = (u'主线', u'支线', u'指引', u'成就', u'奇遇', u'奇缘')


def screen_texts(emu, mapper, local, tag='screen'):
    """OCR 当前画面, 返回 [(原坐标x, 原坐标y, 文字)], 按 y 排序。"""
    src = os.path.join(TMP_DIR, '%s.png' % tag)
    emu.screencap().save(src)
    out = []
    for text, score, cx, cy in local.boxes(src):
        ox, oy = _to_original(mapper, cx, cy)
        out.append((int(round(ox)), int(round(oy)), text))
    out.sort(key=lambda r: r[1])
    return out



def looks_like_quest_title(text):
    """判断一行是不是"任务标题"(而不是"下一步"动作)。

    标题的典型样子: 带 (N/M) 进度, 或以【XX】开头(如【交友】并肩同行、【指引】为民除暴)。
    """
    if PROGRESS_RE.search(text):
        return True
    if text.startswith(u'【') and u'】' in text:
        return True
    return False


def looks_like_action(text):
    """这行是不是"下一步动作"(而不是任务标题)。动作行都以动词开头。"""
    return any(text.startswith(w) for w in ACTION_WORDS)


def has_our_quest(texts, word):
    """任务栏里是不是**真的**有这个日常任务。

    要排除两类假阳性:
      * 别的任务线: `【指引】为民除暴`、`【指引】修行`、`主线—拜入师门`
        —— 它们含"除暴/修行/师门", 会被关键词匹配到
      * 动作行: `领取修行任务`(含"修行"但是下一步动作, 不是任务标题)

    踩过的坑: 启动时只用 `word in t[2]` 判断, 结果把 `【指引】为民除暴`
    当成了除暴日常 → 不去活动面板接任务、直接进驱动 → 然后报"任务不在任务栏里"。
    """
    for t in tracker_rows(texts):
        if word not in t[2]:
            continue
        if any(n in t[2] for n in NOT_OUR_QUEST):
            continue
        if looks_like_action(t[2]):
            continue
        return True
    return False


def tracker_rows(texts):
    """从全屏文字里挑出右侧任务引导窗口那几行。

    这个窗口**可以上下滚动**, 行位置不固定, 所以纵向范围放宽到 200~780,
    靠 ACTION_WORDS 关键词来认哪一行是"下一步", 而不是靠固定位置。
    """
    rows = [t for t in texts if t[0] > 1340 and 200 < t[1] < 780]
    rows.sort(key=lambda r: r[1])
    return rows


def has_text(texts, *words):
    return any(any(w in t[2] for w in words) for t in texts)


def _dismiss_popup(ctrl, emu, mapper, log, local, texts):
    """关掉会挡住画面、导致读不到任务栏的自动弹窗。返回 True 表示处理过了。

    目前已知一种: **升级后自动弹出的「属性加点」面板**。
    实测角色做日常升到下一级时, 游戏会自己弹出来, 它的「属性/加点/技能」标签
    正好落在任务栏那个坐标范围里, 于是驱动读到的是"加点", 找不到任务标题,
    连续几次之后就误判成"任务不在任务栏里了"然后退出 —— 看起来就是"卡住"。

    这里只点「确认」把它关掉, **不点「自动加点」** —— 怎么加点是玩家自己的事,
    自动化不该替玩家决定。
    """
    if has_text(texts, u'属性加点', u'自动加点', u'属性点'):
        ok = next((t for t in texts if t[2].strip() == u'确认'), None)
        if ok is not None:
            log.warn(u'检测到「属性加点」面板(升级自动弹出), 点「确认」关掉 @原(%d,%d)'
                     % (ok[0], ok[1]))
            ctrl.tap(ok[0], ok[1])
            ctrl.sleep(2)
            return True
    return False


# 对话框里"安全"的选项 —— 不花钱、不打斗、不替玩家做决定的那一类。
# 实测场景: 助人为乐「扶危救困」走到衙门口会遇到乞丐乞讨,
#   这么可怜，就给他吧        ← 要掏 105,300 文, 不能自动点
#   你是来敲诈的吧，看我收拾你  ← 要打起来, 也不能替玩家决定
#   先看看再说               ← 安全的, 点了只是结束对话
# 所以只认这一类"再看看/算了/离开"的选项。
# 领奖后续对话: 选完经验奖励后会弹选择题, 按用户要求选「领取原有奖励」
REWARD_CLAIM_WORDS = (u'领取原有奖励', u'领取奖励', u'确定领取', u'我要奖励', u'原有的奖励')

SAFE_DIALOG_WORDS = (u'先看看再说', u'再看看', u'稍后再说', u'以后再说', u'下次再说',
                     u'再想想', u'想想再说', u'算了', u'不要了', u'不给他', u'不给',
                     u'拒绝', u'取消', u'离开', u'关闭', u'返回', u'退出',
                     u'我知道了', u'知道了', u'明白了', u'好的', u'打扰了')


def _dialog_choices(texts, x_min=1340):
    """挑出对话框里的"选项"行。

    **按列位置区分, 不能按标点区分** —— 踩过的坑: 一开始以为"选项不带标点、台词带标点",
    结果把真正的选项「这么可怜，就给他吧」「你是来敲诈的吧，看我收拾你」
    (它们本身就带逗号)全过滤掉了, 只剩下「先看看再说」, 于是任务永远推不动。

    实测同一屏的横向位置:
        台词: "好心人呐，能不能打发我105,300文" x=1342
              "钱？我已经三天没吃饭了。"        x=1302
        选项: "这么可怜，就给他吧"              x=1363
              "你是来敲诈的吧，看我收拾你"      x=1364
              "先看看再说"                     x=1364
    选项和右侧任务栏在同一列(x≈1364), 台词更靠左。所以按 x 切就行,
    长度 >16 的当台词排掉(实测这句台词 17 字)。
    """
    out = []
    for t in texts:
        if t[0] < x_min:
            continue
        if len(t[2]) > 16:
            continue
        out.append(t)
    out.sort(key=lambda t: t[1])
    return out


def pick_safe_choice(texts):
    """在没有【】选项的对话框里挑一个安全选项, 挑不到返回 None。"""
    choices = _dialog_choices(texts)
    for t in choices:
        if t[2].strip() in SAFE_DIALOG_WORDS:
            return t
    for t in choices:
        if any(w in t[2] for w in SAFE_DIALOG_WORDS):
            return t
    return None


# 助人为乐「扶危救困」到衙门口, 乞丐会要 105,300 文。选择题怎么答由 config.ini 决定
# (run.helper_beggar), 默认 fight = 打他, 不花钱。
BEGGAR_PREFER = {
    'fight': (u'收拾你', u'敲诈'),      # 「你是来敲诈的吧，看我收拾你」不花钱, 打一场
    'pay':   (u'就给他吧', u'可怜'),    # 「这么可怜，就给他吧」掏 105,300 文
    'skip':  (u'先看看再说',),          # 「先看看再说」走开 —— 注意**任务不会推进**
}


def pick_choice(texts, prefer_words, claim=False):
    """选择题对话挑一个选项: 先按 prefer_words 的顺序找, 找不到再退回安全选项。
    如果 claim=True, 在安全选项之前优先匹配 REWARD_CLAIM_WORDS(领奖确认)。"""
    choices = _dialog_choices(texts)
    for kw in prefer_words:
        for t in choices:
            if kw in t[2]:
                return t
    if claim:
        for kw in REWARD_CLAIM_WORDS:
            for t in choices:
                if kw in t[2]:
                    return t
    return pick_safe_choice(texts)


def pick_reward_choice(texts):
    """领奖对话挑选项: 有多个含「经验」的选项时取**第二个**, 只有一个就取它。

    用户说明的逻辑: 白邦芒那边领奖时选**经验奖励**, 而且是**第二个** —— 第一个要收钱。

    扫描范围放宽到长度 20(x 仍在选项那一列), 因为奖励选项的文字比较长,
    比如「【助人】我要经验奖励（推荐）」有 14 个字。
    """
    exps = [t for t in texts if t[0] >= 1340 and u'经验' in t[2] and len(t[2]) <= 20]
    if not exps:
        return None, []
    exps.sort(key=lambda t: t[1])
    return (exps[1] if len(exps) >= 2 else exps[0]), exps


def _handle_dialog(word, mode, ctrl, log, texts, wait, team_mode='cancel'):
    """处理 NPC 对话框。返回 True 表示点了东西(调用方应该重新读屏)。

    两种情况:
      1. 对话里有带【】的任务选项(如「【随机师门】中等」「【助人】帮助乞丐」) → 点它
      2. 没有【】选项, 是普通选择题(如衙门口乞丐要钱) → 按 mode 选

    它必须在读任务栏**之前**调用 —— 对话框一开, 台词行就落进任务栏的坐标范围,
    会被当成任务栏内容读出来。
    """
    # 「是否前往组队？」确认框 —— **必须放在 dialog_open 判断之前**。
    # 它只有 7 个字("是否前往组队？"), 过不了 dialog_open 那道"台词要够长"的判断,
    # 所以之前完全没被识别到, 驱动就一直重复点任务栏的动作行, 卡死在这里。
    #
    # 用户设定: 做**单人任务, 不组队** → 选「取消」。
    # (实测: 取消之后弹窗消失, 再点一次任务栏那行就会单人开打)
    # 由 config.ini 的 run.team_prompt 决定(cancel=单人 / confirm=去找队伍)。
    if has_text(texts, u'是否前往组队', u'前往组队'):
        want = u'确认' if team_mode == 'confirm' else u'取消'
        btn = next((t for t in texts if t[2].strip() == want), None)
        if btn is not None:
            log.step(u'弹出「是否前往组队？」—— 按设定(%s)选「%s」@原(%d,%d)'
                     % (team_mode, btn[2], btn[0], btn[1]))
            ctrl.tap(btn[0], btn[1])
            ctrl.sleep(wait)
            return True
        log.warn(u'弹出「是否前往组队？」但没找到「%s」按钮, 不点任何东西' % want)
        return False

    if not dialog_open(texts):
        return False

    # 领奖优先 —— 这一条**必须放在【】匹配之前**。
    # 实测领奖对话里那个选项是「【助人】我要经验奖励（推荐）」, 带【助人】前缀,
    # 会被下面的通用【】匹配先抢走; 而按用户的逻辑要从多个"经验"选项里挑第二个。
    hit, exps = pick_reward_choice(texts)
    if hit is not None:
        log.step(u'领奖: 共 %d 个经验选项, 选第 %d 个「%s」@原(%d,%d)'
                 % (len(exps), 2 if len(exps) >= 2 else 1, hit[2], hit[0], hit[1]))
        for t in exps:
            log.info(u'    经验选项: (%d,%d)  %s' % t)
        ctrl.tap(hit[0], hit[1])
        ctrl.sleep(wait)
        return True

    opt = next((t for t in texts
                if (u'【' in t[2] or u'[' in t[2]) and word in t[2]
                and not any(n in t[2] for n in NOT_OUR_QUEST)), None)
    if opt is not None:
        log.step(u'对话框里点「%s」@原(%d,%d)' % (opt[2], opt[0], opt[1]))
    else:
        # 选择题: 「这么可怜，就给他吧」要花钱 / 「你是来敲诈的吧，看我收拾你」要打
        # / 「先看看再说」走开但任务不推进。怎么答由 config.ini 的 run.helper_beggar 定。
        prefer = BEGGAR_PREFER.get(mode, BEGGAR_PREFER['fight'])
        opt = pick_choice(texts, prefer, claim=True)
        if opt is not None:
            log.step(u'选择题对话(模式 %s), 选「%s」@原(%d,%d)'
                     % (mode, opt[2], opt[0], opt[1]))
        else:
            log.warn(u'对话框里没找到「%s」选项, 也没识别出可选的答法, '
                     u'不点任何东西(免得替你做花钱/开打的决定)'
                     u' —— 对话里的选项: %s'
                     % (word, u' | '.join(t[2] for t in _dialog_choices(texts)[:5])))
            return False

    ctrl.tap(opt[0], opt[1])
    ctrl.sleep(wait)
    return True


def dialog_open(texts):
    """判断是不是开着 NPC 对话框。

    对话框会盖住任务栏, 它的台词和选项落在任务栏那个坐标范围里,
    会被当成任务栏内容读出来 —— 踩过的坑: 对话框里的「查询日常挑战次数」
    含"挑战"二字, 被动作关键词兜底匹配上, 驱动就去点它,
    结果【随机师门】那个真正的选项一直没被点到, **卡死在对话界面**。

    台词行的特征: 带**中文标点** —— 任务栏的行不会带标点
    (如"前往拜访茅山道士", "前往桃柳林教训作恶的强盗" 都不带)。
    注意顿号「、」也算 —— 实测茅山道士那句台词是
    "如果您要收怪、捉妖、勘风水....来找", 只认句号逗号会漏掉。
    """
    return any(t[0] > 1100 and len(t[2]) >= 8
               and any(c in t[2] for c in u'，。！？…；、：')
               for t in texts)


BATTLE_RE = re.compile(u'第\\s*\\d+\\s*回合')


def in_battle(texts):
    """判断是否在战斗中 —— 战斗界面会盖住任务栏, 这时候只能等, 点哪都没用。"""
    return (any(BATTLE_RE.search(t[2]) for t in texts)
            or has_text(texts, u'取消自动', u'战斗自动'))



def _wait_change(ctrl, emu, mapper, log, local, before, max_wait, interval=8):
    """等任务栏内容发生变化, 变了就立刻返回(最多等 max_wait 秒)。

    比"无条件 sleep 固定秒数"快得多 —— 游戏经常提前做完, 死等就是纯浪费。
    每 interval 秒看一次, 战斗中不算"变化"(那是在打怪, 还没推进)。
    """
    end = time.monotonic() + float(max_wait)
    while time.monotonic() < end:
        ctrl.check()
        ctrl.sleep(interval)
        texts = screen_texts(emu, mapper, local, 'wait')
        if in_battle(texts):
            continue                      # 还在打, 继续等
        rows = tracker_rows(texts)
        if not rows:
            continue
        cur = u'|'.join(r[2] for r in rows)
        if cur != before:
            log.debug(u'任务栏已变化, 提前继续')
            return True
    return False



def ensure_quest_tab(ctrl, emu, mapper, log, local, texts):
    """确保右侧引导窗口停在「任务」标签页。

    那个窗口有「任务 / 组队」等标签。停在「组队」页时只会显示"请点击创建 或加入队伍",
    读不到任何任务 —— 驱动会误判成"任务不在任务栏里了"。
    """
    rows = tracker_rows(texts)
    if any(u'请点击创建' in r[2] or u'或加入队伍' in r[2] for r in rows):
        # 标签页在窗口顶部那一行(原坐标 y≈296), 任务标题都在它下面。
        # 判断要**容错**: OCR 会把「任务」认成「通任务」「住务」之类,
        # 用 == '任务' 精确匹配就会漏(实测就是这么卡的)。
        tab = next((t for t in texts
                    if u'任务' in t[2] and len(t[2]) <= 4
                    and t[0] > 1340 and 270 < t[1] < 320), None)
        if tab is not None:
            log.info(u'任务栏停在「组队」页, 切回「任务」@原(%d,%d) (识别为「%s」)'
                     % (tab[0], tab[1], tab[2]))
            ctrl.tap(tab[0], tab[1])
            ctrl.sleep(2)
            return screen_texts(emu, mapper, local, 'tab_quest')
        log.warn(u'任务栏停在「组队」页, 但没找到「任务」标签, 找不到就算了')
    return texts


def _drive_quest(kind, ctrl, emu, mapper, log, local, rounds, wait):
    """反复点任务栏的“下一步”, 每点一次游戏自动跑图/打怪/推进, 直到跑满 rounds 轮。

    kind 见 QUEST_KINDS: 'shimen' 师门 / 'chubao' 除暴。两者流程一样, 只是
    任务栏标题和对话框里的选项关键词不同(【随机师门】 / 【除暴】)。
    """
    word = QUEST_KINDS[kind]['word']
    # 选择题对话怎么答 (config.ini 的 run.helper_beggar, 默认 fight = 打, 不花钱)
    mode = (cfg_get(emu.cfg, 'run', 'helper_beggar', 'fight') or 'fight').strip().lower()
    # 组队确认框的答法(cancel=单人做任务 / confirm=去找队伍)
    team_mode = (cfg_get(emu.cfg, 'run', 'team_prompt', 'cancel') or 'cancel').strip().lower()
    rows = []
    miss = 0
    stuck = 0
    last_mark = None
    for i in range(1, int(rounds) + 1):
        ctrl.check()
        texts = screen_texts(emu, mapper, local, 'tracker')

        # (领奖对话统一由 _handle_dialog 里的 pick_reward_choice 处理, 这里不再重复判断)

        # 战斗中: 游戏自带自动战斗, 等它打完就行, 不算失败
        if in_battle(texts):
            log.info(u'自动战斗中, 等待…')
            ctrl.sleep(12)
            miss = 0
            continue

        # 兜底0: 剧情过场对话 —— 点"跳过"(对应老脚本的 1515,258, 新版实测 1486,253)
        skip = next((t for t in texts if u'跳过' in t[2]), None)
        if skip is not None:
            log.info(u'过场对话, 点"跳过" @原(%d,%d)' % (skip[0], skip[1]))
            ctrl.tap(skip[0], skip[1])
            ctrl.sleep(2)
            continue

        # 兜底1: 误触到"是否要退出游戏?"时点"取消", 别把游戏退掉
        if has_text(texts, u'退出游戏'):
            log.warn(u'检测到"是否要退出游戏"弹窗, 点取消')
            ctrl.tap(892, 578)
            ctrl.sleep(2)
            continue

        # NPC 对话要**最先**处理。
        # 对话里的「【随机师门】中等」就是进入任务的入口, 必须先点它;
        # 而且对话一开, 台词行就混进任务栏范围了, 后面的关键词兜底会误点
        # ("查询日常挑战次数"含"挑战")。所以这一步放在读任务栏之前。
        if _handle_dialog(word, mode, ctrl, log, texts, wait, team_mode):
            miss = 0
            continue

        # 升级弹窗(属性加点)也会挡住任务栏, 先关掉
        if _dismiss_popup(ctrl, emu, mapper, log, local, texts):
            continue

        texts = ensure_quest_tab(ctrl, emu, mapper, log, local, texts)
        rows = tracker_rows(texts)
        if not rows:
            log.warn(u'任务栏读不到内容, 稍后重试')
            ctrl.sleep(15)
            continue

        # 任务引导窗口可以上下滚动, 所以**不能假定标题在第一行、动作在第二行**,
        # 一律按文字内容找, 点到 OCR 量出来的实际位置。
        title_row = next((r for r in rows
                          if word in r[2]
                          and not any(n in r[2] for n in NOT_OUR_QUEST)), None)
        title = title_row[2] if title_row else rows[0][2]
        m = PROGRESS_RE.search(title)
        progress = (u'%s/%s' % (m.group(1), m.group(2))) if m else u'?'

        # 找"下一步"。实测三次任务(师门/除暴/助人)都是同一个规律:
        #     **紧挨着标题下面那一行就是当前要做的事**
        #   (师门—入世(6/10) → 拜访无名剑客; 为民除暴(1/10) → 捉拿强盗黄扒皮;
        #    助人为乐—传递心意 → 请月老帮忙)
        # 所以优先按位置取, 关键词只作兜底 —— 否则会出现"请月老帮忙"这种
        # 既不以动作词开头、也不含常用动词的行认不出来。
        action = None
        if title_row is not None:
            below = [r for r in rows
                     if 5 < r[1] - title_row[1] < 50          # 就在标题下面一行
                     and not looks_like_quest_title(r[2])]
            if below:
                action = below[0]
        # 兜底: 标题找到了、但下面那行不像动作时, 才按关键词在本任务范围内找。
        # 注意: **标题没找到时绝不能按关键词乱点** —— 会点到别的任务的行。
        # (踩过的坑: 把主线的"拜访千面怪"当成助人为乐的下一步点了, 人物跑去做主线)
        if action is None and title_row is not None:
            for r in rows:
                if r is title_row:
                    continue
                if r[1] < title_row[1]:                        # 只看标题下面的行
                    continue
                if any(w in r[2] for w in ACTION_WORDS):
                    action = r
                    break

        if action is None:
            # 走到 NPC 面前会弹出对话, 对话里有带【】的师门选项 —— 这就是老脚本说的
            # "对话框【师门】按钮"(老坐标 1338,650, 新版实测在 1368,577 附近)
            # 注意排除任务栏标题行 —— 它也带【】 (比如「【修炼】修行(2/30)」),
            # 不排除的话会去点标题行, 而不是对话框里的选项
            opt = next((t for t in texts
                        if (u'【' in t[2] or u'[' in t[2]) and word in t[2]
                        and not (title_row is not None and t is title_row)), None)
            if opt is not None:
                log.info(u'对话框里点「%s」@原(%d,%d)' % (opt[2], opt[0], opt[1]))
                ctrl.tap(opt[0], opt[1])
                ctrl.sleep(wait)
                miss = 0
                continue
            miss += 1
            if title_row is None:
                log.warn(u'任务栏里没读到%s任务的标题(第 %d 次), 不点任何东西'
                         u' —— 免得点到别的任务上' % (word, miss))
            else:
                log.warn(u'没找到"下一步"那一行(第 %d 次), 任务栏首行: %s' % (miss, title))
            if miss >= 3:
                log.error(u'连续 3 次找不到“下一步”, 说明%s任务不在任务栏里了'
                          u'(可能已跑完/被别的任务顶掉/人物卡在别处), 停止。'
                          % QUEST_KINDS[kind]['title'])
                log.error(u'当前任务栏内容: %s' % u' | '.join(r[2] for r in rows[:5]))
                return 1
            ctrl.sleep(15)
            continue
        miss = 0

        # 卡循环检测: 如果连续多轮都是"同一个任务 + 同一个下一步", 说明这一步推不动
        # (比如选错了对话选项), 报错停下 —— 免得无限重复(花钱的选项还可能重复扣钱)
        mark = (title, action[2])
        if mark == last_mark:
            stuck += 1
            if stuck >= 6:
                log.error(u'连续 %d 轮都是「%s → %s」, 这一步卡住推不动了, 停止。'
                          u'可能是对话选错了选项, 或者任务需要手动处理。' % (stuck, title, action[2]))
                return 1
            log.warn(u'「%s → %s」已经重复 %d 次了' % (title, action[2], stuck))
        else:
            stuck = 0
        last_mark = mark

        # 点 OCR 报出来的实际位置(窗口能滚动, 位置不固定); x 只做范围保护, 别点到任务栏外面
        ax = max(1360, min(1520, action[0]))
        log.info(u'第 %d/%d 轮   任务:%s (%s)   下一步: %s @原(%d,%d)'
                 % (i, rounds, title, progress, action[2], ax, action[1]))
        snapshot = u'|'.join(r[2] for r in rows)
        ctrl.tap(ax, action[1])
        # 不是死等 wait 秒, 而是每 8 秒看一次, 任务栏一变就继续(最多还是等 wait 秒)
        _wait_change(ctrl, emu, mapper, log, local, snapshot, wait)

    log.step(u'%s驱动跑完 %d 轮' % (QUEST_KINDS[kind]['title'], rounds))
    return 0




# 全屏面板(个人主页/社区等)的关闭位置。实测: 点这里能关掉「个人主页」面板。
# 这类面板的关闭按钮是纯图标, OCR 读不到, 只能靠位置。
PANEL_CLOSE = (1500, 300)


def _close_covering_panel(ctrl, emu, mapper, log, local, texts=None):
    """画面被意外的全屏面板挡住时, 把它关掉, 好让顶部按钮能点到。

    踩过的坑: 多开时 2 号实例上开着「个人主页」面板, 点「活动」完全没反应
    (点击被面板吃掉了), 驱动只会反复重试然后报"打不开活动面板"。
    这里 BACK 不可靠 —— 实测 BACK 关掉面板的同时还会弹"是否要退出游戏",
    点取消之后面板又回来了。所以直接点它的关闭位置。
    """
    # 先找面板自己那个 X 按钮(实测能读到, 只是置信度低 0.5 左右),
    # 找不到再退回固定位置
    x_btn = next((t for t in texts if t[2].strip() in (u'X', u'×', u'✕', u'x')), None)         if texts else None
    if x_btn is not None:
        log.warn(u'画面被面板挡住了, 点它的关闭按钮 X @原(%d,%d)' % (x_btn[0], x_btn[1]))
        ctrl.tap(x_btn[0], x_btn[1])
    else:
        log.warn(u'画面可能被面板挡住了, 点 (%d,%d) 试着关掉' % PANEL_CLOSE)
        ctrl.tap(*PANEL_CLOSE)
    ctrl.sleep(2.5)


def find_activity_btn(texts):
    """找顶部那排按钮里的「活动」。

    不能写死坐标 —— 顶部按钮会随地图/任务变化(比如某些地图没有"巡逻"),
    "活动"的位置会跟着左右移。实测: 天墉城有"巡逻"时活动在 x=651,
    没有"巡逻"时(a)在 x=593。所以每次现找。
    注意排除活动面板打开后标题处那个"活动"(原坐标 y≈278)。
    """
    cands = [t for t in texts
             if t[2].strip() == u'活动' and t[1] < 265 and t[0] < 900]
    if not cands:
        return None
    cands.sort(key=lambda t: t[0])
    return cands[0]


# 游戏加载/过场时会出现这些字(健康游戏忠告是进入游戏时的提示)
LOADING_MARKS = (u'健康游戏忠告', u'抵制不良游戏', u'正在加载', u'资源加载')


def screen_is_loading(texts):
    """画面是不是还在加载 —— 加载时整屏几乎没有可读文字。

    踩过的坑: 游戏加载/切图时驱动读不到文字, 会误报"画面上找不到「活动」按钮"
    然后直接退出, 看起来像程序出错, 其实只是没加载完。
    """
    if has_text(texts, *LOADING_MARKS):
        return True
    return len(texts) < 5


def _settle_screen(ctrl, emu, mapper, log, local, tag='init'):
    """等自动战斗打完、把过场剧情跳过, 返回稳定之后的画面文字。

    战斗界面和过场剧情都会盖住任务栏, 这时候点哪都没用, 只能等/跳过。
    """
    texts = []
    for _ in range(90):
        texts = screen_texts(emu, mapper, local, tag)
        # 通用面板关闭: 画面被队伍面板等挡住时顶部按钮点不到。
        # 检测到没活动按钮时就尝试关掉遮挡面板。但别每轮都关 ——
        # _close_covering_panel 在没 X 时会点固定位置 (1500,300), 无面板时也点就有害了。
        if not find_activity_btn(texts):
            _close_covering_panel(ctrl, emu, mapper, log, local, texts)
        if screen_is_loading(texts):
            log.info(u'游戏正在加载, 等一会儿…')
            ctrl.sleep(6)
            continue
        if in_battle(texts):
            log.info(u'角色在自动战斗中, 等它打完…')
            ctrl.sleep(12)
            continue
        skip = next((t for t in texts if u'跳过' in t[2]), None)
        if skip is not None:
            log.info(u'正在过场对话, 点"跳过" @原(%d,%d)' % (skip[0], skip[1]))
            ctrl.tap(skip[0], skip[1])
            ctrl.sleep(2.5)
            continue
        break
    return texts


# 各日常的等级门槛(实测 + 用户确认)。等级不够时活动面板里那张卡
# **不会渲染"前往"按钮**, 看起来就像"没有这个活动" —— 有了这张表就能直接说清楚。
KNOWN_LEVELS = {
    u'师门任务': 15,
    u'除暴任务': 30,
    u'竞技场': 30,
    u'修炼': 30,
    u'刷道': 30,
    u'助人为乐': 35,
}


def read_level_requirement(texts):
    """从活动面板的悬浮框里读等级要求, 读不到返回 None。

    面板上写的是 `[等级限制]达到35级` 或 `开启条件：30级`。
    等级不够时那张卡**根本不渲染"前往"按钮**, 看起来像"找不到这个活动",
    实际是级别没到 —— 所以把要求读出来直接告诉用户, 别让人猜。

    已知的等级门槛(实测): 师门 15 / 除暴 30 / 竞技场 30 / 修行 30 / 刷道 30 / 助人为乐 35
    """
    joined = u' '.join(t[2] for t in texts)
    m = re.search(u'(?:等级限制|开启条件)[^0-9\\n]{0,6}(\\d+)\\s*级', joined)
    return int(m.group(1)) if m else None


def _accept_from_panel(kind, ctrl, emu, mapper, log, local):
    """去活动面板里找「卡片名」并点它的"前往", 把任务接下来。

    面板的卡片位置、展开状态、甚至同一分类里显示哪几张卡都会变, 所以全程 OCR 现找,
    没有任何写死坐标。返回 True 表示点到了"前往"。
    """
    card = QUEST_KINDS[kind]['card']
    title = QUEST_KINDS[kind]['title']
    log.step(u'任务栏里没有%s任务, 去活动面板接一个' % title)
    # 打开活动面板。
    # 「活动」按钮是开关: 面板开着时点它会关掉, 所以**先看再点**。
    opened = False
    clicks = 0                                     # 真正点了"活动"几次
    # 注意: **等待战斗/加载不能消耗点击预算** —— 原来用 for attempt in range(8)
    # 且等待时 continue, 结果等战斗就把 8 次全用光了, 还没轮到"关遮挡面板"就退出报错。
    for attempt in range(60):                      # 只作为防死循环的硬上限
        texts = screen_texts(emu, mapper, local, 'panel')
        if has_text(texts, u'奖励筛选', u'我的收藏', u'日常活动'):
            opened = True
            break
        if screen_is_loading(texts):
            # 加载/切图时读不到按钮, 别当成出错 —— 等一下重来
            log.info(u'画面还在加载, 等一下再找「活动」按钮…')
            ctrl.sleep(6)
            continue
        if in_battle(texts):
            # 半路打起来了(遇怪/被偷袭), 战斗界面盖住顶部按钮, 等它打完
            log.info(u'角色进战斗了, 等它打完再开面板…')
            ctrl.sleep(10)
            continue
        if clicks >= 2:
            # 点了两次都没开, 多半是有别的面板盖着 —— 先关掉它再试一次
            log.warn(u'活动面板没打开, 先关掉挡着的面板再试')
            _close_covering_panel(ctrl, emu, mapper, log, local, texts)
            texts = screen_texts(emu, mapper, local, 'panel_uncover')
            if has_text(texts, u'奖励筛选', u'我的收藏', u'日常活动'):
                opened = True
                break
            clicks = 0                             # 关掉遮挡后重新给两次机会
        btn = find_activity_btn(texts)
        if btn is None:
            log.error(u'画面上找不到「活动」按钮, 停在哪一屏了?'
                      u'(当前画面文字: %s)' % u' | '.join(t[2] for t in texts[:6]))
            return False
        log.info(u'点「活动」@原(%d,%d)' % (btn[0], btn[1]))
        ctrl.tap(btn[0], btn[1])
        clicks += 1
        ctrl.sleep(3.5)                            # 给面板渲染留够时间
    if not opened:
        log.error(u'打不开活动面板, 请手动确认游戏停在哪一屏')
        return False

    # 点「师门任务」那张卡的"前往" —— 必须每次现找, 因为面板里卡片的位置会变
    # (写死坐标曾经点到"除暴任务"上, 把人物带去李总兵那儿)
    # 找「师门任务」那张卡的"前往"。
    # 面板会记住上次看过的分类(比如停在"限时活动"就没有师门), 所以先看当前分类,
    # 没有就去点"日常活动", 再没有就点"我的收藏"。
    log.step(u'找「%s」的"前往"按钮' % card)
    label = target = None
    # 面板会把卡片标题和"前往"分开排, 而且两个日常活动经常并排出现,
    # 所以要认准"卡片名"(而不是任务栏里的【随机师门】/【除暴】), 再点同一行右边的"前往"
    for cat_name, cat_xy in ((u'当前分类', None), (u'日常活动', (679, 421)),
                             (u'我的收藏', (679, 372))):
        if cat_xy:
            log.info(u'当前分类里没有%s, 切到「%s」' % (card, cat_name))
            ctrl.tap(cat_xy[0], cat_xy[1])
            ctrl.sleep(2.5)
        texts = screen_texts(emu, mapper, local, 'panel2')
        # 限定在活动面板区域(x<1300), 排除右侧任务栏里的"【随机师门】"等
        label = next((t for t in texts
                      if card in t[2] and t[0] < 1300), None)
        if not label:
            continue
        # 同一行(上下 25 以内)、在它右边的"前往"
        cands = [t for t in texts
                 if u'前往' in t[2] and abs(t[1] - label[1]) < 25 and t[0] > label[0]]
        if cands:
            cands.sort(key=lambda t: t[0])
            target = cands[0]
            break

    if not target:
        texts = screen_texts(emu, mapper, local, 'panel3')
        need = KNOWN_LEVELS.get(card)
        panel_lv = read_level_requirement(texts)
        if need:
            # 注意别说死是等级问题 —— 卡片不在面板里还有别的原因:
            # 任务已在进行中(卡片会从"我的收藏"消失)、今日次数用完、分类没展开等。
            log.error(u'找不到「%s」的"前往"按钮。它需要 **%d 级**; '
                      u'如果你已经够级了, 那就是这几种情况之一: '
                      u'①任务已经接了(卡片会从"我的收藏"里消失) '
                      u'②今日次数已用完 ③面板分类没展开。' % (card, need))
        elif panel_lv:
            log.error(u'找不到「%s」的"前往"按钮。面板上显示的等级要求是 %d 级。'
                      % (card, panel_lv))
        else:
            log.error(u'三个分类里都没找到「%s」的"前往"按钮。'
                      u'可能是今日次数已用完(面板上会写"次数:x/10")或等级不够。' % card)
        log.error(u'当前面板文字: %s' % u' | '.join(t[2] for t in texts[:10]))
        return False
    log.info(u'「%s」在 (%d,%d), 它的"前往"在 (%d,%d)'
             % (card, label[0], label[1], target[0], target[1]))
    ctrl.tap(target[0], target[1])
    ctrl.sleep(20)                                 # 人物自动走过去
    return True


def run_quest(kind, ctrl, emu, mapper, log, ocr, rounds=12, wait=75):
    """内置任务驱动(师门 / 除暴)。

    2026-09 实测: 新版日常活动**没有**老脚本里那种"对话框按钮"式的固定流程, 全靠右侧
    任务栏的第二行(写着"前往XXX"/"拜访XXX"/"找到XXX"这类下一步动作):
        点一次 → 人物自动跑图 → 遇怪自动进入战斗(游戏自带自动战斗) → 任务计数 +1
    所以驱动方式是"反复点任务栏第二行", 而不是老脚本那种"固定 N 次点击 + 固定等待"。

    期间要处理四种状态: 战斗中(等) / 过场对话(点跳过) / NPC对话(点带【】的选项) /
    正常任务栏(点下一步)。详见 _drive_quest。

    kind 取值见 QUEST_KINDS: 'shimen'(师门) / 'chubao'(除暴)。
    """
    info = QUEST_KINDS[kind]
    title, card, word = info['title'], info['card'], info['word']
    local = _tool_ocr(log, ocr)

    # 先看任务栏: 如果师门任务已经挂在任务栏里(日常几乎总是这样), 就直接开始驱动,
    # 不用去活动面板绕一圈 —— 面板的卡片布局/悬浮框状态经常变, 能省则省。
    # 战斗中/过场剧情时画面会被盖住, 活动面板也点不动 —— 先等战斗结束、跳过剧情
    texts = _settle_screen(ctrl, emu, mapper, log, local)

    # 有时候一启动就开着对话框(比如上一次跑到衙门口乞丐那里被打断了),
    # 这时任务栏是被盖住的, 直接判断"任务在不在"会误判成不在。
    # 所以先把这个对话框处理掉, 再重新读屏。
    _beggar = (cfg_get(emu.cfg, 'run', 'helper_beggar', 'fight') or 'fight').strip().lower()
    _team = (cfg_get(emu.cfg, 'run', 'team_prompt', 'cancel') or 'cancel').strip().lower()
    for _ in range(4):
        if not _handle_dialog(word, _beggar, ctrl, log, texts, 3, _team):
            break
        texts = _settle_screen(ctrl, emu, mapper, log, local)

    already = has_our_quest(texts, word)
    if already:
        log.info(u'任务栏里已有%s任务, 跳过活动面板, 直接开始' % title)
        return _drive_quest(kind, ctrl, emu, mapper, log, local, rounds, wait)

    # 任务栏里没有 → 去活动面板接一个。
    # ⚠ 这一段之前被误删过(抽取 _accept_from_panel 时漏了调用),
    #   结果新角色永远接不到任务, 直接进驱动然后报"任务不在任务栏里"。
    if not _accept_from_panel(kind, ctrl, emu, mapper, log, local):
        return 1

    # 点完"前往"后人物自动走到 NPC 处, 但走路时间不固定(20~60秒都可能)。
    # 不能死等固定秒数 —— 等太久浪费时间, 等不够对话框没弹出来,
    # 驱动就开始读任务栏 → 读不到 → 报"任务不在任务栏里了"。
    # 改成每8秒看一次: 弹出对话框就处理, 任务进任务栏了就进驱动。
    _beggar = (cfg_get(emu.cfg, 'run', 'helper_beggar', 'fight') or 'fight').strip().lower()
    for _ in range(8):
        texts = screen_texts(emu, mapper, local, 'after_accept')
        if _handle_dialog(word, _beggar, ctrl, log, texts, 3):
            break
        if has_our_quest(texts, word):
            break
        ctrl.sleep(8)

    return _drive_quest(kind, ctrl, emu, mapper, log, local, rounds, wait)



def run_shimen(ctrl, emu, mapper, log, ocr, rounds=12, wait=75):
    """师门任务(日常活动)。"""
    return run_quest('shimen', ctrl, emu, mapper, log, ocr, rounds, wait)


def run_chubao(ctrl, emu, mapper, log, ocr, rounds=12, wait=75):
    """除暴任务(日常活动)。流程与师门一致, 只是卡片名/对话选项关键词不同。"""
    return run_quest('chubao', ctrl, emu, mapper, log, ocr, rounds, wait)


# 助人为乐的委托人(老脚本里出现过的两位)。认不出也没关系, 驱动任务栏一样能跑。
HELPER_NAMES = (u'冯喜来', u'杨镖头')


def run_zhuren(ctrl, emu, mapper, log, ocr, rounds=12, wait=75):
    """助人为乐(日常活动)。

    和师门/除暴的差别只在开头: 接完任务后要点「领赏」, 然后从委托人名单里挑一个。

    老脚本在这里是**截两条写死的区域**来判断委托人是冯喜来还是杨镖头
    (screenshot(1132,622,1536,676) 和 screenshot(1132,690,1536,741))。
    新版界面一变, 这两条区域就截到空白, OCR 返回空串, 四个 if/elif 一个都不成立,
    脚本**静默什么都不做就退出了** —— 比报错还难查。
    这里改成**全屏 OCR 找名字**, 不再依赖任何固定区域。
    """
    local = _tool_ocr(log, ocr)
    mode = (cfg_get(emu.cfg, 'run', 'helper_beggar', 'fight') or 'fight').strip().lower()
    team_mode = (cfg_get(emu.cfg, 'run', 'team_prompt', 'cancel') or 'cancel').strip().lower()
    texts = _settle_screen(ctrl, emu, mapper, log, local)

    # 一开始就开着对话框时(比如上次卡在衙门口乞丐那道选择题上), 先把它处理掉。
    # 不处理的话任务栏被盖住, 会误判成"没有助人为乐"然后跑去活动面板瞎找。
    for _ in range(4):
        if not _handle_dialog(u'助人', mode, ctrl, log, texts, 3, team_mode):
            break
        texts = _settle_screen(ctrl, emu, mapper, log, local)

    if has_our_quest(texts, u'助人'):
        log.info(u'任务栏里已有助人为乐, 跳过活动面板, 直接开始')
        return _drive_quest('zhuren', ctrl, emu, mapper, log, local, rounds, wait)

    if not _accept_from_panel('zhuren', ctrl, emu, mapper, log, local):
        return 1
    ctrl.sleep(20)                                 # 人物走到白帮忙那里

    # 点「领赏」
    for _ in range(8):
        texts = screen_texts(emu, mapper, local, 'ling')
        btn = next((t for t in texts if u'领赏' in t[2] or u'领取' in t[2]), None)
        if btn is not None:
            log.info(u'点「%s」@原(%d,%d)' % (btn[2], btn[0], btn[1]))
            ctrl.tap(btn[0], btn[1])
            ctrl.sleep(3)
            break
        ctrl.sleep(4)
    else:
        log.warn(u'没找到「领赏」按钮, 直接去驱动任务栏')

    # 委托人选择。
    # **助人为乐每次的委托人/剧情都不一样**, 实测见过的形式:
    #     「帮助杨镖头」「帮助李总兵」          (白邦芒的委托名单)
    #     「【助人】帮助乞丐」                 (带【助人】前缀)
    #     「这么可怜，就给他吧 / 收拾你 / 先看看再说」 (衙门口乞丐那道选择题)
    # 所以不能写死名字、也不能只认"帮助"开头的。
    # 这里统一用 _dialog_choices() 取"选项列"里的行(按 x 列位置识别, 不受标点影响),
    # 挑含「帮助」的那个; 找不到就交给 _handle_dialog 按通用规则处理。
    # 踩过的坑: 一开始在全屏文字里搜"冯喜来/杨镖头", 搜到了 NPC 台词里的"杨镖头",
    # 去点了台词那一行, 于是永远卡在选人这一步。
    try:
        dx = int(float(cfg_get(emu.cfg, 'run', 'helper_button_dx', '0') or 0))
    except (TypeError, ValueError):
        dx = 0

    picked = False
    for attempt in range(1, 7):
        texts = screen_texts(emu, mapper, local, 'helper')
        opts = [t for t in _dialog_choices(texts)
                if u'帮助' in t[2] and u'离开' not in t[2]]
        if not opts:
            # 也可能是别的形式的对话, 交给通用处理
            if _handle_dialog(u'助人', mode, ctrl, log, texts, 3, team_mode):
                picked = True
                break
            log.info(u'还没看到委托人选项(第 %d 次), 等一下' % attempt)
            ctrl.sleep(4)
            continue
        opts.sort(key=lambda t: t[1])          # 从上到下, 第一个就是优先
        hit = opts[0]
        log.step(u'选第一个委托人「%s」(%d 个可选) @原(%d,%d)'
                 % (hit[2], len(opts), hit[0] + dx, hit[1]))
        ctrl.tap(hit[0] + dx, hit[1])
        ctrl.sleep(4)
        picked = True
        break

    if not picked:
        log.warn(u'一直没等到委托人选项, 直接去驱动任务栏')

    return _drive_quest('zhuren', ctrl, emu, mapper, log, local, rounds, wait)

    # 点「领赏」
    for _ in range(8):
        texts = screen_texts(emu, mapper, local, 'ling')
        btn = next((t for t in texts if u'领赏' in t[2] or u'领取' in t[2]), None)
        if btn is not None:
            log.info(u'点「%s」@原(%d,%d)' % (btn[2], btn[0], btn[1]))
            ctrl.tap(btn[0], btn[1])
            ctrl.sleep(3)
            break
        ctrl.sleep(4)
    else:
        log.warn(u'没找到「领赏」按钮, 直接去驱动任务栏')

    # 委托人名单 —— 全屏 OCR 认名字, 替代老脚本那两条写死的截图区域
    try:
        dx = int(float(cfg_get(emu.cfg, 'run', 'helper_button_dx', '0') or 0))
    except (TypeError, ValueError):
        dx = 0
    for _ in range(4):
        texts = screen_texts(emu, mapper, local, 'helper')
        hit = None
        for name in HELPER_NAMES:
            hit = next((t for t in texts if name in t[2]), None)
            if hit is not None:
                log.info(u'认出委托人「%s」@原(%d,%d)' % (name, hit[0], hit[1]))
                break
        if hit is None:
            log.warn(u'没认出冯喜来/杨镖头(可能换人了), 跳过选人, 直接驱动任务栏')
            break
        log.info(u'点这一行的"帮助"按钮 @原(%d,%d)' % (hit[0] + dx, hit[1]))
        ctrl.tap(hit[0] + dx, hit[1])
        ctrl.sleep(3)
        break

    return _drive_quest('zhuren', ctrl, emu, mapper, log, local, rounds, wait)


def _auto_team(ctrl, emu, mapper, log, local, wait_match=60):
    """在 NPC 对话框里点「便捷组队」, 选「修行」分类, 开自动匹配, 等一会儿再关掉。

    修行(修炼)必须组队才能做, 所以这一步不能省。实测面板结构:
        对话框「便捷组队」→ 队伍面板 → 左列分类选「修行」→ 底部「自动匹配」
        → 等匹配(面板上会写"等待匹配：队长N人，队员M人") → 右上角关闭
    """
    texts = screen_texts(emu, mapper, local, 'team0')
    btn = next((t for t in texts if u'便捷组队' in t[2]), None)
    if btn is None:
        log.info(u'对话里没有「便捷组队」(可能已经在队伍里), 跳过自动组队')
        return True
    log.step(u'点「便捷组队」@原(%d,%d)' % (btn[0], btn[1]))
    ctrl.tap(btn[0], btn[1])
    ctrl.sleep(3)

    texts = screen_texts(emu, mapper, local, 'team1')
    if not has_text(texts, u'自动匹配', u'创建队伍'):
        log.warn(u'队伍面板没打开, 跳过自动组队')
        return False

    tab = next((t for t in texts if u'修行' in t[2] and t[0] < 800), None)
    if tab is not None:
        log.info(u'选「%s」分类 @原(%d,%d)' % (tab[2], tab[0], tab[1]))
        ctrl.tap(tab[0], tab[1])
        ctrl.sleep(2)

    match = next((t for t in texts if u'自动匹配' in t[2]), None)
    if match is None:
        texts = screen_texts(emu, mapper, local, 'team2')
        match = next((t for t in texts if u'自动匹配' in t[2]), None)
    if match is not None:
        log.step(u'点「自动匹配」@原(%d,%d)' % (match[0], match[1]))
        ctrl.tap(match[0], match[1])
    else:
        log.warn(u'没找到「自动匹配」按钮, 只选了分类')
    ctrl.sleep(3)

    # 等匹配 —— 轮询面板上的"等待匹配：队长N人，队员M人"
    for _ in range(max(1, int(wait_match // 6))):
        ctrl.check()
        texts = screen_texts(emu, mapper, local, 'team_wait')
        line = next((t for t in texts if u'等待匹配' in t[2]), None)
        if line is not None:
            log.info(u'队伍匹配状态: %s' % line[2])
            m = re.search(u'队长\s*(\d+)\s*人.*?队员\s*(\d+)\s*人', line[2])
            if m and int(m.group(1)) + int(m.group(2)) >= 3:
                log.info(u'已经凑够 3 人, 不等了')
                break
        ctrl.sleep(6)

    log.step(u'关掉队伍面板')
    ctrl.tap(*TEAM_PANEL_CLOSE)
    ctrl.sleep(2)
    return True


def run_xiulian(ctrl, emu, mapper, log, ocr, rounds=12, wait=75):
    """修炼·修行(日常活动)。修行必须组队, 所以带自动组队。

    实测流程:
        活动面板「【修炼】」前往 → 走到柳如尘 → 对话框「便捷组队」→ 自动匹配
        → 关面板 → 对话框「【修行】我这就去」→ 之后交给任务栏驱动
    """
    local = _tool_ocr(log, ocr)
    texts = _settle_screen(ctrl, emu, mapper, log, local)

    if has_our_quest(texts, u'修行'):
        log.info(u'任务栏里已有修行, 跳过活动面板, 直接守着')
        return _monitor_quest('xiulian', ctrl, emu, mapper, log, local, rounds, wait)

    if not _accept_from_panel('xiulian', ctrl, emu, mapper, log, local):
        return 1
    ctrl.sleep(20)                                 # 人物走到柳如尘那里

    # 先组队(修行必须组队), 再点「【修行】我这就去」
    _auto_team(ctrl, emu, mapper, log, local)

    for _ in range(6):
        texts = screen_texts(emu, mapper, local, 'xiu_go')
        go = next((t for t in texts
                   if (u'【' in t[2] or u'[' in t[2]) and u'修行' in t[2]), None)
        if go is not None:
            log.step(u'点「%s」@原(%d,%d)' % (go[2], go[0], go[1]))
            ctrl.tap(go[0], go[1])
            ctrl.sleep(5)
            break
        ctrl.sleep(5)
    else:
        log.warn(u'没找到「【修行】我这就去」, 直接去守着')

    # 修行是"接了就一直自动打"的类型, 任务栏没有"下一步"行, 所以只守着不点
    return _monitor_quest('xiulian', ctrl, emu, mapper, log, local, rounds, wait)


def _monitor_quest(kind, ctrl, emu, mapper, log, local, rounds, wait):
    """守着"接了就一直自动进行"的任务(如修行)。

    修行和师门/除暴不一样: 任务栏里**只有标题+进度**(如「【修炼】修行(4/30)」),
    没有"前往XXX"那种"下一步"行 —— 接了任务之后游戏自己一轮轮打, 计数自己涨。
    所以驱动不需要点击, 只要: 等战斗打完 / 跳过场 / 报进度 / 涨满就收工。
    万一中途真的冒出"下一步"行(需要点一下才继续), 也会自动点掉。
    """
    word = QUEST_KINDS[kind]['word']
    title = QUEST_KINDS[kind]['title']
    last = None
    for _ in range(max(1, int(rounds))):
        ctrl.check()
        texts = screen_texts(emu, mapper, local, 'monitor')

        if in_battle(texts):
            log.info(u'自动战斗中, 等待…')
            ctrl.sleep(12)
            continue

        skip = next((t for t in texts if u'跳过' in t[2]), None)
        if skip is not None:
            log.info(u'过场对话, 点"跳过" @原(%d,%d)' % (skip[0], skip[1]))
            ctrl.tap(skip[0], skip[1])
            ctrl.sleep(2.5)
            continue

        rows = tracker_rows(texts)
        row = next((t for t in rows
                    if word in t[2]
                    and not any(n in t[2] for n in NOT_OUR_QUEST)), None)
        if row is not None:
            m = PROGRESS_RE.search(row[2])
            if m:
                prog = u'%s/%s' % (m.group(1), m.group(2))
                if prog != last:
                    log.info(u'%s 进度: %s' % (title, prog))
                    last = prog
                if m.group(1) == m.group(2):
                    log.step(u'%s 已跑满 %s, 收工' % (title, prog))
                    return 0

        # 万一有"下一步"行, 点掉它
        action = next((r for r in rows if any(w in r[2] for w in ACTION_WORDS)), None)
        if action is not None and (row is None or action is not row):
            log.info(u'点「%s」@原(%d,%d)' % (action[2], action[0], action[1]))
            ctrl.tap(max(1360, min(1520, action[0])), action[1])

        # 同上: 等进度变化, 别死等
        _wait_change(ctrl, emu, mapper, log, local,
                     u'|'.join(r[2] for r in rows), wait)

    log.step(u'%s 守着跑完 %d 轮' % (title, rounds))
    return 0


def _arena_state(texts):
    """从画面里读出竞技场的状态: (剩余挑战次数, 挑战按钮列表)。

    实测「剩余挑战次数5」和每个对手下面一个「挑战」按钮。
    """
    joined = u' '.join(t[2] for t in texts)
    m = re.search(u'剩余挑战次数\s*(\d+)', joined)
    left = int(m.group(1)) if m else None
    btns = sorted([t for t in texts if t[2].strip() == u'挑战' and t[0] > 600],
                  key=lambda t: t[0])
    return left, btns


def run_arena(ctrl, emu, mapper, log, ocr, rounds=5, wait=75):
    """竞技场(日常活动)。

    实测流程(2026-09):
        活动面板「竞技场」前往 (991,465) → 走到竞技使者
        → 对话框点「竞技场」(1364,633)        [注意不是「指引」竞技场]
        → 竞技场界面点第一个「挑战」(711,678)  [4 个对手一排, 取最左那个]
        → 弹「是否确定发起挑战？」点「确认」(1060,578)
        → 进入战斗(游戏自带自动战斗)
    面板上写着「剩余挑战次数N」, 打完就收工。
    """
    local = _tool_ocr(log, ocr)
    texts = _settle_screen(ctrl, emu, mapper, log, local)

    left, btns = _arena_state(texts)
    if not btns:
        log.step(u'当前不在竞技场界面, 去活动面板接')
        if not _accept_from_panel('arena', ctrl, emu, mapper, log, local):
            return 1
        ctrl.sleep(20)                             # 走到竞技使者
        for _ in range(6):
            texts = screen_texts(emu, mapper, local, 'arena_dlg')
            # 对话里的选项就是"竞技场"三个字(「【指引】竞技场」是另一条, 要排除)
            opt = next((t for t in texts
                        if t[2].strip() == u'竞技场' and t[0] > 1100), None)
            if opt is not None:
                log.step(u'对话里点「竞技场」@原(%d,%d)' % (opt[0], opt[1]))
                ctrl.tap(opt[0], opt[1])
                ctrl.sleep(4)
                break
            ctrl.sleep(4)
        else:
            log.error(u'没等到竞技使者对话框里的「竞技场」选项')
            return 1

    done = 0
    guard = 0
    while done < int(rounds) and guard < 300:
        guard += 1
        ctrl.check()
        texts = screen_texts(emu, mapper, local, 'arena')

        if in_battle(texts):
            log.info(u'竞技战斗中, 等待…')
            ctrl.sleep(12)
            continue

        left, btns = _arena_state(texts)
        if left == 0:
            log.step(u'剩余挑战次数 0, 竞技场收工')
            break
        if not btns:
            log.info(u'没看到「挑战」按钮(可能刚打完在结算), 等一下')
            ctrl.sleep(10)
            continue

        btn = btns[0]                              # 优先第一个对手
        log.info(u'第 %d/%d 场   挑战第一个对手 @原(%d,%d)   剩余次数:%s'
                 % (done + 1, rounds, btn[0], btn[1],
                    left if left is not None else u'?'))
        ctrl.tap(btn[0], btn[1])
        ctrl.sleep(2)

        texts = screen_texts(emu, mapper, local, 'arena_confirm')
        conf = next((t for t in texts if t[2].strip() == u'确认'), None)
        if conf is not None:
            log.info(u'点「确认」@原(%d,%d)' % (conf[0], conf[1]))
            ctrl.tap(conf[0], conf[1])
        else:
            log.warn(u'没等到「是否确定发起挑战？」确认框')
        ctrl.sleep(6)
        done += 1

    log.step(u'竞技场完成 %d 场' % done)
    return 0


# native 名字 -> 驱动函数(新增内置任务时在这里登记)
NATIVE_TASKS = {
    'shimen': run_shimen,
    'chubao': run_chubao,
    'zhuren': run_zhuren,
    'xiulian': run_xiulian,
    'arena': run_arena,
}


# ---------------------------------------------------------------- 任务执行

def run_task(task, ctrl, emu, log):
    native = task.get('native')
    path = os.path.join(TASK_DIR, task['file']) if task.get('file') else None
    if path and not os.path.exists(path):
        log.error(u'找不到脚本: %s' % path)
        return 1

    log.step(u'准备运行【%s】(%s)' % (task['name'], task.get('file') or u'新版驱动'))

    if task['ocr'] and not ctrl.ocr.available():
        raise OcrUnavailable(
            u'【%s】需要文字识别, 但 OCR 没配好(当前引擎: %s)。\n%s'
            % (task['name'], ctrl.ocr.backend, ctrl.ocr.install_hint()))

    # 确认游戏在前台, 不在就拉起来
    focus = emu.current_focus()
    log.info(u'当前前台窗口: %s' % (focus or u'未知'))
    if not emu.game_in_front():
        log.warn(u'《问道》不在前台, 尝试拉起…')
        emu.launch_game()

    if emu.input_mode == 'mouse':
        emu.focus_window()

    # 开始前的倒计时, 方便手动把画面切到任务起点
    countdown = int(float(cfg_get(emu.cfg, 'run', 'countdown', '5') or 5))
    for i in range(countdown, 0, -1):
        log.info(u'%d 秒后开始(按 ESC 取消)…' % i)
        _wait_interruptible(1.0, ctrl)
        if ctrl.stop.is_set():
            raise AutomationAborted()

    install_stoppable_sleep(ctrl)
    log.step(u'开始执行【%s】, 运行中随时按 ESC 中断' % task['name'])

    result = {'exc': None}

    def worker():
        try:
            if native:
                NATIVE_TASKS[native](ctrl, emu, ctrl.mapper, log, ctrl.ocr,
                                     rounds=task.get('rounds', 12))
            else:
                runpy.run_path(path, run_name='__main__')
        except BaseException as e:                       # noqa: BLE001 - 交给主线程报告
            result['exc'] = e

    th = threading.Thread(target=worker, name='asktao-task')
    th.daemon = True
    th.start()

    try:
        _watch_task(th, ctrl, log)
    except KeyboardInterrupt:
        ctrl.stop.set()
        log.warn(u'收到 Ctrl+C, 正在中断…')
        th.join(timeout=5)

    if ctrl.stop.is_set() and result['exc'] is None:
        log.warn(u'任务已中断 (共点击 %d 次)' % ctrl.tap_count)
        return 130

    exc = result['exc']
    if exc is None:
        log.step(u'【%s】执行完毕 (共点击 %d 次)' % (task['name'], ctrl.tap_count))
        return 0
    if isinstance(exc, AutomationAborted):
        log.warn(u'任务已中断 (共点击 %d 次)' % ctrl.tap_count)
        return 130
    log.error(u'【%s】执行出错: %s' % (task['name'], exc))
    log.error(traceback.format_exc())
    return 1


def _read_abort_key():
    """非阻塞读一个按键, 是 ESC/q 返回 True。"""
    if not IS_WIN:
        return False
    try:
        import msvcrt
    except ImportError:
        return False
    while msvcrt.kbhit():
        if msvcrt.getch() in (b'\x1b', b'q', b'Q'):
            return True
    return False


def _wait_interruptible(seconds, ctrl):
    """等待若干秒, 期间监听 ESC / q。"""
    end = time.monotonic() + float(seconds)
    while True:
        if _read_abort_key() and not ctrl.stop.is_set():
            ctrl.log.warn(u'收到中断指令, 正在停止…')
            ctrl.stop.set()
        if ctrl.stop.is_set() or time.monotonic() >= end:
            return
        _real_sleep(0.1)


def _watch_task(th, ctrl, log):
    """主线程盯着任务线程, 同时监听 ESC / q。"""
    while th.is_alive():
        _wait_interruptible(0.1, ctrl)
    th.join(timeout=5)


# ---------------------------------------------------------------- 工具命令

def cmd_check(emu, mapper, ocr, log):
    log.step(u'===== 环境自检 =====')
    log.info(u'Python: %s' % sys.version.split()[0])
    log.info(u'解释器: %s' % sys.executable)
    log.info(u'脚本目录: %s' % BASE_DIR)
    log.info(u'Pillow: %s' % (u'OK' if Image else u'缺失(截图/OCR 不可用)'))
    try:
        import numpy  # noqa: F401
        log.info(u'numpy: OK')
    except ImportError:
        log.info(u'numpy: 未安装(不影响本启动器)')

    log.info(u'adb: %s' % emu.adb)
    if os.path.exists(emu.adb) or emu.adb == 'adb':
        try:
            emu.ensure_device()
            emu.get_screen_size()
            focus = emu.current_focus()
            log.info(u'前台应用: %s' % (focus or u'未知'))
            if emu.game_in_front():
                log.info(u'《问道》(%s) 正在前台' % emu.package)
            else:
                log.warn(u'《问道》(%s) 不在前台, 可用 --launch 拉起' % emu.package)
        except Exception as e:
            log.error(u'模拟器连接失败: %s' % e)
    else:
        log.error(u'adb 路径不存在: %s' % emu.adb)

    log.info(u'点击方式: %s' % emu.input_mode)
    log.info(u'坐标换算: %s' % mapper.describe())
    log.info(u'OCR 引擎(%s): %s' % (
        ocr.backend, ocr.describe() if ocr.available() else u'未就绪(任务 3/4 需要)'))
    for t in TASKS:
        fname = t.get('file') or u'新版驱动'
        if t.get('file'):
            p = os.path.join(TASK_DIR, t['file'])
            state = u'存在' if os.path.exists(p) else u'缺失'
        else:
            state = u'内置'
        log.info(u'任务 %s %s -> %s %s' % (t['key'], t['name'], fname, state))
    log.info(u'配置文件: %s' % CONFIG_PATH)
    return 0


def cmd_shot(emu, log):
    os.makedirs(LOG_DIR, exist_ok=True)
    out = os.path.join(LOG_DIR, 'shot_%s_%s.png'
                       % (INSTANCE_TAG or u'x', datetime.now().strftime('%H%M%S')))
    emu.screencap().save(out)
    log.info(u'已保存模拟器截图: %s' % out)
    return 0


def cmd_ocr_test(emu, mapper, ocr, log, region=None):
    """截当前画面做一次 OCR, 只验证密钥/额度/识别效果, 不点击任何东西。"""
    if not ocr.available():
        raise OcrUnavailable(u'OCR 还没配好(当前引擎: %s)。\n%s'
                             % (ocr.backend, ocr.install_hint()))
    img = emu.screencap()
    if region:
        img = img.crop(region)
        log.info(u'只识别区域 %s' % (region,))
    if img.width < 800 and ocr.upscale != 1.0:
        img = img.resize((int(img.width * ocr.upscale), int(img.height * ocr.upscale)),
                         Image.LANCZOS)
    os.makedirs(TMP_DIR, exist_ok=True)
    path = os.path.join(TMP_DIR, 'ocr_test.png')
    img.save(path)
    log.info(u'送识别图片: %s (%dx%d), 引擎 %s' % (path, img.width, img.height, ocr.describe()))

    started = time.monotonic()
    text = ocr.recognize(path)
    log.info(u'识别耗时 %.2f 秒' % (time.monotonic() - started))
    log.info(u'识别结果: %s' % (text if text else u'(什么都没有识别到)'))
    return 0


def _tool_ocr(log, ocr=None):
    """校准工具优先用本地 OCR —— 免费、不限量, 而且能给出每个文字的坐标。"""
    if ocr is not None and ocr.local is not None and ocr.local.available():
        return ocr.local
    local = LocalOcr(log)
    if not local.available():
        raise OcrUnavailable(
            u'--dump / --find 需要本地 OCR(要能给出文字坐标)。\n'
            u'  执行: "%s" -m pip install rapidocr-onnxruntime' % sys.executable)
    return local


def _to_original(mapper, cx, cy):
    """屏幕像素 -> 老脚本坐标系(逆运算)。"""
    return (mapper.ref_x + (cx - mapper.off_x) / mapper.kx,
            mapper.ref_y + (cy - mapper.off_y) / mapper.ky)


CLICK_RE = re.compile(r'mousemove_click\(\s*(\d+)\s*,\s*(\d+)\s*\)')
REGION_RE = re.compile(r'screenshot\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,')


def scan_task_coords():
    """扫描 task/ 下所有脚本, 抽出它们写死的点击坐标和截图区域。"""
    clicks, regions = [], []
    for t in TASKS:
        if not t.get('file'):
            continue
        path = os.path.join(TASK_DIR, t['file'])
        if not os.path.exists(path):
            continue
        with open(path, encoding='utf-8', errors='replace') as f:
            for lineno, line in enumerate(f, 1):
                code = line.split('#')[0]
                for m in CLICK_RE.finditer(code):
                    clicks.append((t['name'], t['file'], lineno, int(m.group(1)), int(m.group(2))))
                for m in REGION_RE.finditer(code):
                    regions.append((t['name'], t['file'], lineno,
                                    int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))))
    return clicks, regions


def cmd_coords(log, remap=None):
    """列出老脚本用到的全部坐标, 作为重新校准的清单。"""
    remap = remap or {}
    clicks, regions = scan_task_coords()

    uniq = {}
    for name, fname, lineno, x, y in clicks:
        uniq.setdefault((x, y), []).append(u'%s:%d' % (fname, lineno))

    print(u'')
    print(u'=== 老脚本写死的点击坐标(共 %d 处, 去重后 %d 个点位) ===' % (len(clicks), len(uniq)))
    print(u'%-14s %-10s %s' % (u'老坐标', u'当前映射', u'出现位置'))
    for (x, y) in sorted(uniq):
        cur = remap.get((x, y))
        flag = u' -> '.join(u'%d,%d' % p for p in cur) if cur else u'(未登记)'
        print(u'  (%4d,%4d)   %-12s %s' % (x, y, flag, u'  '.join(uniq[(x, y)])))

    if regions:
        print(u'')
        print(u'=== 截图区域(坐标同样会漂移, 需要重新量) ===')
        for name, fname, lineno, x1, y1, x2, y2 in regions:
            print(u'  %s:%d  [%d,%d -> %d,%d]  (%s)' % (fname, lineno, x1, y1, x2, y2, name))

    out = os.path.join(LOG_DIR, '坐标清单.txt')
    with open(out, 'w', encoding='utf-8') as f:
        f.write(u'# 老脚本用到的坐标清单(去重)\n')
        for (x, y) in sorted(uniq):
            cur = remap.get((x, y))
            f.write(u'%d,%d = %s   ; %s\n'
                    % (x, y, (u'; '.join(u'%d,%d' % p for p in cur)) if cur else u'?,?',
                       u' '.join(uniq[(x, y)])))
    print(u'')
    log.info(u'清单已导出: %s' % out)
    log.info(u'把实测到的新坐标填进 config.ini 的 [coords] 段即可, 不需要改 task/ 里的脚本')
    return 0


def cmd_dump(emu, mapper, log, label='', ocr=None):
    """把当前画面 OCR 一遍, 把所有文字连同原坐标导出成文本, 用于重新校准点击坐标。

    因为老脚本的坐标是按 2018 年的游戏界面录的, 现在的界面已经变了,
    所以需要一个"照着当前画面重新量坐标"的手段。导出成文本后可以逐条核对。
    """
    local = _tool_ocr(log, ocr)
    src = os.path.join(TMP_DIR, 'dump_src.png')
    emu.screencap().save(src)

    started = time.monotonic()
    boxes = local.boxes(src)
    log.info(u'识别到 %d 段文字, 耗时 %.2f 秒' % (len(boxes), time.monotonic() - started))
    if not boxes:
        log.warn(u'这一屏没识别到任何文字(可能画面是纯场景/过场动画)')

    label = (label or datetime.now().strftime('%H%M%S')).replace(' ', '_')
    out = os.path.join(LOG_DIR, 'ocr_dump_%s%s.txt'
                       % (label, (u'_' + INSTANCE_TAG) if INSTANCE_TAG else u''))
    lines = [
        u'# 《问道》画面文字坐标导出   %s' % datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        u'# 模拟器 %dx%d   %s' % (emu.scr_w, emu.scr_h, mapper.describe()),
        u'# 用法: 把"原坐标"那列填进 task/*.py 的 mousemove_click(x, y)',
        u'#',
        u'# %-15s %-16s %-7s %s' % (u'屏幕坐标', u'原坐标', u'置信度', u'文字'),
    ]
    for text, score, cx, cy in sorted(boxes, key=lambda b: (round(b[3] / 20.0), b[2])):
        ox, oy = _to_original(mapper, cx, cy)
        lines.append(u'  (%4d,%4d)       (%4d,%4d)       %.2f    %s'
                     % (cx, cy, round(ox), round(oy), score, text))
    with open(out, 'w', encoding='utf-8') as f:
        f.write(u'\n'.join(lines) + u'\n')

    for line in lines[4:]:
        print(line)
    log.info(u'已导出: %s' % out)
    return 0


def cmd_find(emu, mapper, log, keywords, ocr=None):
    """在当前画面上找指定文字, 打印它的屏幕坐标和原坐标。"""
    local = _tool_ocr(log, ocr)
    src = os.path.join(TMP_DIR, 'find_src.png')
    emu.screencap().save(src)
    boxes = local.boxes(src)
    log.info(u'整屏识别到 %d 段文字' % len(boxes))

    hit = 0
    for kw in keywords:
        matches = [b for b in boxes if kw in b[0]]
        if not matches:
            log.warn(u'没找到: %s' % kw)
            continue
        for text, score, cx, cy in matches:
            ox, oy = _to_original(mapper, cx, cy)
            hit += 1
            log.info(u'[%s] 屏幕(%d,%d) -> 原坐标(%d,%d) 置信度%.2f  整条: %s'
                     % (kw, cx, cy, round(ox), round(oy), score, text))
    return 0 if hit else 1


def cmd_pick(emu, mapper, log):
    """用鼠标"指"出按钮位置: 把鼠标移到模拟器画面上那个按钮上(不用点), 回到本窗口按回车。

    OCR 认不出的图形按钮(纯图标、花体字)用这个量坐标最省事。
    """
    if not IS_WIN:
        log.error(u'--pick 只支持 Windows')
        return 2
    log.info(u'正在把模拟器窗口切到前台…')
    left, top, cw, ch = emu.focus_window()
    log.info(u'模拟器客户区: 屏幕(%d,%d) 尺寸 %dx%d' % (left, top, cw, ch))
    log.info(u'请把鼠标移到模拟器画面里的目标按钮上(**不要点击**), '
             u'然后切回本窗口按回车…')
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        log.warn(u'已取消')
        return 1

    from ctypes import wintypes
    pt = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    ex = (pt.x - left) * emu.scr_w / float(cw)
    ey = (pt.y - top) * emu.scr_h / float(ch)
    if not (0 <= ex < emu.scr_w and 0 <= ey < emu.scr_h):
        log.warn(u'鼠标不在模拟器画面内(算出来是 %d,%d), 请把鼠标移到模拟器窗口上再试' % (ex, ey))
    ox, oy = _to_original(mapper, ex, ey)
    ex, ey = int(round(ex)), int(round(ey))
    ox, oy = int(round(ox)), int(round(oy))
    log.info(u'鼠标屏幕坐标 (%d,%d)' % (pt.x, pt.y))
    log.info(u'-> 模拟器坐标 (%d,%d)' % (ex, ey))
    log.info(u'-> 原坐标     (%d,%d)' % (ox, oy))
    log.info(u'填进 config.ini 的 [coords] 就是:   老坐标 = %d,%d' % (ox, oy))
    return 0


def cmd_grid(emu, mapper, log, marks):
    """在模拟器截图上画出老脚本坐标系的网格, 用来核对/重录坐标。"""
    if ImageDraw is None:
        raise RuntimeError(u'缺少 Pillow, 无法生成网格图')
    img = emu.screencap()
    draw = ImageDraw.Draw(img)
    w, h = img.size

    # 每 100 个原坐标单位画一条线, 并标注原坐标值
    for hx in range(0, 2000, 100):
        ex, _ = mapper.point(hx, mapper.ref_y)
        if 0 <= ex < w:
            draw.line([(ex, 0), (ex, h)], fill=(255, 80, 80), width=1)
            draw.text((ex + 2, 2), str(hx), fill=(255, 80, 80))
    for hy in range(0, 1400, 100):
        _, ey = mapper.point(mapper.ref_x, hy)
        if 0 <= ey < h:
            draw.line([(0, ey), (w, ey)], fill=(80, 200, 255), width=1)
            draw.text((2, ey + 2), str(hy), fill=(80, 200, 255))

    for (hx, hy) in marks:
        ex, ey = mapper.point(hx, hy)
        draw.line([(ex - 14, ey), (ex + 14, ey)], fill=(0, 255, 0), width=2)
        draw.line([(ex, ey - 14), (ex, ey + 14)], fill=(0, 255, 0), width=2)
        draw.text((ex + 6, ey + 6), u'%s,%s' % (hx, hy), fill=(0, 255, 0))

    os.makedirs(LOG_DIR, exist_ok=True)
    out = os.path.join(LOG_DIR, 'grid_preview%s.png'
                       % ((u'_' + INSTANCE_TAG) if INSTANCE_TAG else u''))
    img.save(out)
    log.info(u'网格预览已保存: %s' % out)
    log.info(u'红色竖线 = 原坐标的 X(顶部数字), 蓝色横线 = 原坐标的 Y(左侧数字)')
    log.info(u'照图就能读出画面上任意按钮对应的原坐标, 再填回 config.ini 或改脚本')
    log.info(u'%s' % mapper.describe())
    return 0


# ---------------------------------------------------------------- 交互菜单

def _ask_custom_point(prompt):
    while True:
        try:
            raw = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if not raw:
            return None
        parts = raw.replace(',', ' ').split()
        if len(parts) == 2:
            try:
                return float(parts[0]), float(parts[1])
            except ValueError:
                pass
        print(u'  请输入两个数字, 例如: 771,203')


def interactive(ctrl, emu, mapper, ocr, log, args):
    while True:
        print('')
        print(u'=' * 56)
        print(u'  《问道》自动化启动器   [雷电模拟器]')
        print(u'  设备: %s   分辨率: %dx%d   点击: %s'
              % (emu.serial or u'未连接', emu.scr_w, emu.scr_h, emu.input_mode))
        print(u'=' * 56)
        for t in TASKS:
            flag = u'  [需OCR]' if t['ocr'] else u''
            print(u'  %s. %-10s %-22s%s' % (t['key'], t['name'], t.get('file') or u'内置', flag))
            print(u'       %s' % t['desc'])
        print(u'  c. 环境自检        g. 坐标网格预览')
        print(u'  s. 截取当前画面    t. 试点击原坐标')
        print(u'  0. 退出')
        print(u'  提示: 任务运行中随时按 ESC 中断')
        try:
            choice = input(u'请选择> ').strip().lower()
        except (EOFError, KeyboardInterrupt):
            print('')
            return 0

        if choice in ('0', 'q', 'exit'):
            return 0
        if choice == 'c':
            cmd_check(emu, mapper, ocr, log)
            continue
        if choice == 'g':
            cmd_grid(emu, mapper, log, [])
            continue
        if choice == 's':
            cmd_shot(emu, log)
            continue
        if choice == 't':
            pt = _ask_custom_point(u'输入原坐标 x,y (回车返回)> ')
            if pt:
                if emu.input_mode != 'adb':
                    emu.focus_window()
                ctrl.tap(pt[0], pt[1])
            continue

        task = next((t for t in TASKS if t['key'] == choice), None)
        if not task:
            print(u'无效选择, 请重新输入')
            continue

        try:
            run_task(task, ctrl, emu, log)
        except OcrUnavailable as e:
            log.error(str(e))
        except AutomationAborted:
            log.warn(u'已取消')
        except Exception as e:
            log.error(u'任务失败: %s' % e)
            log.error(traceback.format_exc())
        finally:
            ctrl.stop.clear()
    return 0


# ---------------------------------------------------------------- 入口

def parse_args(argv):
    p = argparse.ArgumentParser(
        description=u'《问道》自动化启动器 (适配雷电模拟器)',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('task', nargs='?', help=u'任务序号(1-4), 直接运行')
    p.add_argument('--list', action='store_true', help=u'列出所有任务')
    p.add_argument('--check', action='store_true', help=u'环境自检')
    p.add_argument('--grid', action='store_true', help=u'生成坐标网格预览图')
    p.add_argument('--mark', action='append', default=[],
                   help=u'在网格图上标记原坐标, 如 --mark 771,203 (可重复)')
    p.add_argument('--coords', action='store_true',
                   help=u'列出 task/ 脚本里写死的全部坐标, 生成待校准清单')
    p.add_argument('--dump', nargs='?', const='', metavar='名称',
                   help=u'导出当前画面全部文字的原坐标(本地OCR, 用于重新校准)')
    p.add_argument('--find', action='append', default=[], metavar='文字',
                   help=u'定位画面上的文字并给出原坐标, 可重复使用')
    p.add_argument('--ocr-test', nargs='?', const='', metavar='X1,Y1,X2,Y2',
                   help=u'截当前画面做一次文字识别(不点击), 可指定原坐标区域')
    p.add_argument('--tap', metavar='X,Y', help=u'试点击一个原坐标')
    p.add_argument('--shot', action='store_true', help=u'截取模拟器当前画面')
    p.add_argument('--pick', action='store_true',
                   help=u'用鼠标指出按钮位置来量坐标(OCR 认不出的图形按钮用这个)')
    p.add_argument('--launch', action='store_true', help=u'把《问道》拉到前台后退出')
    p.add_argument('--serial', metavar='设备号',
                   help=u'指定模拟器设备(多开时用), 如 emulator-5556; 不填则用 config.ini 的')
    p.add_argument('--dry-run', action='store_true', help=u'空跑: 只打印点击动作不真的点')
    p.add_argument('--rounds', type=int, default=0,
                   help=u'新版师门驱动跑几轮(默认 12, 每轮约 75 秒)')
    p.add_argument('--time-scale', type=float, default=1.0,
                   help=u'等待时间加速倍数(调试用, 如 30 = 快 30 倍)')
    p.add_argument('--verbose', action='store_true', help=u'输出调试日志')
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)

    if IS_WIN:
        # 让窗口/屏幕坐标按物理像素算, 避免缩放导致点击偏移
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    os.chdir(BASE_DIR)
    for d in (LOG_DIR, TMP_DIR, CAP_DIR, RES_DIR):
        os.makedirs(d, exist_ok=True)

    cfg = load_config()
    verbose = args.verbose or (cfg_get(cfg, 'run', 'verbose', 'true').lower() != 'false')
    _tag = re.sub(u'[^0-9A-Za-z_.-]', u'_',
                  args.serial or cfg_get(cfg, 'emulator', 'serial', '') or u'default')
    log_path = os.path.join(LOG_DIR, 'run_%s_%s.log'
                            % (_tag, datetime.now().strftime('%Y%m%d_%H%M%S')))
    log = Log(log_path, verbose)

    if args.coords:
        return cmd_coords(log, load_coord_map(cfg, log))

    if args.list:
        for t in TASKS:
            print(u'%s. %-10s %-24s %s%s' % (t['key'], t['name'], t.get('file') or u'内置',
                                             t['desc'], u'  [需OCR]' if t['ocr'] else u''))
        return 0

    emu = Emulator(cfg, log)
    if args.serial:
        emu.serial = args.serial.strip()
    set_instance(emu.serial)
    ocr = OcrEngine(cfg, log)

    try:
        # 自检不强制连模拟器, 连不上就用 1600x900 估算
        if args.check:
            try:
                emu.ensure_device(auto_connect=False)
                emu.get_screen_size()
            except RuntimeError as e:
                log.warn(u'模拟器暂不可用(%s), 分辨率按 1600x900 估算' % e)
                emu.scr_w, emu.scr_h = 1600, 900
            mapper = Mapper(cfg_get(cfg, 'emulator', 'ref_x', '388'),
                            cfg_get(cfg, 'emulator', 'ref_y', '194'),
                            cfg_get(cfg, 'emulator', 'ref_w', '1179'),
                            cfg_get(cfg, 'emulator', 'ref_h', '661'),
                            emu.scr_w, emu.scr_h,
                            cfg_get(cfg, 'emulator', 'offset_x', '0'),
                            cfg_get(cfg, 'emulator', 'offset_y', '0'))
            return cmd_check(emu, mapper, ocr, log)

        emu.ensure_device()
        emu.get_screen_size()

        mapper = Mapper(cfg_get(cfg, 'emulator', 'ref_x', '388'),
                        cfg_get(cfg, 'emulator', 'ref_y', '194'),
                        cfg_get(cfg, 'emulator', 'ref_w', '1179'),
                        cfg_get(cfg, 'emulator', 'ref_h', '661'),
                        emu.scr_w, emu.scr_h,
                        cfg_get(cfg, 'emulator', 'offset_x', '0'),
                        cfg_get(cfg, 'emulator', 'offset_y', '0'))
        log.info(u'坐标换算: %s' % mapper.describe())

        coord_map = load_coord_map(cfg, log)
        if coord_map:
            log.info(u'坐标修正表: 已登记 %d 个点位' % len(coord_map))
        ctrl = Controller(emu, mapper, log, dry_run=args.dry_run,
                          time_scale=args.time_scale, ocr=ocr, remap=coord_map)

        if args.shot:
            return cmd_shot(emu, log)
        if args.pick:
            return cmd_pick(emu, mapper, log)
        if args.launch:
            emu.launch_game()
            log.info(u'前台应用: %s' % emu.current_focus())
            return 0
        if args.grid or args.mark:
            marks = []
            for m in args.mark:
                try:
                    x, y = m.replace(',', ' ').split()
                    marks.append((float(x), float(y)))
                except ValueError:
                    log.warn(u'忽略无法解析的 --mark: %s' % m)
            return cmd_grid(emu, mapper, log, marks)
        if args.dump is not None:
            return cmd_dump(emu, mapper, log, args.dump, ocr)
        if args.find:
            return cmd_find(emu, mapper, log, args.find, ocr)
        if args.ocr_test is not None:
            region = None
            if args.ocr_test.strip():
                try:
                    nums = [float(v) for v in args.ocr_test.replace(',', ' ').split()]
                    if len(nums) != 4:
                        raise ValueError
                    region = mapper.rect(*nums)
                except ValueError:
                    log.error(u'--ocr-test 区域格式应为 x1,y1,x2,y2 (原坐标)')
                    return 2
            return cmd_ocr_test(emu, mapper, ocr, log, region)
        if args.tap:
            try:
                x, y = args.tap.replace(',', ' ').split()
            except ValueError:
                log.error(u'--tap 格式应为 x,y')
                return 2
            if emu.input_mode == 'mouse':
                emu.focus_window()
            # 走 Controller, 这样也会应用 [coords] 修正表, 与任务脚本行为一致
            ctrl.tap(float(x), float(y))
            return 0

        # 注入兼容层, 之后 task/ 下的老脚本可以直接跑
        install_shims(ctrl, ocr, log)
        sys.path.insert(0, TASK_DIR)

        if args.task:
            task = next((t for t in TASKS if t['key'] == str(args.task)), None)
            if not task:
                log.error(u'没有编号为 %s 的任务, 用 --list 查看' % args.task)
                return 2
            if args.rounds:
                task = dict(task, rounds=args.rounds)
            return run_task(task, ctrl, emu, log)

        return interactive(ctrl, emu, mapper, ocr, log, args)

    except OcrUnavailable as e:
        log.error(str(e))
        return 3
    except AutomationAborted:
        log.warn(u'已取消')
        return 130
    except RuntimeError as e:
        log.error(str(e))
        return 1
    except KeyboardInterrupt:
        log.warn(u'已退出')
        return 130
    finally:
        log.info(u'日志: %s' % log_path)
        log.close()


if __name__ == '__main__':
    sys.exit(main())
