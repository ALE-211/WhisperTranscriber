# WhisperTranscriber

基于 **faster-whisper** 的 Windows 桌面音视频转录工具：选文件、选模型、点一下，
得到中文文字稿（txt / srt / md），进度条实时显示百分比、速度和剩余时间。

<div align="center">

[![Python Version](https://img.shields.io/badge/python-3.12-blue)](https://www.python.org/)
[![PyQt5](https://img.shields.io/badge/PyQt5-5.15-green)](https://pypi.org/project/PyQt5/)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

</div>

## 功能

- **四种模型可选**：large-v3-turbo / large-v3 / medium / small，按机器性能自由切换
- **可导入自己的模型**：已有的 CTranslate2 / faster-whisper 模型直接选目录使用，不用重新下载
- **一键转录**：mp4 / mkv / mov / avi / wav / mp3 / m4a / flac 等常见格式
- **GPU 加速**：NVIDIA 显卡用 cuda + float16，无 GPU 自动回退 CPU（int8）
- **模型按需下载**：只使用阿里 ModelScope（魔搭），带进度、速度、剩余时间
- **环境自动修复**：自动注册/补齐 cublas、cudart、cudnn 的 DLL，解决 cublas64_12.dll not found
- **三种输出**：纯文本 .txt、字幕 .srt（毫秒级时间戳）、Markdown .md（带时间戳，适合做笔记）
- **实时日志 + 可中止**：转录过程全程可见，随时停止且不会留下半截文件
- **轻量 exe 启动器**：双击启动，不用命令行（约几十 KB，不打包 1 GB 依赖）

## 环境要求

| 项目 | 要求 |
| --- | --- |
| 系统 | Windows 10 / 11（64 位） |
| Python | 3.9 - 3.11（exe 启动器也依赖本机 Python） |
| 显卡 | NVIDIA 显卡（可选，推荐 ≥ 6 GB 显存） |
| FFmpeg | 已安装并加入 PATH，或把 ffmpeg.exe / ffprobe.exe 放到项目根目录 |
| 磁盘 | 模型 0.5 - 3 GB，模型可多选共存 |

## 快速开始

1. 安装 Python 3.10（安装时勾选 “Add Python to PATH”）。
2. 安装 FFmpeg 并加入 PATH（命令行输入 ~~~ffmpeg -version~~~ 能看到版本即成功）。
3. 在项目目录运行任意一种：

   ~~~bat
   python main.py
   ~~~

   或者双击 ~~~WhisperTranscriber.exe~~~（先用 ~~~python build_launcher.py~~~ 生成，见下文）。

首次启动会用清华镜像自动安装依赖，然后弹出界面。缺少 CUDA 运行库时会自动安装对应 pip 包并复制 DLL（约 1-2 GB，只需一次）。

## 生成 exe 启动器

~~~bat
python build_launcher.py
~~~

会在项目根目录生成 **WhisperTranscriber.exe**（约 80 KB），双击即可启动界面。
它只做一件事：找到本机的 ~~~pythonw.exe~~~，以项目目录为工作目录运行 ~~~main.py~~~。

- **不打包** Python、PyQt5、CUDA、模型，所以体积小、启动快，也不会像 PyInstaller 那样被
  杀毒软件误报、或者因为塞了 1 GB 依赖被 SmartScreen 反复拦。
- 依赖仍然用你机器上的 Python 环境；升级依赖不用重新生成 exe。
- 查找 Python 的顺序：环境变量 ~~~WHISPER_PYTHON~~~ → 项目内 .venv/venv/runtime →
  注册表 → 常见安装目录 → PATH。
- 支持把参数透传给 main.py：

~~~bat
WhisperTranscriber.exe --diagnose
WhisperTranscriber.exe --self-test
~~~

### 关于“无法验证发布者，是否要运行此软件？”

这条提示来自 Windows 的“打开文件 - 安全警告”，它检查两件事：文件是否**来自 Internet**、
发布者是否**有签名**。程序本身是本地用 Windows 自带的 C# 编译器从 ~~~launcher/Launcher.cs~~~
编译的，源码就在项目里，不联网，也不改系统设置。

> **为什么连一个空 bat 双击都会弹？**
> 我实测过：同一个文件放在桌面或 ~~~%TEMP%~ 里运行完全没提示，放回本目录就弹“来自 Internet - 未知发布者”。
> 也就是说，弹窗跟文件内容无关，是**所在文件夹被 Windows 标记成了“来自 Internet”**（本目录位于
> ~~~Useful Tools~~~ 这类从压缩包解压出来的下载工具目录里，整棵目录都带这个标记）。
> 因此哪怕空 bat、空快捷方式也会被问一次。

| 启动方式 | 会不会弹发布者提示 | 说明 |
| --- | --- | --- |
| 桌面快捷方式（推荐） | 不弹 | 在桌面新建快捷方式，目标 ~~~pythonw.exe~~~，参数为 ~~~main.py~~~ 完整路径 |
| ~~~WhisperTranscriber.exe~~~ | 本目录里会弹 | 点“运行”即可；要彻底不弹，把整个文件夹移到干净目录 |
| ~~~WhisperTranscriber.lnk~~~ / ~~~启动转录器.bat~~~ | 在本目录里会弹 | 同上，受文件夹的 Internet 标记影响 |
| ~~~python main.py~~~（终端） | 不弹 | 有控制台日志，便于排错 |

彻底消除 exe 的提示需要购买代码签名证书（给 exe 签名）。日常使用：把整个文件夹移到干净目录
（例如 ~~~C:\WhisperTranscriber~~~），或者在**桌面**建一个快捷方式指向 ~~~pythonw.exe~~~、
参数写 ~~~main.py~~~ 的完整路径——这样双击桌面图标即可启动，不受本目录标记影响。
移动文件夹后，旧的 .lnk 会失效：重新运行 ~~~python build_launcher.py~~~ 可重建，或继续用
~~~启动转录器.bat~~~（相对路径，跟着文件夹走）。

## 选择模型

界面顶部“使用模型”下拉框里有四种内置模型：

| 模型 | 体积 | 适用情况 |
| --- | --- | --- |
| large-v3-turbo（默认） | 约 1.5 GB | 速度快、中文准确，日常首选；RTX 3080 转 55 分钟视频约 10-20 分钟 |
| large-v3 | 约 2.9 GB | 精度最高、速度最慢，需要约 10 GB 显存 |
| medium | 约 1.4 GB | 精度略低于 large，速度明显更快 |
| small | 约 461 MB | 轻量，适合低显存或 CPU，先出草稿 |

选好模型后点“下载所选模型”。程序**不会**在启动时偷偷下载几个 GB 的模型，
只有你明确点击才会开始。下载源固定为 ModelScope（例如 large-v3 走 Systran/faster-whisper-large-v3），失败会自动切换备用仓库。

## 导入本地模型

如果你已经有 faster-whisper 模型（目录里包含 ~~~model.bin~~~、~~~config.json~~~、~~~tokenizer.json~~~）：

1. 点“导入本地模型...”。
2. 选中模型目录（选它的上级目录也行，程序会向下寻找 model.bin）。
3. 下拉框会出现“本地模型：xxx”，选中即可直接转录。

导入只记录路径，**不会复制文件**；点“移除导入”只删除记录，不动磁盘文件。

## 模型与配置存放位置

| 内容 | 位置 |
| --- | --- |
| 模型 | 项目目录下 ~~~models/<模型名>/~~~ |
| 配置（导入的模型、上次选择） | ~~~%LOCALAPPDATA%\WhisperTranscriber\config.json~~~ |
| 日志（exe / pythonw 启动时） | ~~~%LOCALAPPDATA%\WhisperTranscriber\logs\app.log~~~ |

程序**不会**自动扫描上级目录或其它项目里的 models 文件夹，避免把别人的模型误当成自己的。
需要指定模型根目录时用环境变量：

~~~bat
set WHISPER_MODEL_DIR=D:\ai-models\whisper
python main.py
~~~

## 输出格式示例

**txt**：一行一个识别片段，干净无时间戳，适合喂给大模型做总结。

**srt**：

~~~text
1
00:00:01,240 --> 00:00:04,880
大家好，今天我们来聊一聊这个项目。
~~~

**md**：

~~~markdown
# 视频文件名

- 源文件：D:\videos\meeting.mp4
- 时长：55 分 12 秒
- 识别语言：zh

---

**[00:00:01]** 大家好，今天我们来聊一聊这个项目。
~~~

## 项目结构

~~~text
WhisperTranscriber/
├── main.py                  # 唯一入口：依赖 -> 环境 -> GUI
├── WhisperTranscriber.exe   # 启动器（build_launcher.py 生成）
├── build_launcher.py        # 用 C# 编译器生成轻量启动器
├── build_launcher.bat       # 双击生成
├── launcher/Launcher.cs     # 启动器源码（找 pythonw 并启动 main.py）
├── requirements.txt
├── assets/                  # logo / 图标
├── core/                    # 纯 Python，无 UI 依赖
│   ├── paths.py             # 路径、用户数据目录
│   ├── models.py            # 模型清单、模型目录校验
│   ├── config.py            # 导入模型 / 上次选择
│   ├── env_checker.py       # CUDA / DLL 检查与修复
│   ├── downloader.py        # ModelScope 下载（按模型独立缓存目录）
│   ├── transcriber.py       # FFmpeg 提取 + 转录 + txt/srt/md 导出
│   └── summarizer.py        # 预留：接入大模型做总结
└── ui/
    └── app.py               # PyQt5 主窗口 + QThread 工作线程
~~~

## 常见问题

**1. 报错 Library cublas64_12.dll is not found**

执行下面的命令后重新启动，程序会自动注册/复制 DLL。

~~~bat
python -m pip install nvidia-cublas-cu12 nvidia-cuda-runtime-cu12 nvidia-cudnn-cu12 -i https://pypi.tuna.tsinghua.edu.cn/simple
~~~

复制失败（Program Files 权限问题）时用管理员身份运行一次。

**2. 模型下载很慢或失败**

本项目只用 ModelScope，**不要**改成 HuggingFace 或 hf-mirror（容易 401/404、下载残缺）。
网络不稳时重试即可，已下载文件会复用；每个模型的下载失败会自动尝试备用仓库。

**3. 没有显卡能用吗？**

能。检测不到 CUDA 会自动用 CPU（int8），small / medium 模型在 CPU 上比较现实，large 系列会很慢。

**4. 界面进度条不动 / 没有剩余时间**

如果 ffprobe 读不到时长（文件损坏或 FFmpeg 不在 PATH），进度条会变成滚动状态，日志里会提示。

**5. 双击 exe 没反应 / 提示找不到 Python**

- 看 ~~~%LOCALAPPDATA%\WhisperTranscriber\logs\app.log~~~；
- 运行 ~~~WhisperTranscriber.exe --diagnose~~~ 生成诊断文件；
- 确认已安装 Python 3 并勾选 Add to PATH，或设置 ~~~WHISPER_PYTHON~~~ 指向 pythonw.exe；
- 也可以用 ~~~python main.py~~~ 直接启动，能看到报错信息。

**6. 识别出来的中文夹着英文，或有重复句**

默认已设置 condition_on_previous_text=False、language=zh。若仍有问题，勾选“过滤静音片段（VAD）”，
或剪掉音频里无人声的片段。模型是多语言版，不需要改 config.json。

## 开发说明

- ~~~core/~~~ 不 import PyQt5，任何模块都可以在命令行/脚本中单独调用：

~~~python
from pathlib import Path
from core.transcriber import Transcriber

tx = Transcriber(Path("models/large-v3-turbo"), language="zh", log=print)
result = tx.transcribe(Path("demo.mp4"), Path("demo.srt"), fmt="srt")
print(result.segment_count, result.device)
~~~

- 新增模型：在 ~~~core/models.py~~~ 的 ~~~BUILTIN_MODELS~~~ 里加一条（仓库 ID 用 ModelScope 的）。
- 新增输出格式：在 ~~~core/transcriber.py~~~ 加 writer 类，并注册到 ~~~FORMAT_SUFFIX~~~ / ~~~FORMAT_LABELS~~~。
- 接入大模型总结：实现 ~~~core/summarizer.py~~~ 的 ~~~summarize()~~~，读取 .md/.txt 后调用 DeepSeek API 或本地 Ollama。
- UI 线程安全：所有耗时任务都在 QThread 中，子线程只 emit 信号，不直接操作控件。
