@echo off
cd /d "%~dp0"
py -m pip install -r requirements.txt pyinstaller
py -m PyInstaller --noconfirm --onefile --windowed --name PhoneBackup --collect-all arabic_reshaper phone_backup.py
echo.
echo Done. Your program is at: dist\PhoneBackup.exe
pause
