#!/bin/sh
set -eu
python3 -m pip install -r native_browser_helper/requirements.txt
python3 -m PyInstaller --clean --noconfirm native_browser_helper/packaging/macos.spec
python3 native_browser_helper/scripts/package_macos.py --app dist/XianyuNativeHelper.app
echo "Built native_browser_helper/dist/xianyu-native-browser-helper-macos-arm64-1.0.1.zip"
