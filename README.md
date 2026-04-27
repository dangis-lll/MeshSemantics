# MeshSemantics

![MeshSemantics Labeling View](doc/label.png)

[中文说明](./README_zh.md)

MeshSemantics is a desktop application for interactive triangle-mesh annotation, landmark editing, project tracking, and lightweight mesh inspection. It is built for practical `STL` / `VTP` workflows where you need to load a mesh, label regions, place landmarks, check mesh quality, and export results without switching between multiple tools.

The application is implemented with `Python + PyQt6 + vedo + VTK` and currently targets Windows desktop usage.

## What It Does

- Open a single mesh or scan an entire folder into a task queue
- View and annotate `STL` / `VTP` meshes in an interactive 3D viewport
- Label faces with:
  - right-click single-cell toggling
  - spline-based closed-surface selection with preview
- Manage label IDs by adding, deleting, recoloring, and remapping them
- Toggle overwrite mode when relabeling already-labeled cells
- Undo and redo label edits, landmark edits, and mesh cleanup results
- Create, rename, delete, import, export, and place named landmarks
- Auto-load `*.landmarks.json` next to the current mesh when present
- Import label arrays from JSON with `Import Segment`
- Run `Mesh Check` analysis for:
  - non-manifold edges
  - self-intersections
  - small connected components
  - small holes
- Apply low-risk cleanup steps such as duplicate-point merge, small-component removal, and hole filling
- Export labeled meshes as `VTP`, label arrays as `JSON`, landmarks as `*.landmarks.json`, and split meshes as per-label `STL`
- Track per-task status across a project folder and jump to the next unfinished model

## Screenshots

### Labels

![Label Panel](doc/label.png)

### Landmarks

![Landmark Panel](doc/landmark.png)

### Mesh Check

![Mesh Check Panel](doc/meshdoctor.png)

## Workflow Overview

1. Open one mesh with `Open File`, or scan a folder with `Open Folder`.
2. Select a task from the left-side queue.
3. Annotate regions in the `Labels` tab.
4. Quick-save the current mesh as `VTP`.
5. Add or import landmarks in the `Landmarks` tab.
6. Run `Mesh Check` when topology issues are suspected.
7. Export `VTP`, label `JSON`, landmark `JSON`, or per-label `STL` as needed.
8. Mark the task as completed and move to the next unfinished mesh.

## Main Panels

### Labels

- Right click a face to add or remove it from the current selection
- Press `E` to apply the preview selection to the active label
- Double click a labeled face to load that label into the active selector
- Press `S` to enter spline mode
- Use `Enter` to build the spline preview and `C` to clear it
- Enable overwrite mode to replace existing labels instead of skipping them

### Label Management

- Create new label IDs
- Delete label IDs
- Edit label colors
- Remap one label ID to another
- Keep a persistent color map across sessions

### Landmarks

- Add landmarks by name
- Rename or delete existing landmarks
- Double click the mesh in landmark mode to create a landmark at the clicked position
- Click `Pick On Mesh` to place the currently selected landmark
- Import or export landmark JSON
- If a landmark name already exists, the app reuses or updates that landmark instead of silently duplicating it

### Mesh Check

`Mesh Check` is a lightweight inspection and repair workflow inside the main app.

- Analyze the current mesh for:
  - non-manifold edges
  - self-intersections
  - small connected components
  - small holes
- Highlight affected cells in the viewport
- Review counts and a text report in the right panel
- Run `Safe Cleanup` to:
  - merge duplicate points
  - remove small components
  - fill small holes
  - optionally keep only the largest component
  - recompute normals

If cleanup changes the mesh cell count, labels are reset and landmarks plus undo history are cleared so exported data stays consistent with the repaired mesh.

### Task Queue

- Scan a folder into a project dataset
- Show `Unlabeled`, `In Progress`, `Completed`, and `Failed` task states
- Filter by text or status
- Open the previous or next incomplete mesh
- Persist per-folder progress between sessions
- Fall back from a missing generated `VTP` to the original source mesh when possible

## Files

### Input

- `*.stl`
- `*.vtp`

### Output

- labeled `*.vtp`
- label-array `*.json`
- landmark `*.landmarks.json`
- per-label split `*.stl`

### JSON Layouts

Label JSON:

- `cell_count`
- `labels`

Landmark JSON:

- `landmark_count`
- `landmarks`
- each landmark has `name` and `coordinates`

Unplaced landmarks are saved with `coordinates: null`.

## Toolbar And Interaction

Top toolbar:

- `Open File`
- `Open Folder`
- `Import Segment`
- `Save As`
- `Clear Selection`

Viewport floating controls:

- previous model
- next model
- quick save
- completed toggle

Drag and drop:

- mesh files (`.stl`, `.vtp`) can be dropped into the app to open them
- label JSON can be dropped onto an already opened mesh to import segment labels

## Shortcuts

Shortcuts depend on the active right-side tab.

### Global

| Key | Action |
| --- | --- |
| `B` | Open previous model |
| `N` | Open next incomplete model |
| `Ctrl+Z` | Undo |
| `Ctrl+Y` | Redo |

### Labels

| Key | Action |
| --- | --- |
| `Ctrl+S` | Quick save current mesh as `VTP` |
| `Ctrl+Shift+S` | Export current result as `VTP` / `JSON` / `STL` |
| `S` | Enter spline mode |
| `Enter` | Build spline preview |
| `E` | Apply preview to the active label |
| `C` | Clear preview |
| `M` | Toggle completed status |
| `Delete` / `Backspace` | Delete highlighted spline control point |

### Landmarks

| Key | Action |
| --- | --- |
| `Enter` | Add a landmark from the current name input |
| `Ctrl+S` | Quick save landmarks to `*.landmarks.json` |
| `Ctrl+Shift+S` | Export landmarks as JSON |
| `M` | Toggle completed status |
| `Delete` / `Backspace` | Delete the active landmark |

### Mesh Check

| Key | Action |
| --- | --- |
| `Ctrl+S` | Quick save current mesh as `VTP` |
| `Ctrl+Shift+S` | Export current result as `VTP` / `JSON` / `STL` |
| `R` | Run analysis |
| `Ctrl+R` | Run safe cleanup |

## Running From Source

```bash
python -m pip install -r requirements.txt
python main.py
```

Validated direct dependencies:

- `numpy==2.2.6`
- `PyQt6==6.11.0`
- `vedo==2026.6.1`
- `vtk==9.6.1`

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

- `STL` loads as an unlabeled mesh.
- `VTP` reuses its `Label` cell data when available.
- Saving to `STL` exports one file per label into the selected folder.
- `Import Segment` expects a label JSON whose `cell_count` matches the current mesh.
- `Safe Cleanup` is intended for low-risk repair steps, not CAD-style manual surface editing.

## License

See [LICENSE](LICENSE).
