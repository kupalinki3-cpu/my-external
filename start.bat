@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PY=python
where python >nul 2>nul || set PY=py
title Sniper + Clipper (nie zamykaj tego okna)
if not exist config.yaml (
  echo Nie ma pliku config.yaml. Najpierw odpal instaluj.bat i uzupelnij config.yaml.
  pause
  exit /b 1
)
if not exist nagrania mkdir nagrania

:loop
echo.
echo [%date% %time%] Startuje bota (sniper Vinted + /klip na Discordzie)...
echo Aby zakonczyc: zamknij to okno.
echo.
%PY% -m sniper.main config.yaml
if %errorlevel%==2 (
  echo.
  echo Popraw config.yaml wedlug komunikatu powyzej i odpal start.bat jeszcze raz.
  pause
  exit /b 2
)
echo.
echo Bot sie zatrzymal (kod %errorlevel%). Restart za 15 s...
timeout /t 15
goto loop
