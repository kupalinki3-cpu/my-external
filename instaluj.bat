@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PY=python
where python >nul 2>nul || set PY=py
title Instalacja
echo Instaluje biblioteki (to moze chwile potrwac)...
%PY% -m pip install --upgrade pip
%PY% -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Instalacja sie nie udala. Czy masz zainstalowanego Pythona? winget install Python.Python.3.12
  pause
  exit /b 1
)
where ffmpeg >nul 2>nul || (
  echo.
  echo Brak ffmpeg - instaluje...
  winget install --accept-source-agreements --accept-package-agreements ffmpeg
  echo Po instalacji ffmpeg zamknij to okno i otworz ponownie.
)
if not exist config.yaml (
  copy config.example.yaml config.yaml >nul
  echo Utworzono config.yaml - uzupelnij go teraz w Notatniku.
  notepad config.yaml
)
if not exist nagrania mkdir nagrania
echo.
echo Gotowe. Teraz odpal sprawdz.bat, a potem start.bat
pause
