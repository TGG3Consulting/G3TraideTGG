@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo Установка зависимостей BinanceFriend
echo ============================================

REM Используем Python 3.12 (3.14 имеет проблемы с numpy)
set PYTHON=C:\Users\Tigran\AppData\Local\Programs\Python\Python312\python.exe

if not exist "%PYTHON%" (
    echo ERROR: Python 3.12 не найден!
    echo Установи Python 3.12 с https://www.python.org/downloads/
    pause
    exit /b 1
)

echo Python: %PYTHON%
%PYTHON% --version

echo.
echo Обновление pip...
%PYTHON% -m pip install --upgrade pip

echo.
echo Установка зависимостей из requirements.txt...
%PYTHON% -m pip install -r requirements.txt

echo.
echo ============================================
echo Установка завершена!
echo ============================================
echo.
echo Теперь можно запускать:
echo   run_ml_training.bat - обучение ML
echo   run.py - основной скринер
echo.
pause
