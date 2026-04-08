@echo off
echo Setting up CloudCarbon...

echo Creating virtual environment...
python -m venv venv

echo Activating virtual environment...
call venv\Scripts\activate.bat

echo Installing API dependencies...
pip install -r apps/api/requirements.txt

echo Done. To start the API run:
echo call venv\Scripts\activate.bat
echo cd apps\api
echo python -m uvicorn src.main:app --reload --port 8000
