@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo  Electronic Device Recommender
echo ========================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install Python 3.10+ and try again.
    pause
    exit /b 1
)

if not exist "venv\Scripts\activate.bat" (
    echo First run — setting up...
    python -m venv venv
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment.
        pause
        exit /b 1
    )
    call venv\Scripts\activate.bat
    python -m pip install --upgrade pip
    pip install -r requirements.txt
    pip install tavily-python spacy
    python -m spacy download en_core_web_sm
) else (
    call venv\Scripts\activate.bat
)

if not exist ".env" (
    if exist ".env.example" (
        copy /Y ".env.example" ".env" >nul
        echo Created .env from .env.example — add your API keys if needed.
    ) else (
        echo WARNING: .env file not found.
    )
    echo.
)

where docker >nul 2>&1
if not errorlevel 1 (
    echo Starting PostgreSQL...
    docker-compose up -d
)

echo.
echo Starting server at http://localhost:8000
echo Press Ctrl+C to stop.
echo.

uvicorn main:app --reload --host 0.0.0.0 --port 8000

pause
