# MeshSemantics

![image](doc/demo.png)

[中文说明](./README_zh.md)

MeshSemantics is a desktop application for interactive mesh annotation. It combines semantic face labeling and landmark editing in one workflow, so you can inspect triangle meshes, annotate regions, place named landmarks, and export results for downstream analysis or dataset production.

The app is built with `Python + PyQt6 + vedo + VTK` and is currently oriented toward batch-processing folders of `STL` / `VTP` meshes with persistent task progress.

## Features

- Open a single mesh file or scan a whole project folder
- Work with `STL` and `VTP` triangle meshes in an interactive 3D viewport
- Annotate semantic regions by:
  - right-click single-face picking
  - spline-based surface selection with preview
- Edit label colors, add new labels, remap labels, and delete labels
- Optional overwrite mode when assigning labels onto already labeled faces
- Undo and redo both label edits and landmark edits
- Maintain landmark lists per mesh:
  - add, rename, select, and delete landmarks
  - pick landmark positions directly on the mesh
  - double click the mesh in landmark mode to create a named point
  - auto-load `*.landmarks.json` beside the current mesh when available
- Save outputs as:
  - labeled `VTP`
  - label `JSON`
  - landmark `JSON`
  - per-label split `STL`
- Track project task states with filtering and next-item navigation
- Remove missing or unwanted entries from the task list, or delete local files from disk
- Remember the last opened folder and last visited file in a project

## Supported Files

- Input meshes:
  - `*.stl`
  - `*.vtp`
- Output files:
  - `*.vtp` for labeled meshes
  - `*.json` for label arrays
  - `*.landmarks.json` for landmarks
  - per-label `*.stl` exports

### Label JSON

The label JSON export stores:

- `cell_count`
- `labels`

### Landmark JSON

The landmark JSON export stores:

- `landmark_count`
- `landmarks`
  - `name`
  - `coordinates`

Landmarks without a picked position are saved with `coordinates: null`.

## Main Interface

- Center: interactive 3D mesh viewport
- Left panel:
  - project file list
  - search box
  - task status filter
  - next incomplete model button
- Right dock:
  - `Labels` tab for semantic labeling
  - `Landmarks` tab for landmark management
- Top toolbar:
  - `Open File`
  - `Open Folder`
  - `Import Segment`
  - `Save As`
  - `Clear Selection`
- Floating viewport actions:
  - quick save
  - completed toggle
- Status bar: mode changes, loading progress, save feedback, and interaction hints

## Labeling Workflow

### 1. Single-face Picking

In the `Labels` panel, right click a triangle to toggle it in the current selection.

- Unselected faces become selected
- Selected faces become deselected
- Press `E` to apply the active label to the previewed selection
- Double click a labeled face to load its label into the active label selector

### 2. Spline Surface Selection

Press `S` in the `Labels` panel to enter spline mode.

In spline mode:

- Left click adds control points on the mesh surface
- Left click near the first control point closes the contour
- Left click on the preview curve inserts a control point
- `Enter` builds the surface selection preview
- `E` applies the preview to the current label
- `C` clears the current preview
- `Delete` or `Backspace` removes the highlighted spline control point

The spline selection pipeline is:

1. Pick control points on the mesh surface.
2. Build a spline through those points.
3. Snap spline samples back to the surface.
4. Use the closed surface loop to clip the mesh.
5. Use the largest connected clipped region as the preview selection.

## Landmark Workflow

Switch to the `Landmarks` tab to manage named points.

- Type a name and press `Enter` or click `Add` to create a landmark
- If the name already exists, the existing landmark is selected instead of duplicated
- Double click a landmark row to make it active
- Click `Pick On Mesh` and then left click the mesh to place the active landmark
- In landmark mode, double click the mesh to create a new landmark directly at that position
- If the name entered from the double-click dialog already exists, you can overwrite the old point or create a copy
- Use `Rename`, `Delete`, and `Import JSON` from the landmark panel as needed

## Project Task Tracking

When you open a folder, MeshSemantics scans supported meshes and builds a task list.

- Task states include:
  - `Unlabeled`
  - `In Progress`
  - `Completed`
  - `Failed`
- The list supports search and status filtering
- `Next Model` jumps to the next incomplete task
- Completed status is persisted per project folder
- If a task file is deleted locally, the app can fall back to the original source mesh or remove the entry from the list

Project status is stored in the folder so you can resume work later.

## Shortcuts

Shortcuts depend on the active right-side panel.

### Global

| Key | Action |
| --- | --- |
| `B` | Open previous model |
| `N` | Open next model |
| `Ctrl+Z` | Undo |
| `Ctrl+Y` | Redo |

### Labels Panel

| Key | Action |
| --- | --- |
| `Ctrl+S` | Quick save current mesh as `VTP` |
| `Ctrl+Shift+S` | Save current labels as `VTP` / `JSON` / `STL` |
| `S` | Enter spline mode |
| `Enter` | Build spline preview |
| `E` | Apply preview to current label |
| `C` | Clear current preview |
| `M` | Toggle completed status |
| `Delete` / `Backspace` | Delete highlighted spline control point |

### Landmarks Panel

| Key | Action |
| --- | --- |
| `Enter` | Add landmark from the name input |
| `Ctrl+S` | Quick save landmarks as `JSON` |
| `Ctrl+Shift+S` | Export landmarks as `JSON` |
| `Delete` / `Backspace` | Delete the active landmark |

When switching from `Labels` to `Landmarks`, any active spline preview is cleared so panel-specific interactions do not conflict.

## Typical Workflow

1. Open a single mesh or scan a folder.
2. Use the left panel to choose the current task.
3. In `Labels`, assign triangle labels with right click or spline selection.
4. Quick save to `VTP` as needed.
5. In `Landmarks`, create named points and pick them on the mesh.
6. Export landmarks to `*.landmarks.json` when needed.
7. Export label JSON or split STL files if your downstream pipeline requires them.
8. Mark the task as completed and move to the next model.

## Running the App

From the repository root:

```bash
python main.py
```

## Environment

Recommended Python version:

- Python 3.10+

Install dependencies with:

```bash
pip install -r requirements.txt
```

Current requirements:

- `numpy>=1.24`
- `PyQt6>=6.4`
- `vedo>=2023.5`
- `vtk>=9.2`

## Project Structure

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

## Notes

- `STL` files are treated as unlabeled meshes on load.
- `VTP` files reuse their `Label` cell data when present.
- The application favors practical annotation workflow over CAD-grade boundary editing.

## License

See [LICENSE](LICENSE).
