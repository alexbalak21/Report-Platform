# Report Generator

A Windows desktop application that automates the generation of Word reports from Excel data. Select a row from your data file, and the app fills a `.docx` template with the corresponding values and saves the finished report.

---

## Table of Contents

- [Features](#features)
- [How It Works](#how-it-works)
- [Installation](#installation)
- [Project Structure](#project-structure)
- [Data Files](#data-files)
- [Mapping File](#mapping-file)
  - [Column Mapping](#column-mapping)
  - [Computed Fields](#computed-fields)
  - [Operations](#operations)
  - [File Name Rule](#file-name-rule)
  - [Config Block](#config-block)
- [Development Setup](#development-setup)
- [Building the Executable](#building-the-executable)
- [Releasing a New Version](#releasing-a-new-version)
- [Auto-Update System](#auto-update-system)
- [Architecture](#architecture)

---

## Features

- Fills a `.docx` template from a selected Excel row
- Supports multiple mapping profiles (one JSON file per report type)
- Auto-generates report numbers using a date-based counter (`yymmdd-N`)
- Writes the generated report number back into the Excel file
- Persists all file paths between sessions (no re-selecting every time)
- Silent background update checker with download progress bar
- Packaged as a Windows installer (`Setup.exe`)

---

## How It Works

```
Excel file (.xlsx)
      │
      │  row selected by user
      ▼
Mapping file (.json)  ──►  Report Generator  ──►  Word report (.docx)
                                  │
                                  └──► writes report number back to Excel
```

1. The user selects an Excel data file, a `.docx` template, a mapping file, and an output folder.
2. They pick the row number corresponding to the sample they want to report on.
3. The app reads that row, applies the mapping rules, fills all `{{placeholder}}` tags in the template, and saves the finished `.docx`.
4. The auto-generated report number (`yymmdd-N`) is written back into the Excel file.

---

## Installation

Download the latest `ReportGenerator-Setup-x.x.x.exe` from the [Releases page](https://github.com/alexbalak21/Report-Generator/releases) and run it.

The installer:
- Installs the app to `C:\Program Files\ReportGenerator\`
- Copies the `/data` folder (templates, mapping files) alongside the exe
- Creates a Start Menu shortcut and an optional desktop shortcut
- Registers an uninstaller in *Add or Remove Programs*

User data (SQLite config database) is stored in `%APPDATA%\ReportGenerator\` and is preserved across updates.

---

## Project Structure

```
Report-Generator/
│
├── app/
│   ├── __init__.py              # __version__ lives here
│   ├── app.py                   # Entry point: creates MainWindow, starts update check
│   ├── updater.py               # Background update checker + download dialog
│   │
│   ├── core/
│   │   ├── excel_reader.py      # Reads .xlsx files, caches all sheets
│   │   ├── mapping_loader.py    # Loads/saves .json mapping files
│   │   ├── processors.py        # Field transformation operations
│   │   ├── report_generator.py  # Orchestrates Excel → template → .docx
│   │   ├── report_actions.py    # High-level generate action
│   │   ├── state_manager.py     # Incremental report number state
│   │   └── word_processor.py    # Fills {{placeholders}} in .docx templates
│   │
│   ├── gui/
│   │   ├── config_persistence.py  # Save/restore UI state via SQLite
│   │   ├── file_dialogs.py        # File picker wrappers
│   │   ├── line_selector.py       # Row number widget
│   │   ├── report_actions.py      # GUI-level generate logic
│   │   └── windows/
│   │       ├── main_window.py              # Main application window
│   │       └── generation_complete_dialog.py
│   │
│   ├── repository/
│   │   ├── config_repository.py   # SQLite: config key-value + mapping path list
│   │   └── rapport_repository.py
│   │
│   └── utils/
│       └── paths.py               # Path helpers for dev vs frozen exe
│
├── data/
│   ├── rapport_mapping.json     # Default mapping file
│   ├── rapport_template.docx    # Default Word template
│   ├── Rapport_data.xlsx        # Sample data file
│   └── mappings.xlsx            # (legacy reference)
│
├── tests/                       # Unit tests
├── run.py                       # Dev entry point
├── run.spec                     # PyInstaller build spec
└── installer.iss                # Inno Setup installer script
```

---

## Data Files

### Excel file (`.xlsx`)

Your data file must have a **header row** in row 1. Each subsequent row is one sample/report. The app reads whichever row number you select in the UI.

Example columns:
| date rapport | nom entreprise client | appellation produit | lot | conformite |
|---|---|---|---|---|
| 21/06/2026 | ACME Corp | Steak haché | L240501 | Conforme |

### Word template (`.docx`)

Place `{{placeholder}}` tags anywhere in the document — in paragraphs, tables, headers, or footers. The app replaces each tag with the corresponding value from Excel.

Example:
```
Date of report: {{date_rapport}}
Client: {{nom_entreprise_client}}
Product: {{appellation_produit}}
Report No.: {{sample_number}}
```

---

## Mapping File

The mapping file is a `.json` file that tells the app how to connect Excel columns to Word placeholders. You can have multiple mapping files for different report types and switch between them in the UI.

### Column Mapping

The simplest rule: take a value directly from an Excel column.

```json
"nom entreprise client": {
  "column": "nom entreprise client",
  "placeholder": "{{nom_entreprise_client}}"
}
```

### Computed Fields

Fields that don't come directly from a column but are calculated:

```json
"date_du_jour": {
  "operation": "today",
  "format": "%d/%m/%Y",
  "placeholder": "{{date_du_jour}}"
}
```

```json
"numero_rapport": {
  "operation": "excel_day_counter",
  "date_column": "date rapport",
  "sample_column": "numero echantillon",
  "date_format": "%d/%m/%Y"
}
```

`excel_day_counter` generates a unique report number in the format `yymmdd-N` where `N` is the count of samples analysed on that day (e.g. `260621-3`).

### Operations

Operations can be chained on any column value under the `"operations"` key:

| Operation | Description | Parameters |
|---|---|---|
| `date_format` | Format a date value | `"format": "%d/%m/%Y"` |
| `uppercase` | Convert to uppercase | — |
| `lowercase` | Convert to lowercase | — |
| `multiply` | Multiply numeric value | `"value": 100` |
| `round` | Round to N decimals | `"decimals": 0` |
| `suffix` | Append a string | `"value": "%"` |
| `formula` | Evaluate Excel formula result | — |
| `concat` | Concatenate multiple fields | `"fields": [...]` |
| `lookup` | Look up a value in another sheet | `"sheet": "...", "key": "..."` |

Example — display a percentage from a decimal Excel value:

```json
"imp": {
  "column": "imp",
  "placeholder": "{{imp}}",
  "operations": [
    { "type": "formula" },
    { "type": "multiply", "value": 100 },
    { "type": "round", "decimals": 0 },
    { "type": "suffix", "value": "%" }
  ]
}
```

### File Name Rule

Controls the output filename of the generated report:

```json
"file_name": {
  "name": "NOVOCIB Rapport d'essai",
  "operation": "format",
  "format": "{name} {numero_rapport}.docx"
}
```

This produces filenames like `NOVOCIB Rapport d'essai 260621-3.docx`.

### Config Block

The `"config"` block stores paths and settings that the UI writes back automatically when you select files:

```json
"config": {
  "data_file": "C:/Reports/Rapport_data.xlsx",
  "template_file": "C:/Reports/rapport_template.docx",
  "output_dir": "C:/Reports/output",
  "date_format": "%d/%m/%Y"
}
```

These are saved automatically — you don't need to edit them manually.

---

## Development Setup

**Requirements:** Python 3.11+, Windows (for building the exe)

```powershell
# Clone the repo
git clone https://github.com/alexbalak21/Report-Generator.git
cd Report-Generator

# Create and activate a virtual environment
python -m venv env
env\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the app
py run.py
```

---

## Building the Executable

**Requirements:** PyInstaller and Inno Setup 6 installed.

```powershell
pip install pyinstaller

# 1. Build the exe folder
pyinstaller run.spec

# 2. Build the installer
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
```

Output: `installer_output\ReportGenerator-Setup-x.x.x.exe`

> The `dist/` and `installer_output/` folders are git-ignored — never commit them.

---

## Releasing a New Version

1. **Bump the version** in `app/__init__.py`:
   ```python
   __version__ = "1.0.2"
   ```

2. **Update `installer.iss`:**
   ```ini
   AppVersion=1.0.2
   OutputBaseFilename=ReportGenerator-Setup-1.0.2
   ```

3. **Rebuild:**
   ```powershell
   pyinstaller run.spec
   & "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
   ```

4. **Push and tag:**
   ```powershell
   git add .
   git commit -m "Release v1.0.2"
   git tag v1.0.2
   git push origin main
   git push origin v1.0.2
   ```

5. **Publish on GitHub:**
   - Go to [Releases → Draft a new release](https://github.com/alexbalak21/Report-Generator/releases/new)
   - Choose tag `v1.0.2`, title `v1.0.2`
   - Attach `installer_output\ReportGenerator-Setup-1.0.2.exe`
   - Click **Publish release**

Users on older versions will be prompted to update the next time they launch the app.

---

## Auto-Update System

On every launch, the app silently checks the GitHub Releases API in a background thread:

```
https://api.github.com/repos/alexbalak21/Report-Generator/releases/latest
```

If a newer version is found, an update dialog appears:

```
┌──────────────────────────────────────┐
│       A new version is available!    │
│          v1.0.1  →  v1.0.2           │
│                                      │
│  The update will be downloaded and   │
│  installed automatically.            │
│                                      │
│  ████████████░░░░  4.2 / 6.1 MB      │
│                                      │
│  [Download & Install]   [Later]      │
│                                      │
│       Ignore this version            │
└──────────────────────────────────────┘
```

| Button | Behaviour |
|---|---|
| **Download & Install** | Downloads the `Setup.exe` in the background with a live progress bar, then launches it and closes the app |
| **Later** | Dismisses the dialog; the user will be prompted again next launch |
| **Ignore this version** | Saves the version number to the local database; the user will not be prompted for this version again (but will be for the next one) |

If the network is unreachable or GitHub is down, the check fails silently with no impact on app startup.

---

## Architecture

### Path resolution

Two distinct locations exist when the app is frozen by PyInstaller:

| Location | Contents | Accessed via |
|---|---|---|
| `sys._MEIPASS` | Files declared in `run.spec` `datas[]` (e.g. `icon.ico`) | `get_resource_path()` |
| `os.path.dirname(sys.executable)` | The exe + files installed by Inno Setup (e.g. `/data`) | `get_data_path()` |

### Database

User config is stored in a SQLite database at `%APPDATA%\ReportGenerator\app_data.db` (writable, preserved across updates). It holds:
- `config` table — key/value pairs (last used paths, ignored update version)
- `mappings` table — list of known mapping file paths