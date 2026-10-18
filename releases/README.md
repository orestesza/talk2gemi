# talk2gemi 版本归档与发布记录

当前最新候选发布版本为 **1.0 Beta** (`v1.0.0-beta`)。

## 当前版本发布总览

| 版本 | 发布日期 | 状态与核心里程碑 | 归档源码包 | SHA-256 校验 |
| :--- | :--- | :--- | :--- | :--- |
| **[1.0 Beta](1.0-beta/README.md)** | 2026-10-18 | **当前版本**：彻底修复 >30s 长语音断流（内存队列解耦流控）、macOS 黑色雪鸮状态栏图标、标准 DMG 安装包、Muse 卡片备份与多设备支持；43 项测试与实机背诵通过 | [talk2gemi-1.0-beta.zip](talk2gemi-1.0-beta.zip) | [校验文件](talk2gemi-1.0-beta.zip.sha256) |

## 固件应用二进制校验 (SHA-256)

应用固件二进制与实机验收版本逐字节一致：

- **固件文件**：`firmware/talk2gemi_v1.0-beta.bin`
- **文件大小**：`2,161,712` 字节
- **写入位置**：`0x10000`
- **SHA-256**：`446d99097c7c0762fb59639aa2fdc1bc817d9875eb978630585eed049cb39a75`

## 校验方法

在当前版本子目录内运行：
```bash
shasum -a 256 -c SHA256SUMS
```

校验发布 ZIP 压缩包完整性：
```bash
shasum -a 256 -c talk2gemi-1.0-beta.zip.sha256
```
