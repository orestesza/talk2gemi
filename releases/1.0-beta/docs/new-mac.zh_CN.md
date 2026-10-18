# 在另一台 Apple 芯片 Mac 上使用 talk2gemi

适用 Apple 芯片（M 系列）、macOS 14 或更新版本。

## 首次启动

1. 打开 `Talk2Gemi.dmg`，把 `Talk2Gemi` 拖入“应用程序”文件夹（或解压 `talk2gemi-mac-arm64.zip`）。
2. 打开程序。菜单栏出现黑色**雪鸮图标**，后台中继自动随程序启动；无需安装 Python、ESP-IDF 或开发工具。
3. 允许系统蓝牙访问权限。点击菜单栏雪鸮图标 → **Gemini API Key…**，粘贴自己的 Key，安全保存到钥匙串。
4. 打开 **Open control panel**，查看当前中继地址和云端连接状态，可选择参考音色和默认音量。

本安装包采用本地固定代码签名，尚未取得 Apple 公证。首次打开若系统提示安全拦截，请在“系统设置” → “隐私与安全性”中选择“仍要打开”。

## 把设备改连到新 Mac

**蓝牙直连：**
1. 设备长按侧面 `DOWN` 键打开 `Settings` → `Connection` → `Bluetooth` → `Change Mac`。
2. 选择新 Mac，按 `OK`。首次配对包含两步确认：
   - 第一步：设备显示 **System code**，与 macOS 系统蓝牙弹窗核对，数字一致则在 Mac 点击确认，并在设备短按 `OK`。
   - 第二步：设备显示 **Mac link code**，与 Talk2Gemi 弹出的 **Pair talk2gemi** 窗口核对，一致则点击 **Confirm matching code**。
3. 配对成功后重连无需重复确认。

**若需切换连接方式：**
- 在设备 `Settings` → `Connection`，把 **Prefer** 设为 **BLE**，选择 **Reconnect**。

## API Key 和设置存放位置

- Key 保存在当前 Mac 的系统钥匙串中，条目名为 **talk2gemi Gemini API Key**。
- 程序仅在运行时从钥匙串读取 Key 传给本地中继，再由中继与 Google Gemini 通信；设备端绝不接收、保存任何 API Key。
- 固件、安装包和源码仓库中绝不包含 API Key、旧配对状态或用户录音。
- 本地日志与运行配置位于 `~/Library/Application Support/talk2gemi/`。
