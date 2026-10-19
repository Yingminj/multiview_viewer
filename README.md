# multiviewer — 多视角视频查看器

Ubuntu 桌面工具，使用 **PySide6 + PyAV**。输入 episode 文件夹后默认显示
`ego/rgb.mp4`、`cam_left.mkv`、`cam_right.mkv`，其他视频通过侧边栏勾选加入。
加载后显示版本与对齐状态，点击播放开始。默认仅对背包 v2.1.14 自动对齐三路 RGB，其他版本按首帧播放。数据目录以只读方式访问。

## 安装说明（其他开发者）

工具可以放在任意有读写权限的目录，不依赖原开发者的用户名、数据路径或 `lerobot` 环境。
以下命令均在 Ubuntu 终端执行，安装 Python 包需要访问软件源。

### 1. 准备环境和源码

- Ubuntu 桌面环境；打开窗口需要可用的 X11 或 Wayland 会话。
- Python 3.10 或以上；本项目已验证 Python 3.12，其他版本组合未逐一测试。
- 收到源码后解压，进入包含 `viewer.py`、`requirements.txt` 和 `setup.sh` 的目录。
- 本机验证依赖版本：PySide6 6.12.0、PyAV 15.1.0、Pillow 12.3.0、NumPy 2.2.6。
  `requirements.txt` 给出了允许的版本范围，安装结果可能与上述版本不同。

```bash
cd /path/to/multiviewer
```

### 2. 安装 Ubuntu 系统依赖

```bash
sudo apt update
sudo apt install -y \
  python3 python3-venv python3-pip \
  libgl1 libegl1 libxkbcommon-x11-0 \
  libxcb-cursor0 libxcb-icccm4 libxcb-image0 \
  libxcb-keysyms1 libxcb-render-util0 libxcb-xinerama0
```

这些是系统运行库，需要管理员权限；它们不会随 `pip install PySide6` 一起安装。
尤其不能漏掉 `libxcb-cursor0`，否则可能出现 `Could not load the Qt platform plugin "xcb"`。
系统库要求参见 [Qt 官方 Linux 依赖说明](https://doc.qt.io/qt-6/linux-requirements.html)。
若中文显示为方框，可另外安装字体：`sudo apt install fonts-noto-cjk`。

### 3. 创建独立 Python 环境并安装依赖

首次安装时使用系统 Python 创建项目内的 `.venv`：

```bash
MULTIVIEW_PYTHON=/usr/bin/python3 bash setup.sh
```

`setup.sh` 自动创建 `.venv` 并安装 `requirements.txt`。如果该环境已经存在，脚本会复用它。
不要从其他机器复制 `.venv`；应在目标机器重新创建。
如果使用自行安装的 Python 3.12，将上面的 `/usr/bin/python3` 替换为其实际路径。

安装后检查：

```bash
.venv/bin/python -c "import av, PySide6, PIL, numpy, mcap; print('Python 依赖就绪')"
bash run.sh --help
```

也可以使用已有 Conda 环境，替代上述 `.venv` 安装步骤：

```bash
conda activate YOUR_ENV
python -m pip install -r requirements.txt
MULTIVIEW_PYTHON="$CONDA_PREFIX/bin/python" bash run.sh
```

Conda 方式同样需要第 2 步的桌面系统库。

### 4. 启动与加载数据

打开窗口后选择数据目录：

```bash
bash run.sh
```

或启动时传入 episode 目录（将示例路径替换为实际数据路径）：

```bash
bash run.sh /path/to/episode
```

默认目录结构：

```text
episode/
├── ego/
│   └── rgb.mp4
├── cam_left.mkv
├── cam_right.mkv
└── 其他可选视频.mkv
```

文件名不同可以修改 `views.json`，或通过 `--config` 指定配置。
成功加载后显示三路画面、背包软件／数据版本／硬件版本，以及 Ego 软件／SDK／数据版本。
左侧显示当前时间对齐模式和结果，点击“播放”开始联动。
启动脚本会自动使用项目内的 `.venv`，无需手动激活；它也保留了原开发环境的查找路径作为回退。

### 5. 可选：安装到其他目录

建议先确定最终目录，再创建虚拟环境。复制工具时指定目标路径：

```bash
bash install.sh "$HOME/tool/multiviewer"
cd "$HOME/tool/multiviewer"
MULTIVIEW_PYTHON=/usr/bin/python3 bash setup.sh
bash run.sh
```

`install.sh` 复制代码、配置、说明、测试和已有的预览截图，不复制 `.venv`。
目标目录已存在时会停止，避免覆盖文件。
原开发者可省略目标参数，默认安装到 `/home/dm/tool/multiviewer/`；其他开发者应显式传入目标目录。

### 6. 打包发送给其他开发者

在源码目录的上一级执行，包中保留依赖清单和安装脚本，排除本机虚拟环境及缓存：

```bash
tar --exclude='multiviewer/.venv' \
    --exclude='__pycache__' --exclude='*.pyc' --exclude='core*' \
    -czf multiviewer.tar.gz multiviewer
```

接收者执行 `tar -xzf multiviewer.tar.gz`，然后按本节步骤安装。
视频数据不包含在工具源码中，需要另行准备。

## 窗口操作

| 操作 | 行为 |
| --- | --- |
| 输入目录 / 选择目录 → 加载 | 默认打开三路视频，并递归扫描其他支持的视频文件 |
| 勾选 / 取消勾选 | 加入视角并跳至当前时间；取消后停止该路后台读取 |
| 添加视频 | 从任意本地目录加入额外视频 |
| 播放 / 暂停、空格 | 统一控制当前勾选的视频 |
| 上一帧 / 下一帧、← / → | 暂停后按参考视频真实帧时间逐帧移动 |
| 点击或拖动进度条 | 松开时统一跳转；原来在播放则继续播放 |
| 倍速 | 支持 0.25、0.5、1.0、1.5、2.0 倍 |
| 双击画面 | 放大当前视角，再次双击或按 Esc 恢复网格 |
| 时间偏移 | 输入毫秒值并按回车，或使用上下箭头调整 |
| 保存视角配置 | 保存名称、路径、勾选状态、对齐模式和手动时间偏移到所选 JSON 文件 |

输入框内的空格、左右箭头用于正常编辑，不触发播放器快捷键。
默认三路采用上方 Ego、下方左右相机的布局，其他组合自动排为网格。
画面保持宽高比；Ego 原始视频中的左右拼接内容会完整显示。

## 时间轴与同步

左侧“时间对齐”提供三种设置：

- **自动对齐（仅 v2.1.14）**：默认设置。读取 episode 根目录 `metadata.json` 的
  `software_version`，只接受 `2.1.14` / `v2.1.14` 及其 `~release` 等后缀。
  v2.1.10、v2.1.11、v2.1.15 等其他版本不自动对齐，全部视角偏移为 0。
- **首帧对齐**：忽略配置中的偏移，各路第一帧对应全局 0 秒。
- **手动偏移**：使用配置或每路输入框中的偏移。直接编辑偏移框会切换到手动模式。

自动模式仅处理 `cam_left.mkv`、`cam_right.mkv`、`ego/rgb.mp4`，其他视角不自动对齐。
版本信息固定显示在目录栏下方；字段缺失显示“未知”。软件版本决定是否启用自动对齐，
`data_version` 只作展示，不用它替代软件版本判断。

自动对齐的数据来源：

1. 左右主摄：根 metadata 的 `video_details[].start_offset_us` 加首个可解码帧的实际 PTS。
2. Ego：确认自身 metadata 中的 `time_sync_mapping.output_timestamp_domain=unix_ns`
   和曝光参考标记，读取 `ego/sensor.mcap` 的 `rgb_metainfo.mid_exposure_utc_ns`。
   **不写死 5 ms 修正**，也不把 `ego_sync.json` 的协议时钟偏差再次加到视频上。
3. 取三路最早首帧为全局零点，计算各自偏移，替换已有偏移，避免重复补偿。

播放器保留原始 PTS，用固定偏移实现回放。加载时检查 Ego 元信息的逐帧索引、视频帧数、
PTS 和曝光时间：元信息必须完整匹配，`mid_exposure_utc_ns − PTS_ns` 的跨度不得超过
0.1 ms。若时轴需要动态修正、缺少 MCAP/起点或验证失败，则三路一同退回首帧播放，
侧边栏明确显示原因，不会只对齐其中两路。Ego 的 metadata 起点也必须与声明的曝光参考一致。
这项校验保证固定偏移适用于该文件，不是相机之间光学曝光精度的证明。

0049 实测自动偏移：左摄 **0 ms**，右摄 **853.168 ms**，Ego **1163.863 ms**。
全局时间约 1.164 秒后，三路才都有有效画面。无需另外生成或传入该 episode 的专用配置：

```bash
./run.sh /path/to/episode_20261016_0049_...
```

偏移规则为：`视频内部时间 = 全局时间 - 偏移毫秒 / 1000`。
例如左相机填 `500`，表示左相机第一帧对应全局 `0.5 秒`。
在开始前显示首帧并标记“未开始”；结束后保留末帧并标记“已结束”。
时间轴范围覆盖所有已勾选视角，取消最长视角时范围会相应缩短。

实际选帧仍使用解码帧 PTS，选择不晚于目标时刻的最新帧，不按 `帧号/fps` 假设均匀间隔。
逐帧优先以已勾选且可读取的 `ego/rgb.mp4` 为参考，否则使用第一个可用视角。

“保存视角配置…”同时保存模式，例如：

```json
{
  "time_alignment": "auto_v2_1_14",
  "views": [
    {"name": "Ego", "path": "ego/rgb.mp4", "enabled": true, "offset_ms": 0},
    {"name": "左相机", "path": "cam_left.mkv", "enabled": true, "offset_ms": 0},
    {"name": "右相机", "path": "cam_right.mkv", "enabled": true, "offset_ms": 0}
  ]
}
```

`time_alignment` 可选 `auto_v2_1_14`、`first_frame`、`manual`，缺省为自动模式。
自动模式保存的偏移为 0，加载新目录时重新计算，防止复用前一个 episode 的值。
如需继续使用旧配置中的固定偏移，请在文件中增加 `"time_alignment": "manual"`，
或在界面选择“手动偏移”。修改模式后通过“保存视角配置…”保存，下次用 `--config` 加载。
`--check` 使用相同的对齐逻辑，并在输出中列出版本、对齐状态和各路偏移。

## 扩展视角

`views.json` 定义固定视角，路径相对于本次加载的 episode 文件夹，也支持绝对路径。
在列表中补充条目即可增加固定视角：

```json
{
  "name": "辅助左相机",
  "path": "stereo_left.mkv",
  "enabled": false,
  "offset_ms": 0
}
```

其余 `.mp4`、`.mkv`、`.avi`、`.mov`、`.webm`、`.m4v`、`.mpg`、`.mpeg`
文件会自动加入列表，默认不勾选。缺失的默认文件会保留条目并提示错误。
保存自定义配置后，下次可指定：

```bash
./run.sh /path/to/episode --config /path/to/custom_views.json
```

## 验证与排查

不打开窗口检查默认视频的解码、缩放和时间跳转：

```bash
./run.sh /path/to/episode --check
```

自动测试包含变帧率、非零起始 PTS、前后逐帧、EOF 后回退、后台任务替换、
窗口渲染、播放暂停、进度条、增减视角、偏移、配置保存和缺失文件。

```bash
cd /path/to/multiviewer
QT_QPA_PLATFORM=offscreen .venv/bin/python \
  -m unittest discover -s tests -v
```

真实数据的离屏窗口检查与截图：

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python \
  tests/check_episode.py /path/to/episode --screenshot preview.png
```

本组数据已验证三路显示、跳转、逐帧、播放暂停、增加第四视角和结束状态。
`preview.png` 是使用实际视频生成的窗口截图。离屏检查不能代替桌面显示驱动测试。
正常使用请在 Ubuntu 桌面终端启动，不要设置 `QT_QPA_PLATFORM=offscreen`。

以上测试命令使用 `.venv`；如果采用 Conda 安装，将 `.venv/bin/python` 替换为该环境的 `python`。
`tests/check_episode.py` 的真实数据检查还会勾选 `stereo_left.mkv`，运行该检查需要准备此额外视频。

### 常见安装问题

| 现象 | 处理方法 |
| --- | --- |
| 提示 `libxcb-cursor.so.0` 缺失 | 执行 `sudo apt install libxcb-cursor0` 后重启工具 |
| 仍然提示 xcb 插件无法加载 | 确认第 2 步依赖已安装；运行下面的诊断命令查看缺失的库 |
| `No module named venv` 或 `ensurepip is not available` | 系统 Python 安装 `python3-venv`；自定义版本需安装与解释器对应的 venv 组件 |
| 提示缺少 PySide6、av 等 Python 包 | 在实际使用的解释器中执行 `python -m pip install -r requirements.txt` |
| `.venv/bin/python` 失效或指向其他机器路径 | 将旧 `.venv` 移出项目目录，重新执行第 3 步 |
| `could not connect to display` 或未检测到桌面 | 在 Ubuntu 桌面会话的终端启动；无桌面服务器可使用 `--check` 检查视频 |
| 程序运行但没有可见窗口 | 检查是否设置了 `QT_QPA_PLATFORM=offscreen`，正常桌面使用前执行 `unset QT_QPA_PLATFORM` |

查看当前 Python 所带的 xcb 插件及其动态库依赖：

```bash
.venv/bin/python - <<'PY'
import subprocess
from pathlib import Path
from PySide6.QtCore import QLibraryInfo
plugin = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)) / 'platforms/libqxcb.so'
print(plugin, flush=True)
subprocess.run(['ldd', str(plugin)], check=True)
PY
```

输出中的 `not found` 表示缺少动态库。程序也会在创建 X11 窗口前检查插件依赖并提示；
该检查不影响 `--help`、`--check` 或离屏测试。

每路仅保留有限解码帧和一个待处理请求；使用后台线程、软件 HEVC 解码。
性能不足时播放会跳过预览帧以追随统一时间轴，暂停与逐帧可用于细查。
默认不播放音频。数值深度或专业传感器视频仅做普通图像预览，不做专用色阶映射。

## 文件结构

- `viewer.py`：命令行入口及无窗口检查。
- `window.py`：PySide6 窗口、视角布局和联动播放。
- `video.py`：视角发现、时间戳读取、精确跳转和后台线程。
- `alignment.py`：版本信息、v2.1.14 时间对齐与 Ego 曝光元信息校验。
- `views.json`：默认视角配置。
- `run.sh`、`setup.sh`、`install.sh`：启动、依赖安装、目标目录安装。
- `tests/`：自动测试和真实数据窗口检查。
