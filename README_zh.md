# MeshSemantics

![MeshSemantics 标注界面](doc/label.png)

[English README](./README.md)

MeshSemantics 是一个用于交互式三角网格标注与检查的桌面软件。它把语义面片标注、landmark 特征点编辑、项目进度管理以及网格质量检查整合到同一套流程里，方便你直接从原始网格走到可导出的标注结果，而不用在多个工具之间来回切换。

软件基于 `Python + PyQt6 + vedo + VTK` 开发，面向日常的 `STL` / `VTP` 网格处理与批量项目标注工作。

## 功能亮点

- 支持打开单个模型，或扫描整个项目文件夹
- 在交互式三维视图中查看和标注 `STL` / `VTP` 三角网格
- 支持两种面片标注方式：
  - 右键单面片点选
  - 基于样条闭环的曲面区域选择与预览
- 支持新增、删除、改色、重映射标签
- 支持覆盖模式，可直接改写已标注区域
- 标签编辑和 landmark 编辑都支持撤销 / 重做
- 支持为每个模型维护命名 landmark，并直接在模型表面拾取位置
- 可自动加载当前模型旁边已有的 `*.landmarks.json`
- 内置 `Mesh Check` 手动检查流程，可分析常见拓扑问题
- 支持安全清理，如合并重复点、填补小孔、移除小连通域
- 支持导出带标签 `VTP`、标签 `JSON`、landmark `JSON`、按标签拆分的 `STL`
- 支持项目任务状态管理，并快速跳转到下一个未完成模型

## 界面截图

### 标签标注

![标签面板](doc/label.png)

### Landmark 编辑

![Landmark 面板](doc/landmark.png)

### Mesh Check

![Mesh Check 面板](doc/meshdoctor.png)

## 主要功能

### 1. 语义标签标注

- 右键面片可加入或移出当前选择
- 按 `E` 将当前预览区域应用到活动标签
- 双击已标注面片，可把该面片的标签读入当前标签选择器
- 支持样条模式，可直接在模型表面绘制闭合轮廓
- 可在调整边界时插入或删除样条控制点
- 需要重标已有区域时可开启覆盖模式

### 2. 标签管理

- 新建标签 ID
- 删除不再需要的标签 ID
- 修改标签颜色
- 将一个标签 ID 重映射到另一个标签 ID
- 跨会话保留颜色映射配置

### 3. Landmark 编辑

- 新增、重命名、选择、删除命名 landmark
- 点击 `Pick On Mesh` 后，直接在模型表面放置当前 landmark
- 在 landmark 模式下双击模型，可在该位置直接创建 landmark
- 当名称重复时，可复用已有点、覆盖旧点或自动创建副本名称
- 支持导入 landmark JSON，并导出 `*.landmarks.json`

### 4. Mesh Check 与安全清理

`Mesh Check` 标签页为软件增加了轻量级的网格检查流程。

- 手动分析可检查：
  - 非流形边
  - 自相交
  - 小连通域
  - 小孔洞
  - 三角面数量
- 报告面板会汇总问题类型和受影响面片数量
- 安全清理可按选项执行：
  - 合并重复点
  - 删除小连通域
  - 填补小孔洞
  - 仅保留最大连通域
  - 重算法线

如果清理后导致单元数量变化，标签会被重置，同时 landmark 和撤销历史也会被清空，以保证修复后的网格与导出结果保持一致。

### 5. 项目工作流

- 扫描文件夹生成任务列表
- 按名称和状态过滤任务
- 跟踪 `Unlabeled`、`In Progress`、`Completed`、`Failed`
- 支持跳转到上一个、下一个或下一个未完成模型
- 项目进度会持久化保存，方便下次继续
- 当生成后的工作文件缺失时，可回退到原始源文件，或移除无效条目

## 支持的文件格式

### 输入

- `*.stl`
- `*.vtp`

### 输出

- `*.vtp`：带标签的网格
- `*.json`：标签数组
- `*.landmarks.json`：landmark 数据
- 按标签拆分的 `*.stl`

### JSON 结构

标签 JSON 包含：

- `cell_count`
- `labels`

Landmark JSON 包含：

- `landmark_count`
- `landmarks`
  - `name`
  - `coordinates`

尚未拾取位置的 landmark 会以 `coordinates: null` 保存。

## 界面组成

- 中央区域：交互式三维网格视图
- 左侧面板：
  - 项目文件列表
  - 搜索框
  - 状态筛选
  - 模型跳转
- 右侧停靠面板：
  - `Labels`
  - `Landmarks`
  - `Mesh Check`
- 顶部工具栏：
  - `Open File`
  - `Open Folder`
  - `Import Segment`
  - `Save As`
  - `Clear Selection`
- 视图浮动操作：
  - 上一个模型
  - 下一个模型
  - 快速保存
  - 完成状态切换

## 快捷键

快捷键会根据右侧当前激活的面板变化。

### 全局

| 按键 | 功能 |
| --- | --- |
| `B` | 打开上一个模型 |
| `N` | 打开下一个模型 |
| `Ctrl+Z` | 撤销 |
| `Ctrl+Y` | 重做 |

### Labels

| 按键 | 功能 |
| --- | --- |
| `Ctrl+S` | 快速保存当前网格为 `VTP` |
| `Ctrl+Shift+S` | 另存当前结果为 `VTP` / `JSON` / `STL` |
| `S` | 进入样条模式 |
| `Enter` | 生成样条预览 |
| `E` | 将当前预览应用到活动标签 |
| `C` | 清除当前预览 |
| `M` | 切换完成状态 |
| `Delete` / `Backspace` | 删除高亮样条控制点 |

### Landmarks

| 按键 | 功能 |
| --- | --- |
| `Enter` | 按当前输入名称新增 landmark |
| `Ctrl+S` | 快速保存 landmarks 为 `JSON` |
| `Ctrl+Shift+S` | 导出 landmarks 为 `JSON` |
| `Delete` / `Backspace` | 删除当前活动 landmark |

### Mesh Check

| 按键 | 功能 |
| --- | --- |
| `Ctrl+S` | 快速保存当前网格为 `VTP` |
| `Ctrl+Shift+S` | 另存当前结果为 `VTP` / `JSON` / `STL` |
| `R` | 执行手动分析 |
| `Ctrl+R` | 执行安全清理 |

## 典型使用流程

1. 打开单个模型，或扫描项目文件夹。
2. 在左侧列表里选择当前任务。
3. 在 `Labels` 中完成区域标注。
4. 用快速保存保存阶段性结果。
5. 在 `Landmarks` 中添加或导入特征点。
6. 如果怀疑网格质量有问题，在 `Mesh Check` 中执行分析。
7. 按需导出 `VTP`、标签 `JSON`、landmark `JSON` 或拆分后的 `STL`。
8. 将任务标记为完成，然后切换到下一个模型。

## 运行方式

推荐环境：

```bash
conda activate meshlabeler
python -m pip install -r requirements.txt
python main.py
```

当前验证通过的解释器版本：

- Python `3.10.20`

当前验证通过的直接依赖：

- `numpy==2.2.6`
- `PyQt6==6.11.0`
- `vedo==2026.6.1`
- `vtk==9.6.1`

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
- 软件重点是高效的标注与检查工作流，而不是 CAD 级的精细边界手工编辑。

## 许可证

见 [LICENSE](LICENSE)。
