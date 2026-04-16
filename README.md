# MeshSemantics

![image](doc/demo.png)

[中文说明](./README_zh.md)

MeshSemantics is a desktop tool for interactive semantic labeling of triangle meshes.

It is built with `Python + PyQt6 + vedo + VTK` and is designed for workflows where you need to open many `STL` or `VTP` meshes, mark triangle regions with labels, and export the result for later processing or dataset creation.

## What It Does

- Open single mesh files or scan a whole folder of mesh tasks
- Display large triangle meshes in an interactive 3D viewport
- Assign labels to triangles with:
  - right-click single-face picking
  - spline-based surface selection
- Preview pending selections before applying them
- Edit labels with undo/redo
- Quick-save the current task as `VTP`
- Export a labeled mesh as:
  - one `VTP` with cell label data
  - multiple `STL` files split by label
- Track task completion state inside a project folder

## Supported Files

- Input: `*.stl`, `*.vtp`
- Output:
  - `*.vtp`
  - `*.json`
  - per-label `*.stl`

## Main Interface

- Center: 3D mesh view
- Left panel: project file list
- Right panel: label panel, color map, quick save, task completion
- Top toolbar: open, save, undo, redo
- Status bar: current mode and interaction feedback

## Selection Modes

### 1. Single-face Picking

Use right click on the mesh to toggle triangle selection.

- If a face is not selected, it becomes selected
- If a face is already selected, it becomes deselected
- Press `E` to assign the current label to the selected faces

### 2. Spline Surface Selection

Press `S` to enter spline mode.

In spline mode:

- Left click adds control points on the mesh surface
- Left click near the first control point closes the contour
- Left click on the preview curve inserts a new control point
- Left drag rotates the model
- Middle drag keeps the default VTK camera interaction
- `Delete` or `Backspace` removes the highlighted control point
- `Enter` computes the surface-loop preview
- `E` applies the previewed selection
- `C` clears the current spline and exits preview/edit mode

The spline workflow uses a surface contour and surface-loop clipping approach:

1. control points are picked on the mesh surface
2. a spline is generated from those points
3. spline points are snapped back to the mesh surface
4. the closed surface loop is used to clip the mesh
5. the largest connected clipped region becomes the preview selection

## Shortcuts

| Key | Action |
| --- | --- |
| `S` | Enter spline mode |
| `Enter` | Confirm spline contour and build preview |
| `E` | Apply current preview to the active label |
| `C` | Clear current preview / exit spline workflow |
| `M` | Toggle task completion |
| `Ctrl+Z` | Undo |
| `Ctrl+Y` | Redo |
| `Delete` / `Backspace` | Delete highlighted spline control point |

## Typical Workflow

1. Open a file or folder.
2. Choose the target label in the right panel.
3. Select triangles with right click or spline mode.
4. Press `E` to assign the label.
5. Repeat for the remaining structures.
6. Click `Quick Save` or save as `VTP`.
7. Optionally export per-label `STL` files.
8. Mark the task as completed.

## Running the App

From the repository root:

```bash
python main.py
```

## Suggested Environment

Recommended Python version:

- Python 3.10+

Core dependencies used by the app:

- `PyQt6`
- `vedo`
- `vtk`
- `numpy`

If you are creating a fresh environment, a typical install looks like:

```bash
pip install PyQt6 vedo vtk numpy
```

## Project Structure

```text
MeshSemantics/
|-- main.py
|-- meshsemantics/
|   |-- core/
|   |   |-- file_io.py
|   |   |-- interactor.py
|   |   |-- label_engine.py
|   |   |-- project_dataset.py
|   |   `-- spline_selector.py
|   `-- ui/
|       |-- file_panel.py
|       |-- label_panel.py
|       |-- main_window.py
|       `-- vedo_widget.py
`-- doc/
```

## Current Version

Current milestone:

- `v0.1.0`

This version is the first complete software baseline, covering:

- project browsing
- label assignment
- spline surface selection
- undo/redo
- save/export
- task completion tracking

## Notes

- Local test files in `data/` are not treated as part of the main software release by default.
- The current spline selection is optimized for practical labeling use rather than exact CAD-grade boundary editing.

## License

See [LICENSE](LICENSE).
