@echo off
setlocal
cd /d "%~dp0"
set /p VERSION=<version.txt
echo === MindFlow %VERSION% ===

rem --- 1. venv: pywebview can pythonnet, nen uu tien Python 3.13 / 3.12 / 3.11 ---
if not exist venv\Scripts\python.exe (
  for %%V in (3.13 3.12 3.11) do if not exist venv\Scripts\python.exe py -%%V -m venv venv 2>nul
)
if not exist venv\Scripts\python.exe py -3 -m venv venv
if not exist venv\Scripts\python.exe (
  echo Khong tao duoc venv. Hay cai Python 3.12 hoac 3.13.
  goto :fail
)
call venv\Scripts\activate.bat
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt pyinstaller || goto :fail

rem --- 2. file exe: xoa build + dist cu roi build lai ---
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
set ICON=
if exist app.ico set ICON=--icon app.ico
pyinstaller --noconfirm --clean --onefile --windowed --name MindFlow %ICON% --add-data "ui;ui" --add-data "version.txt;." main.py || goto :fail
if exist MindFlow.spec del MindFlow.spec

rem --- 3. bo cai bang Inno Setup (tu dong tim ISCC.exe) ---
set ISCC=
if exist "D:\file\Inno Setup 7\ISCC.exe" set "ISCC=D:\file\Inno Setup 7\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 7\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 7\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 7\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 7\ISCC.exe"
if not defined ISCC if exist "%LocalAppData%\Programs\Inno Setup 7\ISCC.exe" set "ISCC=%LocalAppData%\Programs\Inno Setup 7\ISCC.exe"
if not defined ISCC for /f "delims=" %%I in ('where iscc 2^>nul') do set "ISCC=%%I"
if not defined ISCC (
  echo Khong tim thay ISCC.exe - chi tao file exe, bo qua bo cai.
  goto :done
)
"%ISCC%" /Qp /DAppVersion=%VERSION% installer.iss || goto :fail

:done
echo.
echo Xong. Trong thu muc dist:
dir /b dist
pause
exit /b 0

:fail
echo.
echo LOI - xem thong bao phia tren.
pause
exit /b 1
