@echo off
echo Starting Warri Region GIS Platform...
echo.
pip install flask flask-cors --quiet
echo.
echo Open browser at: http://localhost:5000
echo Press Ctrl+C to stop.
echo.
python app.py
pause
