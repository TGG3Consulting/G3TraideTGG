@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo ML Training - Optimal Parameters
echo ============================================

REM Проверяем Python 3.12
set PYTHON=C:\Users\Tigran\AppData\Local\Programs\Python\Python312\python.exe

if not exist "%PYTHON%" (
    echo ERROR: Python 3.12 не найден!
    echo Путь: %PYTHON%
    pause
    exit /b 1
)

echo Python: %PYTHON%
%PYTHON% --version

echo.
echo Проверка зависимостей...
%PYTHON% -m pip install --quiet structlog scikit-learn aiohttp numpy pandas pydantic pydantic-settings PyYAML

echo.
echo Запуск обучения...
echo ============================================
%PYTHON% -m src.ml.training.optimal_ml_pipeline %*

echo.
echo ============================================
echo Готово!
pause
