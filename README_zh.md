# MeshSemantics

![image](doc/demo.png)

[English README](./README.md)

MeshSemantics 是一个用于三角网格交互式标注的桌面应用。它把语义面片标注和 landmark 特征点编辑整合到同一套工作流里，适合在三维网格上完成区域标注、命名特征点拾取，以及后续结果导出。

软件基于 `Python + PyQt6 + vedo + VTK` 开发，目前主要面向 `STL` / `VTP` 网格文件的批量处理，并支持项目级进度管理。

## 功能概览

- 打开单个网格文件，或扫描整个项目文件夹
- 在交互式三维视图中查看 `STL` 和 `VTP` 三角网格
- 通过以下方式完成语义区域标注:
  - 右键单面片点选
  - 基于样条曲线的曲面区域选择与预览
- 支持标签颜色编辑、新增标签、标签重映射和删除标签
- 支持覆盖模式，可直接改写已标注区域
- 标签编辑和 landmark 编辑都支持撤销 / 重做
- 为每个模型维护 landmark 列表:
  - 新增、重命名、选择、删除 landmark
  - 在网格表面直接拾取 landmark 坐标
  - 在 landmark 模式下双击模型，直接创建带名字的新点
  - 自动加载当前模型旁边的 `*.landmarks.json`
- 支持导出:
  - 带标签的 `VTP`
  - 标签 `JSON`
  - landmark `JSON`
  - 按标签拆分的 `STL`
- 支持项目任务状态管理、状态筛选和顺序跳转
- 可从任务列表移除无效条目，或直接删除本地文件
- 自动记住上次打开的文件夹和上次访问的文件

## 支持的文件格式

- 输入网格:
  - `*.stl`
  - `*.vtp`
- 输出文件:
  - `*.vtp`: 带标签的网格
  - `*.json`: 标签数组
  - `*.landmarks.json`: 特征点
  - 按标签拆分的 `*.stl`

### 标签 JSON

标签 JSON 中包含:

- `cell_count`
- `labels`

### Landmark JSON

landmark JSON 中包含:

- `landmark_count`
- `landmarks`
  - `name`
  - `coordinates`

如果某个 landmark 还没有拾取位置，会以 `coordinates: null` 保存。

## 界面组成

- 中央区域: 交互式三维网格视图
- 左侧面板:
  - 项目文件列表
  - 搜索框
  - 状态筛选
  - 下一个未完成模型按钮
- 右侧停靠面板:
  - `Labels` 标签页用于语义标注
  - `Landmarks` 标签页用于特征点管理
- 顶部工具栏:
  - `Open File`
  - `Open Folder`
  - `Import Segment`
  - `Save As`
  - `Clear Selection`
- 视图浮动操作:
  - 快速保存
  - 完成状态切换
- 底部状态栏: 显示模式切换、加载进度、保存反馈和交互提示

## 标签标注工作流

### 1. 单面片点选

在 `Labels` 面板中，右键点击三角面片可以切换它的选中状态。

- 未选中的面片会加入当前选择
- 已选中的面片会取消选择
- 按 `E` 把当前预览区域应用到活动标签
- 双击已标注的面片，可把该面片的标签读入当前标签选择器

### 2. 样条曲面选择

在 `Labels` 面板中按 `S` 进入样条模式。

样条模式下:

- 左键单击: 在模型表面添加控制点
- 在第一个控制点附近左键单击: 闭合轮廓
- 左键单击预览曲线: 在曲线上插入新的控制点
- `Enter`: 生成曲面预览区域
- `E`: 把预览区域应用到当前标签
- `C`: 清除当前预览
- `Delete` / `Backspace`: 删除高亮的控制点

当前样条选择的处理流程为:

1. 在模型表面拾取控制点。
2. 用控制点生成样条曲线。
3. 将样条采样点重新吸附到网格表面。
4. 使用闭合曲面轮廓裁剪网格。
5. 取裁剪后最大的连通区域作为预览选择结果。

## Landmark 工作流

切换到 `Landmarks` 标签页后，可以管理带名字的特征点。

- 在输入框中填写名称后按 `Enter`，或点击 `Add` 新增 landmark
- 如果名称已存在，软件会直接选中已有 landmark，而不是重复创建
- 双击表格行可将该 landmark 设为当前活动点
- 点击 `Pick On Mesh` 后，在模型上左键单击即可为当前 landmark 赋坐标
- 在 landmark 模式下双击模型，可以直接在该位置创建新的 landmark
- 如果双击创建时输入的名称已存在，可选择覆盖旧点，或自动创建副本名称
- 可使用 `Rename`、`Delete`、`Import JSON` 管理 landmark

## 项目任务管理

打开文件夹后，MeshSemantics 会扫描其中的支持格式网格，并建立任务列表。

- 任务状态包括:
  - `Unlabeled`
  - `In Progress`
  - `Completed`
  - `Failed`
- 列表支持搜索和按状态筛选
- `Next Model` 可跳转到下一个未完成任务
- 完成状态会按项目文件夹持久化保存
- 如果任务文件已在本地删除，软件可以回退到原始源文件，或将该条目从列表中移除

项目状态会保存在项目目录中，方便下次继续处理。

## 快捷键

快捷键会根据右侧当前激活的面板切换。

### 全局

| 按键 | 功能 |
| --- | --- |
| `B` | 打开上一个模型 |
| `N` | 打开下一个模型 |
| `Ctrl+Z` | 撤销 |
| `Ctrl+Y` | 重做 |

### Labels 面板

| 按键 | 功能 |
| --- | --- |
| `Ctrl+S` | 快速保存当前网格为 `VTP` |
| `Ctrl+Shift+S` | 另存当前标签结果为 `VTP` / `JSON` / `STL` |
| `S` | 进入样条模式 |
| `Enter` | 生成样条预览 |
| `E` | 将预览应用到当前标签 |
| `C` | 清除当前预览 |
| `M` | 切换任务完成状态 |
| `Delete` / `Backspace` | 删除高亮样条控制点 |

### Landmarks 面板

| 按键 | 功能 |
| --- | --- |
| `Enter` | 根据输入框内容新增 landmark |
| `Ctrl+S` | 快速保存 landmarks 为 `JSON` |
| `Ctrl+Shift+S` | 导出 landmarks 为 `JSON` |
| `Delete` / `Backspace` | 删除当前活动 landmark |

从 `Labels` 切换到 `Landmarks` 时，当前样条预览会自动清空，避免两个面板的交互冲突。

## 典型使用流程

1. 打开单个模型，或扫描一个项目文件夹。
2. 在左侧列表中选择当前任务。
3. 在 `Labels` 中通过右键点选或样条选择完成语义标注。
4. 需要时快速保存为 `VTP`。
5. 在 `Landmarks` 中创建带名字的特征点，并在模型上拾取位置。
6. 需要时导出 `*.landmarks.json`。
7. 如果下游流程需要，可继续导出标签 `JSON` 或按标签拆分的 `STL`。
8. 标记任务完成，然后切换到下一个模型。

## 运行方式

当前项目以 Conda 环境 `meshlabeler` 作为验证环境。

推荐运行步骤:

```bash
conda activate meshlabeler
python -m pip install -r requirements.txt
python main.py
```

当前验证通过的解释器版本:

- Python `3.10.20`

`meshlabeler` 环境中验证过的直接依赖:

- `numpy==2.2.6`
- `PyQt6==6.11.0`
- `vedo==2026.6.1`
- `vtk==9.6.1`

如果需要从零创建环境，建议保持上述 Python 版本和四个核心依赖的兼容组合。

## 项目结构

```text
MeshSemantics/
|-- main.py
|-- meshsemantics/
|   |-- app.py
|   |-- config/
|   |-- core/
|   |-- ui/
|   `-- assets/
|-- doc/
|-- data/
|-- README.md
`-- README_zh.md
```

## 说明

- 加载 `STL` 时，会将其视为未标注网格。
- 加载 `VTP` 时，如果存在 `Label` 单元数据，会直接复用。
- 当前软件更偏向高效实用的标注流程，而不是 CAD 级别的精确边界编辑器。

## 许可证

见 [LICENSE](LICENSE)。
