# Claude Desktop 简体中文汉化 (完整修复版)

## 项目简介

本脚本解决 Claude Desktop (Windows 版) 汉化时**进入界面依然是纯英文**的根本缺陷，实现**主进程原生菜单 + 渲染层 Webview 界面**的 100% 深度汉化。

---

## 为什么之前的汉化尝试全部失败？

通过逆向 Claude Desktop 的主进程 (`app.asar`) 和渲染层编译代码 (`ion-dist/assets/v1/`)，发现了导致汉化失败的**两大核心死穴**：

1. **“换 en-US 内容方案”失效原因**：
   - Claude 渲染层在 `shared-24` 中设计了短路机制：当 `locale === "en-US"` 时，函数 `mv("en-US")` 会直接从内存中提取 React 组件硬编码的 `defaultMessage`（即 `W_()` 函数），并将网络请求 `enabled` 设为 `false`。
   - **Claude 根本不会发起 `/i18n/en-US.json` 网络请求**！直接换 `en-US.json` 的内容对主界面起不到任何作用。

2. **“改 locale 方案”失效原因**：
   - Claude 渲染层在 `shared-2-*.js` 中硬编码了 Tier-1 支持语言表：
     ```javascript
     var Jf = ["en-US","de-DE","fr-FR","ko-KR","ja-JP","es-419","es-ES","it-IT","hi-IN","pt-BR","id-ID"];
     ```
   - 若系统语言或配置的语言不在 `Jf` 中，`Qf(e, Jf)` 函数会**无条件强制降级回 `en-US`**！
   - 同时，降级守卫 `wp(e)` 会比对 `index.html` 中的 `data-i18n-catalogs` 元数据，缺少时也会被拦截。
   - 此外，Chromium 的 V8 Code Cache 与 HTTP 缓存会将未汉化的旧状态持久化在磁盘，导致重启依然显示英文。

---

## 解决方案（当前实现）

本工具采用**全链路闭环汉化方案**：

1. **外壳层汉化**：铺设 `resources/zh-CN.json`（主进程完全支持匹配 `/[a-z]{2}-[A-Z]{2}/` 的文件名）。
2. **渲染层核心库修补**：在 `resources/ion-dist/assets/v1/shared-2-*.js` 中，将 `"zh-CN"` 和 `"zh-Hans"` 注册到硬编码支持列表 `Jf`，并为 `wp(e)` 守卫添加中文放行规则。
3. **元数据注入**：在 `resources/ion-dist/index.html` 注入 `data-i18n-catalogs` 元数据。
4. **语言包铺设**：铺设 28,000+ 条官方高品质汉化译文至 `resources/ion-dist/i18n/zh-CN.json`。
5. **配置同步**：同时更新官方模式与第三方模式 (`Claude-3p`) 的 `config.json` 为 `zh-CN`。
6. **缓存深度清理**：自动清除 Chromium 的 `Cache` 和 `Code Cache`，确保新 bundle 和语言包立即生效。

---

## 快速使用

### 方式一：双击一键脚本 (推荐)
直接双击运行目录下的：
```
一键汉化.cmd
```
脚本会自动关闭运行中的 Claude、备份原文件、注入补丁并清理缓存。完成后可直接按提示启动 Claude。

### 方式二：命令行运行
```bash
# 1. 运行汉化安装
python install-zh.py

# 2. 安装完成后自动启动 Claude
python install-zh.py --launch

# 3. 验证汉化状态与各层链路
python verify.py

# 4. 如需还原回官方原版
python install-zh.py --restore
```

---

## 文件结构说明

| 文件 | 作用 |
|---|---|
| `一键汉化.cmd` | Windows 用户一键双击运行脚本（自动设置 UTF-8 环境并执行） |
| `install-zh.py` | 汉化核心主程序（支持自动寻址、进程管理、代码注入、缓存清理与还原） |
| `verify.py` | 全链路验证工具（只读检测主进程、渲染层、核心表与元数据状态） |
| `zh-Hans.json` | 官方渲染层中文词典（28,803 条，覆盖率 97.7%） |
| `dynamic-zh-Hans.json` | 动态文案包（模型名、动态提示等） |
| `shell-zh-Hans.json` | 桌面外壳原生菜单词典（703 条） |
| `.backup-<时间戳>/` | 每次修改前的完整自动备份目录 |
