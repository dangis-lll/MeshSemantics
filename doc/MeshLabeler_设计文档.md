# MeshLabeler — 三角面片模型交互式标注工具
**软件设计文档 v1.0** | 技术栈：Python / PyQt6 / vedo / VTK

---

## 目录

1. [项目概述](#1-项目概述)
2. [总体架构](#2-总体架构)
3. [模块详细设计](#3-模块详细设计)
4. [交互设计](#4-交互设计)
5. [数据流](#5-数据流)
6. [依赖与环境](#6-依赖与环境)
7. [性能设计](#7-性能设计)
8. [扩展性设计](#8-扩展性设计)
9. [开发路线图](#9-开发路线图)

---

## 1. 项目概述

### 1.1 背景与目标

MeshLabeler 是一款面向医学影像、工业检测、计算机图形学等领域的三角面片模型交互式标注工具。用户可以在三维视图中对 STL/VTP 格式的三角网格模型进行逐面片的语义标注，输出带 Label 标量场的 VTP 文件或按标签分割的多个 STL 文件，用于后续的深度学习训练、有限元分析等任务。

核心设计目标：

- **高响应**：依托 vedo/VTK 的 GPU 渲染管线，百万面片模型的旋转/缩放在普通显卡上达到 60 fps。
- **低门槛**：所有高频操作均绑定键盘快捷键，样条框选只需三步（`S` 进入 → 点击若干点 → `Enter` 确认）。
- **可扩展**：Colormap、标签数量均可在配置文件中自定义，Label 以 VTK CellData 标量存储，兼容下游工具链。

### 1.2 名词定义

| 术语 | 说明 |
|------|------|
| 面片 / Cell | 三角网格中的一个三角形单元，即 VTK 的 Cell。 |
| Label | 每个面片对应的整数类别编号，存储于 VTK CellData 中名为 `"Label"` 的 vtkIntArray。 |
| 样条框选 | 用户在三维视图上点击若干控制点，程序生成闭合样条曲线，自动计算投影范围内的面片集合。 |
| Colormap | Label 编号到 RGB 颜色的映射表，支持用户自定义 JSON 配置。 |

---

## 2. 总体架构

### 2.1 分层架构

软件采用四层架构，各层职责清晰、单向依赖：

| 层次 | 职责与主要模块 |
|------|--------------|
| **UI 层** | PyQt6 窗口、工具栏、标签面板、文件树、拖拽接收器；负责事件路由。 |
| **交互层** | vedo Interactor 自定义子类；处理鼠标点击、键盘事件、样条绘制状态机。 |
| **逻辑层** | LabelEngine：维护 Label 数组、Undo/Redo 栈、框选算法、标签交换。 |
| **数据层** | FileIO：STL/VTP 读写；PreloadManager：后台线程预加载；ColormapConfig：JSON 解析。 |

### 2.2 核心类关系

- **MainWindow**（QMainWindow）：顶层窗口，持有 FilePanel、LabelPanel、VedoWidget。
- **VedoWidget**（QWidget）：嵌入 vedo plotter 的容器，持有 MeshInteractor 和 LabelEngine。
- **MeshInteractor**（vedo.Interactor 子类）：覆写鼠标/键盘回调，内部状态机管理样条模式。
- **LabelEngine**：纯 Python 逻辑类，不依赖 Qt/vedo，便于单元测试。
- **FilePanel**（QDockWidget）：文件树 + 预加载进度条，通过信号触发 MainWindow 加载模型。
- **LabelPanel**（QDockWidget）：当前 Label 数字输入框（支持上下箭头）、颜色预览、Colormap 编辑器。
- **PreloadManager**（QThread）：后台遍历文件夹，调用 `FileIO.load()` 缓存 vtkPolyData 对象。
- **FileIO**：静态工具类，封装 vtkSTLReader / vtkXMLPolyDataReader / Writer。

---

## 3. 模块详细设计

### 3.1 主窗口布局（MainWindow）

主窗口采用 Qt DockWidget 机制，布局如下：

| 区域 | 内容 |
|------|------|
| 顶部工具栏 | 导入文件、导入文件夹、保存为 STL、保存为 VTP、标签交换、撤销（Ctrl+Z） |
| 左侧停靠面板（FilePanel） | 文件树（QTreeView）+ 搜索框 + 加载进度条；显示文件夹中所有 `.stl` / `.vtp` 文件 |
| 右侧停靠面板（LabelPanel） | 当前 Label 编号微调器、颜色块预览、Colormap 表格编辑器、标签交换选择器 |
| 中央区域（VedoWidget） | vedo 三维渲染视图；底部状态栏显示当前模式、面片数量、已标注面片数 |

**拖拽支持**：MainWindow 实现 `dragEnterEvent` 和 `dropEvent`，接受 `.stl` / `.vtp` 文件；拖入后调用与工具栏「导入」相同的加载流程。

---

### 3.2 渲染模块（VedoWidget + MeshInteractor）

#### 3.2.1 vedo 嵌入方式

使用 vedo 的 Qt 嵌入模式，通过将 `vedo.Plotter` 的 `qt_widget` 属性挂载到 QWidget 实现无缝集成：

```python
plotter = vedo.Plotter(qt_widget=self, bg='#1a1a2e')
```

渲染背景采用深色主题，以便标注颜色更清晰可辨。

#### 3.2.2 模型渲染策略

模型始终作为一个整体 `vedo.Mesh` 渲染，通过修改 CellData（`"Label"` 标量）驱动颜色变化，避免面片分组带来的渲染切换开销：

- **未标注面片**：Label = 0，映射为浅灰色（`#CCCCCC`）。
- **已标注面片**：Label = N（N ≥ 1），按 Colormap 映射为对应颜色。
- **样条框选预览中的面片**：临时置灰（`#888888`），按 `E` 确认后写入当前 Label。
- **鼠标悬停面片**：高亮描边，不改变 Label 值。

Colormap 以 `vtkLookupTable` 形式注入 VTK 渲染管线，修改颜色时仅调用 `Modified()` + 请求重绘，不重建 Actor。

#### 3.2.3 交互状态机

MeshInteractor 内部维护一个三状态机：

| 状态 | 触发进入 | 可执行操作 |
|------|---------|-----------|
| **NORMAL** | 启动 / `C` 键 / `E` 键 | 旋转/缩放/平移；右键单选面片；`S` 键进入 SPLINE |
| **SPLINE** | `S` 键 | 左键添加样条控制点；`Enter` 计算框选（→ CONFIRM）；`C` 键取消（→ NORMAL） |
| **CONFIRM** | `Enter`（在 SPLINE 中） | 框选面片以灰色预览；`E` 键标注并退出（→ NORMAL）；`C` 键清除（→ NORMAL） |

---

### 3.3 样条框选算法

样条框选分为以下三个步骤：

1. **控制点投影**：将用户在屏幕上点击的坐标序列 `[(x₁,y₁), …, (xₙ,yₙ)]` 通过 VTK 坐标变换转为世界坐标系中的射线，取射线与模型的最近交点，得到三维控制点序列。

2. **样条插值与屏幕多边形生成**：对控制点序列应用 Catmull-Rom 样条插值，生成平滑闭合曲线（200 个插值点），再将这些三维点重新投影回屏幕坐标，得到一个二维多边形 P。

3. **面片筛选**：遍历模型所有面片，计算每个面片的重心，通过 VTK 相机矩阵将其投影到屏幕坐标 `(u, v)`，用射线法（ray casting）判断 `(u, v)` 是否在多边形 P 内。满足条件的面片加入候选集合。

算法复杂度为 O(F)（F 为面片数），对于百万面片量级建议启用 NumPy 向量化加速，一次性完成所有面片重心的矩阵变换与多边形包含判断。

#### 边缘情况处理

- 控制点少于 3 个时，按 `Enter` 弹出提示，不执行框选。
- 模型背面面片（法线背向相机）可通过配置项选择是否纳入框选范围（默认排除）。
- 多次框选可叠加：每次 `E` 确认后仅覆盖当前框选的面片，其余面片 Label 不变。

---

### 3.4 标签引擎（LabelEngine）

#### 3.4.1 数据结构

LabelEngine 核心数据结构：

- `label_array`：numpy int32 数组，长度等于面片数，初始全为 0。
- `undo_stack`：`list of (操作类型, cell_ids, old_labels)`，最大深度可配置（默认 50）。
- `colormap`：dict，键为 Label 整数，值为 `(R, G, B)` 元组（0–255 范围）。

对外暴露以下接口：

| 方法签名 | 说明 |
|---------|------|
| `assign(cell_ids, label)` | 将指定面片集合标注为 label，推入 undo 栈。 |
| `undo()` | 弹出 undo 栈顶，恢复 `label_array` 中对应面片的旧值，同时推入 redo 栈。 |
| `swap_labels(a, b)` | 交换 `label_array` 中所有值为 a 与 b 的面片，作为一次原子操作推入 undo 栈。 |
| `get_vtk_array()` | 返回 vtkIntArray，可直接设置到 `polydata.GetCellData()`。 |
| `get_cells_by_label(label)` | 返回所有标注为 label 的面片索引列表，用于按标签导出 STL。 |

#### 3.4.2 Undo/Redo 机制

采用命令模式（Command Pattern）实现撤销/重做：每次 `assign` 或 `swap_labels` 调用前，将（受影响面片 ID 集合，这些面片的原始 label 值）打包为一个 `UndoRecord` 推入栈。`Ctrl+Z` 触发 `undo()`，从栈顶取出记录并恢复；`Ctrl+Y` 触发 `redo()`。Undo 栈超过上限时，移除栈底最旧的记录。

---

### 3.5 文件 I/O（FileIO）

FileIO 是无状态的静态工具类，所有方法均为类方法。

#### 3.5.1 导入

| 格式 | 读取器 | 处理说明 |
|------|--------|---------|
| STL | vtkSTLReader | 读取后检查是否已有 `"Label"` CellData；无则初始化为全 0 数组。 |
| VTP | vtkXMLPolyDataReader | 读取后提取 `"Label"` CellData（若存在），直接恢复标注状态；若无则同 STL 流程初始化。 |

#### 3.5.2 导出

| 格式 | 处理说明 |
|------|---------|
| STL（多文件） | 遍历所有非零 Label 值，用 `vtkExtractCells` 提取对应子网格，分别用 `vtkSTLWriter` 写出 `{原文件名}_{label}.stl`；Label 0（未标注）单独保存为 `{原文件名}_unlabeled.stl`（可配置是否跳过）。 |
| VTP（单文件） | 将最新 `label_array` 写入 polydata 的 CellData（`"Label"`），调用 `vtkXMLPolyDataWriter` 以 Binary 模式写出（减小文件体积）。 |

---

### 3.6 文件面板与预加载（FilePanel + PreloadManager）

#### 3.6.1 文件树

FilePanel 使用 `QFileSystemModel` 绑定 `QTreeView`，过滤只显示 `.stl` 和 `.vtp` 文件。点击文件名触发 `file_selected` 信号，MainWindow 收到信号后：

1. 检查 PreloadManager 缓存中是否已有该文件的 vtkPolyData 对象。
2. **命中缓存**：直接将 polydata 传给 VedoWidget 渲染，耗时 < 100ms。
3. **未命中**：在 UI 层显示进度动画，异步加载后渲染。

#### 3.6.2 预加载策略

PreloadManager 在 QThread 中按以下优先级预加载：

- 当前选中文件的**相邻文件**（上一个 + 下一个）优先级最高，立即加载。
- 其余文件按文件列表顺序在后台逐个加载，使用 LRU 缓存（默认容量 20 个模型）淘汰久未使用的条目。
- 每个文件加载完成后发出 `preload_done` 信号，携带文件路径，UI 更新预加载指示图标（小圆点变绿）。

**内存保护**：单个模型超过配置阈值（默认 500 MB）时不进入缓存，直接按需加载；使用 psutil 监控系统内存，超过 70% 时暂停后台预加载。

---

### 3.7 Colormap 配置

Colormap 以 JSON 文件存储，路径默认为 `~/.meshlabeler/colormap.json`，格式示例：

```json
{
  "0": [204, 204, 204],
  "1": [255,  82,  82],
  "2": [ 33, 150, 243],
  "3": [ 76, 175,  80],
  "_default": [200, 200, 0]
}
```

LabelPanel 内嵌 Colormap 编辑器（QTableWidget），可添加/删除行、通过 QColorDialog 选色，实时更新 `vtkLookupTable` 触发重绘，并提供「另存为」和「加载」按钮管理多套方案。

---

## 4. 交互设计

### 4.1 快捷键总览

| 按键 | 生效条件 | 行为 |
|------|---------|------|
| `S` | NORMAL 模式 | 进入 SPLINE 模式，状态栏显示提示 |
| `Enter` | SPLINE 模式，控制点 ≥ 3 | 计算框选面片，进入 CONFIRM 状态，预览灰色高亮 |
| `E` | CONFIRM 状态 或 右键单选后 | 将预览面片标注为当前 Label，退出到 NORMAL 模式 |
| `C` | SPLINE / CONFIRM 模式 | 清除所有控制点和预览，退出到 NORMAL 模式 |
| `Ctrl+Z` | 任意模式 | 撤销最后一次标注操作 |
| `Ctrl+Y` | 任意模式 | 重做（redo） |
| `↑` / `↓` | LabelPanel 数字框聚焦 | 当前 Label 编号 +1 / -1 |
| 右键单击 | NORMAL 模式 | 拾取单个面片进入单选预览；再按 `E` 标注 |
| 鼠标滚轮 | 任意模式 | 缩放视图 |
| 左键拖拽 | NORMAL 模式 | 旋转模型（trackball 模式） |
| 中键拖拽 | 任意模式 | 平移相机 |

### 4.2 标签选择与交换

Label 编号通过 LabelPanel 顶部的 `QSpinBox` 控件选择（最小值 1，最大值由 Colormap 条目数决定，超出时自动使用 `_default` 颜色）。支持三种方式：直接键入数字、点击上下箭头微调、键盘 `↑↓` 方向键。

标签交换：在 LabelPanel 下方设置两个下拉框分别选择「标签 A」和「标签 B」，点击「交换」按钮调用 `LabelEngine.swap_labels(a, b)`，此操作可被 `Ctrl+Z` 撤销。

### 4.3 用户反馈设计

- **状态栏（底部）**：实时显示当前模式、选中面片数量、总面片数、已标注百分比。
- **Tooltip**：工具栏按钮均有 Tooltip，显示功能说明和快捷键。
- **进度对话框**：打开大型文件（> 50 万面片）或批量导出 STL 时，弹出带取消按钮的进度对话框。
- **错误提示**：文件格式错误、读写失败等以 `QMessageBox` 弹出明确信息，不暴露技术细节。

---

## 5. 数据流

### 5.1 模型导入流程

1. 用户点击「导入」/ 拖拽文件 / 点击文件面板中的文件名。
2. MainWindow 检查 PreloadManager 缓存，命中则直接取出 vtkPolyData，未命中则调用 `FileIO.load()` 加载（大文件启用进度条）。
3. `LabelEngine.reset(polydata)` 将 `label_array` 初始化为 polydata 中现有的 `"Label"` CellData（或全 0 数组）。
4. VedoWidget 用新 polydata 重建 `vedo.Mesh`，应用当前 Colormap，调用 `plotter.show()`。
5. FilePanel 更新选中高亮；LabelPanel 刷新面片统计信息。

### 5.2 样条标注流程

1. 用户按 `S`：MeshInteractor 进入 SPLINE 状态，临时禁用 vedo 默认的旋转交互。
2. 用户左键点击 N 次：每次点击在三维视图上绘制一个小球形标记，记录屏幕坐标和世界坐标。
3. 用户按 `Enter`：调用样条插值算法，得到候选面片集合 S。状态变为 CONFIRM，S 中面片临时渲染为灰色（不修改 `label_array`）。
4. 用户按 `E`：调用 `LabelEngine.assign(S, current_label)`，更新 `label_array`，刷新 `vtkLookupTable` 颜色，删除临时标记球，恢复 NORMAL 状态。
5. 用户按 `C`（任意环节）：清除临时标记和预览，恢复 NORMAL 状态，`label_array` 不变。

### 5.3 保存流程

1. 用户点击「保存为 VTP」：`FileIO.save_vtp(polydata, label_engine.get_vtk_array(), path)`。
2. 用户点击「保存为 STL」：弹出目录选择框，`FileIO.save_stl_per_label(polydata, label_engine, dir_path)`，按标签分割后逐个写出 STL，显示进度对话框。

---

## 6. 依赖与环境

### 6.1 Python 依赖包

| 包名 | 最低版本 | 用途 |
|------|---------|------|
| PyQt6 | 6.4 | 主 UI 框架（窗口、事件、信号槽） |
| vedo | 2023.5 | 三维渲染、交互器、Qt 嵌入 |
| vtk | 9.2 | 底层数据结构（vtkPolyData、CellData 等） |
| numpy | 1.24 | 面片批量计算加速（样条、框选） |
| scipy | 1.10 | Catmull-Rom 样条插值、多边形包含检测 |
| psutil | 5.9 | 内存监控（预加载保护） |

### 6.2 目录结构

```
meshlabeler/
├── main.py                  # 程序入口，启动 QApplication + MainWindow
├── ui/
│   ├── main_window.py       # MainWindow 类
│   ├── vedo_widget.py       # VedoWidget 类
│   ├── file_panel.py        # FilePanel + PreloadManager
│   ├── label_panel.py       # LabelPanel + ColormapEditor
│   └── dialogs.py           # 进度对话框、标签交换对话框
├── core/
│   ├── interactor.py        # MeshInteractor 状态机
│   ├── label_engine.py      # LabelEngine（纯逻辑）
│   ├── spline_selector.py   # 样条框选算法
│   └── file_io.py           # FileIO 静态类
├── config/
│   ├── colormap.json        # 默认 Colormap
│   └── settings.json        # 全局设置（undo 栈深度、缓存上限等）
├── assets/
│   └── icons/               # 工具栏图标（PNG，16x16 和 32x32）
└── requirements.txt
```

### 6.3 打包与发布

使用 PyInstaller 或 Nuitka 打包为单目录可执行文件：

- **Windows**：生成 `MeshLabeler.exe`，附带 VTK/Qt DLL。
- **macOS**：生成 `MeshLabeler.app` 包，经 codesign 签名后可分发。
- **Linux**：生成 AppImage 格式，内嵌所有动态库。

---

## 7. 性能设计

### 7.1 渲染性能

- **单 Actor 渲染**：所有面片作为一个 `vtkActor` 渲染，颜色通过 `vtkLookupTable` 映射，避免多 Actor 管理开销。
- **增量颜色更新**：标注操作仅调用 `vtkLookupTable.Modified()`，不重建 Mapper，VTK 渲染管线自动识别 dirty flag 并最小化 GPU 上传量。
- **LOD 策略（可选）**：对超过 200 万面片的模型，在旋转/缩放时自动启用 `vtkQuadricDecimation` 生成的低分辨率代理模型，静止后切换回原始模型。

### 7.2 框选性能

- 100 万面片模型的全量遍历使用 NumPy 矩阵运算，典型耗时 < 500ms（i7 12th Gen），在 QThread 中执行，不阻塞 UI。
- 框选计算通过 QThread signals 将进度汇报给状态栏，用户可随时按 `C` 取消。

### 7.3 预加载性能

- PreloadManager 使用单后台线程，避免并发 VTK reader 的线程安全问题。
- LRU 缓存条目数默认 20，可在 `settings.json` 中调整；内存监控使用 psutil，超过系统内存 70% 时暂停预加载。

---

## 8. 扩展性设计

### 8.1 插件式标注模式

MeshInteractor 的标注模式设计为可扩展：每种模式实现 `on_left_click`、`on_key_press`、`on_enter`、`on_exit` 四个接口方法，注册到 MeshInteractor 的模式字典中。未来可方便地新增「矩形框选模式」「笔刷模式」「种子区域增长模式」等，而无需修改核心状态机逻辑。

### 8.2 批处理 CLI

LabelEngine 和 FileIO 不依赖 Qt，可直接在命令行脚本中调用：

```bash
python -m meshlabeler.batch --input dir/ --colormap my_colormap.json --output out/
```

用于将已有 VTP 标注文件批量转换为按标签分割的 STL。

### 8.3 配置热重载

使用 `watchdog` 库监听 `colormap.json` 和 `settings.json` 文件变化，自动热重载 Colormap 并刷新渲染，无需重启软件。

---

## 9. 开发路线图

| 里程碑 | 预期工期 | 交付物 |
|--------|---------|--------|
| M1 — 骨架 | 2 周 | 主窗口布局、STL 导入渲染、基础旋转缩放交互 |
| M2 — 标注核心 | 2 周 | 右键单选、样条框选、LabelEngine（assign + undo） |
| M3 — 文件管理 | 1 周 | VTP 导入/导出、STL 分标签导出、文件面板 + 预加载 |
| M4 — 体验打磨 | 1 周 | Colormap 编辑器、标签交换、拖拽导入、进度反馈、快捷键完善 |
| M5 — 发布 | 1 周 | 性能基准、打包为可执行文件、用户手册 |

---

*MeshLabeler 软件设计文档 — v1.0*
