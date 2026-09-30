@echo off
rem Сборка автономного приложения для Windows (ТЗ п.111).
rem
rem Что делает:
rem   1. создаёт окружение buildenv в папке репозитория;
rem   2. ставит зафиксированные зависимости и PyInstaller;
rem   3. собирает приложение по описанию ExecutiveDocSystem.spec.
rem
rem Запуск: двойным щелчком или из командной строки  build_windows.bat
rem
rem ВАЖНО: папка dist\ExecutiveDocSystem\storage — это данные программы.
rem Пересборка удаляет dist целиком, поэтому перед сборкой, если данные уже
rem есть, скопируйте storage в отдельное место.

setlocal
cd /d "%~dp0"

echo [1/3] Проверка Python...
where python >nul 2>nul
if errorlevel 1 (
    echo ОШИБКА: Python не найден в PATH. Установите Python 3.10+ с python.org
    echo и отметьте "Add python.exe to PATH" при установке.
    pause
    exit /b 1
)
python --version

echo [2/3] Установка зависимостей...
if not exist buildenv (
    python -m venv buildenv
    if errorlevel 1 (
        echo ОШИБКА: не удалось создать окружение buildenv
        pause
        exit /b 1
    )
)
call buildenv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r requirements-build.txt
if errorlevel 1 (
    echo ОШИБКА: не удалось установить зависимости
    pause
    exit /b 1
)

echo [3/3] Сборка...
rem --clean удаляет кэш сборки, --noconfirm соглашается на перезапись dist.
python -m PyInstaller --clean --noconfirm ExecutiveDocSystem.spec
if errorlevel 1 (
    echo ОШИБКА: сборка не удалась, смотрите сообщения выше
    pause
    exit /b 1
)

echo.
echo ГОТОВО: dist\ExecutiveDocSystem\ExecutiveDocSystem.exe
echo Данные программы появятся рядом с .exe в папке storage.
pause
endlocal
