$ErrorActionPreference = "Stop"
python -m pip install -r native_browser_helper/requirements.txt
python -m PyInstaller --clean --noconfirm native_browser_helper/packaging/windows.spec
Compress-Archive -Force -Path dist/XianyuNativeHelper.exe -DestinationPath dist/xianyu-native-browser-helper-windows-x64-1.0.1.zip
Write-Host "Built dist/xianyu-native-browser-helper-windows-x64-1.0.1.zip"
