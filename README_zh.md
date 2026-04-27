# MeshSemantics

![MeshSemantics 标注界面](doc/label.png)

[English README](./README.md)

MeshSemantics 是一个用于交互式三角网格标注、Landmark 编辑、项目进度跟踪和轻量级网格检查的桌面软件。它面向实际的 `STL` / `VTP` 工作流，适合在一个界面里完成模型打开、区域标注、特征点放置、质量检查和结果导出。

软件基于 `Python + PyQt6 + vedo + VTK` 开发，当前主要面向 Windows 桌面环境。

## 功能概览

- 支持打开单个模型，或扫描整个文件夹生成任务队列
- 在交互式三维视图中查看和标注 `STL` / `VTP` 网格
- 支持两种面片标注方式：
  - 右键单面片点选
  - 基于样条闭环的曲面区域选择与预览
- 支持新增、删除、改色和重映射标签
- 支持覆盖模式，可重写已有标签区域
- 标签编辑、Landmark 编辑和 Mesh Cleanup 都支持撤销 / 重做
- 支持创建、重命名、删除、导入、导出和拾取命名 Landmark
- 自动加载当前模型同名的 `*.landmarks.json`
- 支持通过 `Import Segment` 导入标签 JSON
- 内置 `Mesh Check`，可检查：
  - 非流形边
  - 自相交
  - 小连通域
  - 小孔洞
- 支持低风险清理步骤，如合并重复点、移除小连通域和填补小孔
- 支持导出带标签的 `VTP`、标签 `JSON`、Landmark `*.landmarks.json` 和按标签拆分的 `STL`
- 支持项目任务状态管理，并快速跳转到下一个未完成模型

## 界面截图

### Labels

![标签面板](doc/label.png)

### Landmarks

![Landmark 面板](doc/landmark.png)

### Mesh Check

![Mesh Check 面板](doc/meshdoctor.png)

## 典型流程

1. 用 `Open File` 打开单个模型，或用 `Open Folder` 扫描整个项目文件夹。
2. 在左侧任务队列中选择当前模型。
3. 在 `Labels` 面板中完成区域标注。
4. 用快速保存把当前结果存成 `VTP`。
5. 在 `Landmarks` 面板中新增或导入特征点。
6. 如果怀疑模型存在拓扑问题，在 `Mesh Check` 中执行分析。
7. 按需导出 `VTP`、标签 `JSON`、Landmark `JSON` 或拆分后的 `STL`。
8. 将当前任务标记为完成，切换到下一个未完成模型。

## 主要面板

### Labels

- 右键单击面片，将其加入或移出当前选择
- 按 `E` 将预览区域应用到当前标签
- 双击已标注面片，可把该面片的标签读入当前标签选择器
- 按 `S` 进入样条模式
- 按 `Enter` 生成样条预览，按 `C` 清除预览
- 开启覆盖模式后，可以直接覆盖已有标签区域

### 标签管理

- 新建标签 ID
- 删除标签 ID
- 修改标签颜色
- 将一个标签 ID 重映射到另一个标签 ID
- 跨会话保留颜色映射配置

### Landmarks

- 新增 Landmark 名称
- 重命名或删除已有 Landmark
- 在 Landmark 模式下双击模型，可直接在点击位置创建一个 Landmark
- 点击 `Pick On Mesh` 可为当前选中的 Landmark 拾取模型表面坐标
- 支持导入和导出 Landmark JSON
- 如果名称重复，软件会优先复用或更新已有 Landmark，而不是静默重复创建

### Mesh Check

`Mesh Check` 是内置在主界面里的轻量级检查和修复流程。

- 可分析当前网格中的：
  - 非流形边
  - 自相交
  - 小连通域
  - 小孔洞
- 可在三维视图中高亮受影响面片
- 右侧面板会显示问题计数和文字报告
- `Safe Cleanup` 支持：
  - 合并重复点
  - 移除小连通域
  - 填补小孔
  - 可选仅保留最大连通域
  - 重算法线

如果清理后网格面片数量发生变化，已有标签会被重置，同时 Landmark 和撤销历史也会清空，以保证修复后的网格与导出结果保持一致。

### 项目任务队列

- 扫描文件夹生成项目数据集
- 显示 `Unlabeled`、`In Progress`、`Completed`、`Failed` 四种状态
- 支持按名称或状态过滤
- 支持跳转到上一个模型或下一个未完成模型
- 自动持久化每个项目文件夹的处理进度
- 当生成的 `VTP` 不存在时，尽量回退到原始源模型继续打开

## 文件格式

### 输入

- `*.stl`
- `*.vtp`

### 输出

- 带标签的 `*.vtp`
- 标签数组 `*.json`
- Landmark `*.landmarks.json`
- 按标签拆分的 `*.stl`

### JSON 结构

标签 JSON：

- `cell_count`
- `labels`

Landmark JSON：

- `landmark_count`
- `landmarks`
- 每个 Landmark 包含 `name` 和 `coordinates`

尚未放置位置的 Landmark 会保存为 `coordinates: null`。

## 工具栏与交互

顶部工具栏：

- `Open File`
- `Open Folder`
- `Import Segment`
- `Save As`
- `Clear Selection`

三维视图中的悬浮操作：

- 上一个模型
- 下一个模型
- 快速保存
- 完成状态切换

拖放支持：

- 可直接把 `.stl` 或 `.vtp` 拖入软件打开
- 在已经打开模型的前提下，也可以把标签 JSON 直接拖入软件导入

## 快捷键

快捷键会随右侧当前激活的面板而变化。

### 全局

| 按键 | 功能 |
| --- | --- |
| `B` | 打开上一个模型 |
| `N` | 打开下一个未完成模型 |
| `Ctrl+Z` | 撤销 |
| `Ctrl+Y` | 重做 |

### Labels

| 按键 | 功能 |
| --- | --- |
| `Ctrl+S` | 快速保存当前网格为 `VTP` |
| `Ctrl+Shift+S` | 导出当前结果为 `VTP` / `JSON` / `STL` |
| `S` | 进入样条模式 |
| `Enter` | 生成样条预览 |
| `E` | 将预览应用到当前标签 |
| `C` | 清除预览 |
| `M` | 切换完成状态 |
| `Delete` / `Backspace` | 删除高亮的样条控制点 |

### Landmarks

| 按键 | 功能 |
| --- | --- |
| `Enter` | 使用当前输入名称新增 Landmark |
| `Ctrl+S` | 快速保存为 `*.landmarks.json` |
| `Ctrl+Shift+S` | 导出 Landmark JSON |
| `M` | 切换完成状态 |
| `Delete` / `Backspace` | 删除当前 Landmark |

### Mesh Check

| 按键 | 功能 |
| --- | --- |
| `Ctrl+S` | 快速保存当前网格为 `VTP` |
| `Ctrl+Shift+S` | 导出当前结果为 `VTP` / `JSON` / `STL` |
| `R` | 执行分析 |
| `Ctrl+R` | 执行安全清理 |

## 从源码运行

```bash
python -m pip install -r requirements.txt
python main.py
```

当前验证过的直接依赖版本：

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

- 加载 `STL` 时，会把它视为未标注网格。
- 加载 `VTP` 时，如果存在 `Label` 单元数据，会直接复用。
- 选择 `STL` 导出时，软件会按标签把多个 `STL` 文件输出到目标文件夹。
- `Import Segment` 要求导入的标签 JSON 与当前模型的 `cell_count` 一致。
- `Safe Cleanup` 适合低风险修复，不以 CAD 式精细边界编辑为目标。

## 许可证

见 [LICENSE](LICENSE)。
