#!/bin/bash
# 一键打包：dist/手机文件导出.app + dist/手机文件导出.app.zip
set -e
cd "$(dirname "$0")"

VENV=.venv
APP=手机文件导出
BIN=ADBPhotoSync

# ---------------------------------------------------------------- 依赖准备
echo "[1/6] 准备环境"
if [ ! -d "$VENV" ]; then
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q -r requirements.txt
fi

# 内置 adb 是第三方二进制，不入库；本地没有就自动从 Google 官方仓库拉一份
if [ ! -x adb ]; then
  echo "      本地没有 adb，从 Google 官方仓库下载 platform-tools…"
  TMP=$(mktemp -d)
  curl -fsSL -o "$TMP/pt.zip" \
    https://dl.google.com/android/repository/platform-tools-latest-darwin.zip
  unzip -q "$TMP/pt.zip" -d "$TMP"
  cp "$TMP/platform-tools/adb" ./adb
  chmod +x ./adb
  rm -rf "$TMP"
  echo "      已内置: $(./adb version | head -1)"
fi

# ---------------------------------------------------------------- 图标
echo "[2/6] 生成图标"
QT_QPA_PLATFORM=offscreen "$VENV/bin/python" make_icon.py
rm -rf icon.iconset && mkdir icon.iconset
for s in 16 32 128 256 512; do
  sips -z $s $s icon.png --out icon.iconset/icon_${s}x${s}.png >/dev/null
  sips -z $((s*2)) $((s*2)) icon.png --out icon.iconset/icon_${s}x${s}@2x.png >/dev/null
done
iconutil -c icns icon.iconset -o icon.icns
rm -rf icon.iconset

# ---------------------------------------------------------------- 打包
echo "[3/6] PyInstaller 打包"
"$VENV/bin/pyinstaller" --noconfirm --clean --windowed \
  --name "$BIN" --icon icon.icns \
  --hidden-import PySide6.QtSvg \
  --add-binary "adb:adb" \
  main.py

# ---------------------------------------------------------------- 验证
echo "[4/6] 离屏冒烟验证打包产物"
QT_QPA_PLATFORM=offscreen "dist/$BIN.app/Contents/MacOS/$BIN" &
PID=$!
sleep 4
if kill -0 $PID 2>/dev/null; then
  echo "      app 启动正常，关闭"
  kill $PID
else
  echo "ERROR: app 启动即崩溃"
  exit 1
fi

echo "[5/6] 改名 + 检查"
rm -rf "dist/$APP.app"
mv "dist/$BIN.app" "dist/$APP.app"
/usr/libexec/PlistBuddy -c "Print :CFBundleName" "dist/$APP.app/Contents/Info.plist"
if [ -x "dist/$APP.app/Contents/Frameworks/adb/adb" ]; then
  echo "      OK: 内置 adb 已就位"
  "dist/$APP.app/Contents/Frameworks/adb/adb" version | head -1
else
  echo "ERROR: 内置 adb 缺失"
  exit 1
fi

echo "[6/6] 压缩"
rm -f "dist/$APP.app.zip"
ditto -c -k --sequesterRsrc --keepParent "dist/$APP.app" "dist/$APP.app.zip"
ls -lh "dist/$APP.app.zip"
echo "DONE: dist/$APP.app.zip"
