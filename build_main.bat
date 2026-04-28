@echo off
setlocal

cd /d "%~dp0"

set "ENV_NAME=meshlabeler"
set "SPEC_FILE=main.spec"
set "CONDA_BAT="

if not exist "%SPEC_FILE%" (
    echo [ERROR] %SPEC_FILE% not found.
    pause
    exit /b 1
)

if defined CONDA_EXE (
    for %%I in ("%CONDA_EXE%") do set "CONDA_BAT=%%~dpI..\condabin\conda.bat"
)

if not defined CONDA_BAT if exist "%USERPROFILE%\miniconda3\condabin\conda.bat" set "CONDA_BAT=%USERPROFILE%\miniconda3\condabin\conda.bat"
if not defined CONDA_BAT if exist "%USERPROFILE%\anaconda3\condabin\conda.bat" set "CONDA_BAT=%USERPROFILE%\anaconda3\condabin\conda.bat"
if not defined CONDA_BAT if exist "C:\ProgramData\miniconda3\condabin\conda.bat" set "CONDA_BAT=C:\ProgramData\miniconda3\condabin\conda.bat"
if not defined CONDA_BAT if exist "C:\ProgramData\anaconda3\condabin\conda.bat" set "CONDA_BAT=C:\ProgramData\anaconda3\condabin\conda.bat"

if not defined CONDA_BAT (
    echo [ERROR] conda.bat not found. Please check your Conda installation.
    pause
    exit /b 1
)

call "%CONDA_BAT%" activate "%ENV_NAME%"
if errorlevel 1 (
    echo [ERROR] Failed to activate Conda environment: %ENV_NAME%
    pause
    exit /b 1
)

where pyinstaller >nul 2>nul
if errorlevel 1 (
    echo [ERROR] pyinstaller was not found in environment %ENV_NAME%.
    echo Example: conda activate %ENV_NAME% ^&^& pip install pyinstaller
    pause
    exit /b 1
)

echo.
echo [INFO] Building %SPEC_FILE%
echo [INFO] Environment: %ENV_NAME%
echo.

pyinstaller --clean -y "%SPEC_FILE%"
if errorlevel 1 (
    echo.
    echo [ERROR] Build failed.
    pause
    exit /b 1
)

echo.
echo [OK] Build completed. Check the dist directory.
pause
