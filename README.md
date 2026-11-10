# 连点器 AutoClicker Pro

Windows 上免安装的连点器 / 自动化小工具。**标准版 + 傻瓜化**：全程点点点和填数字，不需要写代码。

面向普通用户的说明在 [使用说明.md](使用说明.md)（也有 [使用说明.html](使用说明.html) 可以直接双击看）。
功能设计见 [连点器-功能设计.md](连点器-功能设计.md)。

---

## 功能一览

| 模块 | 能力 |
|---|---|
| 连点 | 左/右/中/侧键/滚轮、单击/双击/三连击/长按/拖拽、CPS 或固定间隔或随机区间、正态分布、拟人抖动、坐标随机偏移、随机微停顿、随机漏点 |
| 运行约束 | 无限 / 指定次数 / 指定时长、启动倒计时、前台窗口限定、后台点击（不抢鼠标） |
| 位置收藏 | 热键 F9 存点、屏幕拾取、最多 8+ 个点位、点位专属热键、跟随窗口（窗口移动后自动跟着走）、屏幕十字标记 |
| 智能识别 | **文字 OCR**（系统自带中文引擎）、**找图**（模板匹配，多尺度）、**找色**；支持框选识别范围、相似度、多条任务、冷却时间、最多命中次数、命中后提示音、测试识别并画框 |
| 录制回放 | 录制鼠标/键盘（含时间戳）、0.2~5 倍速回放、循环、忽略停顿、导入导出 JSON |
| 安全 | ESC 急停、鼠标甩角急停、CPS 上限二次确认、目标窗口关闭即停 |
| 其他 | 全局热键（可绑鼠标侧键）、深色/浅色主题、迷你悬浮面板、系统托盘、配置档、计划任务、开机自启、单实例 |

---

## 目录结构

```
AutoClicker/
├─ main.py                  程序入口（含单实例互斥、DPI 感知）
├─ app/
│  ├─ winapi.py             Win32 封装：SendInput 注入、窗口信息、热键解析、函数签名声明
│  ├─ screen.py             GDI BitBlt 截屏（多显示器、负坐标）
│  ├─ config.py             配置读写（便携、深合并、就地合并、配置档）
│  ├─ clicker.py            连点引擎线程（节奏、限制、拟人化、终止条件）
│  ├─ hotkeys.py            WH_KEYBOARD_LL + WH_MOUSE_LL 全局钩子（热键/急停/取点/录制源）
│  ├─ recognizer.py         识别任务调度（轮询、命中、点击、冷却）
│  ├─ ocr.py                OCR 抽象层（Windows OCR / RapidOCR / Tesseract）
│  ├─ ocr_bridge.ps1        常驻 PowerShell OCR 服务（必须 UTF-8 with BOM）
│  ├─ matcher.py            纯 numpy 的 FFT 归一化互相关找图（多尺度 + NMS）
│  ├─ recorder.py           录制与回放
│  └─ ui/                   tkinter 界面（主题、组件、5 个页签、覆盖层、迷你面板、托盘）
├─ tests/                   自测脚本（见下）
├─ tools/                   图标生成、说明文档转换、打包脚本、启动器源码
├─ assets/                  图标
├─ dist/连点器/             打包产物（免安装，双击 连点器.exe；不在仓库里，需自己跑打包脚本）
└─ config/                  运行期配置、点位、模板图
```

---

## 安装与运行（从源码）

仓库里只有源码，没有打包好的 exe（`dist/` 已排除，需要自己生成，见下）。

```powershell
# 1. 需要 Python 3.12（tkinter 是标准库，不用额外装）
python --version

# 2. 装依赖：numpy + Pillow
pip install -r requirements.txt

# 3. 启动
python main.py
```

无网络环境下也能跑：OCR 用系统自带的 Windows OCR（`zh-Hans-CN` 需系统语言包），找图用自研 numpy 实现，不依赖 OpenCV。

---

## 测试

```powershell
python tests\smoke_test.py            # 30+ 项：截屏、热键解析、连点节奏、钩子、录制、UI 构建、真实注入
python tests\test_features.py         # 21 项：F9 存点、位置卡片、回放、配置档、计划任务、开机自启、主题、热键线程安全
python tests\test_recognize_e2e.py    # 13 项：屏幕上显示中文 → OCR 找到 → 鼠标真的点过去（含找图、托盘、标记）
python tests\test_ocr.py              # 83 项：OCR 中文拼接、坐标精度、进程复用、健壮性、并发
python tests\test_matcher.py          # 8 项：NCC 与暴力公式逐点对照、多尺度、多目标、性能与内存
python tests\shot_ui.py               # 给各页签截图到 docs/screenshots
python tools\diag_overlay.py          # 诊断：屏幕标记/识别范围框到底画出来没有（按像素统计）
```

实测性能：全屏截屏 5 ms；OCR 700×220 图 32 ms / 1920×1080 全屏 63 ms；找图 1707×1067 + 100×100 模板单尺度 320 ms。

---

## 打包成免安装文件夹

先把 `tools/build_portable.ps1` 第 6 行的 `$src` 改成**你自己的 Python 安装目录**（该目录下要有 `python.exe`），然后：

```powershell
powershell -ExecutionPolicy Bypass -File tools\build_portable.ps1
```

产出 `dist\连点器\`：自带 Python 运行时（裁剪过，去掉 pandas/pip 等无关库，约 155 MB）、`连点器.exe`（csc 编译的启动器，带图标、不弹黑框）、备用 bat、使用说明、截图、源码。

> 脚本必须以 UTF-8 with BOM 保存，否则 Windows PowerShell 5.1 会按 ANSI 读中文导致语法错误。

---

## 已知限制

- **点击需要同桌面会话**：如果连点器运行在隔离会话/服务里，`SendInput` 会返回成功但事件送不到目标窗口（本项目测试环境即如此，所以注入测试是点自己创建的置顶窗口）。
- **后台点击（PostMessage）** 对部分程序无效（尤其游戏），需要实测。
- **目标程序以管理员运行时**，连点器也要提权才能点到它。
- **文字识别依赖系统语言包**：没有中文 OCR 语言包时 `available=False`，此时可改用找图/找色。
- 只做了 Windows 10/11 x64 适配。

---

## 沙箱环境注意事项

如果在本项目的 DSH 沙箱里开发，注意：`tempfile.mkdtemp()` 或 `os.mkdir(p, 0o700)` 在工作区内建出的目录会带上「自己也访问不了」的权限（`mkstemp` 文件不受影响）。`app/` 下所有运行期目录都用 `mkdir(exist_ok=True)` 默认权限，不受影响。

项目根目录里遗留的 5 个空目录（`tmp1e9kg4em`、`tmpmq23whdi`、`_matcher_io_0ikdnz74`、`_matcher_io_lfatacr7`、`_probe_mode_0700`）就是当初排查这个现象时留下的，沙箱内删不掉，需要在沙箱外手动删除，不影响程序运行。

## 许可

MIT License，见 [LICENSE](LICENSE)。

## 许可

MIT License，见 [LICENSE](LICENSE)。

## 相关

- 另有配套的 [DSH 插件合集](https://github.com/Kcx520/dsh-plugins)
