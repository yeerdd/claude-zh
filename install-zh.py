#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Desktop 简体中文汉化安装器 (完整修复版)

解决原脚本失效的核心原因：
  1. 渲染层硬编码支持语言表 Jf 未包含 zh-CN，导致前端加载时被静默降级为 en-US。
  2. 当语言被降级为 en-US 时，Claude 前端直接使用 JS 内嵌的默认英文组件属性（W_()），
     根本不会去请求任何语言包 JSON，导致“单纯换 en-US.json 内容”或“单纯改配置”完全无效。
  3. Chromium V8 Code Cache 与 HTTP Cache 缓存了未汉化的旧 bundle 与配置。
  4. Windows 控制台默认编码非 UTF-8 导致脚本输出特殊字符时抛出 UnicodeEncodeError 异常中断。

本安装器执行完整 6 步闭环操作：
  [1/6] 终止后台运行的 Claude 进程（防止配置刷回与文件占用）
  [2/6] 铺设外壳层与渲染层语言包（zh-CN / zh-Hans）
  [3/6] 修补渲染层 shared-2 bundle（将 zh-CN/zh-Hans 加入核心支持表 Jf，并解除 wp 降级守卫）
  [4/6] 注入 index.html 的 i18n-catalogs 元数据
  [5/6] 同步官方模式与第三方模式的 config.json（locale -> zh-CN）
  [6/6] 清理 Chromium 缓存（Cache / Code Cache / GPUCache）

支持随时完整还原：python install-zh.py --restore
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time

# 保证 Windows 控制台输出 UTF-8 不报错
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(os.environ.get("LOCALAPPDATA", ""), "AnthropicClaude")

LOCALE = "zh-CN"
ALIASES = ["zh-Hans"]

CONFIGS = [
    os.path.join(os.environ.get("APPDATA", ""), "Claude", "config.json"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Claude-3p", "config.json"),
]

CACHE_DIRS = [
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Claude-3p", "Cache"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Claude-3p", "Code Cache"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Claude-3p", "GPUCache"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Claude-3p", "DawnGraphiteCache"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Claude-3p", "DawnWebGPUCache"),
    os.path.join(os.environ.get("APPDATA", ""), "Claude", "Cache"),
    os.path.join(os.environ.get("APPDATA", ""), "Claude", "Code Cache"),
    os.path.join(os.environ.get("APPDATA", ""), "Claude", "GPUCache"),
]

SOURCES = {
    "zh-Hans.json": ["ion-dist", "i18n", "zh-Hans.json"],
    "dynamic-zh-Hans.json": ["ion-dist", "i18n", "dynamic", "zh-Hans.json"],
    "shell-zh-Hans.json": ["zh-Hans.json"],
}


def find_app_dir():
    """挑选版本号最大的 app-<version> 目录。"""
    cands = []
    for p in glob.glob(os.path.join(ROOT, "app-*")):
        m = re.search(r"app-([\d.]+)$", p)
        if m and os.path.isdir(os.path.join(p, "resources")):
            cands.append(([int(x) for x in m.group(1).split(".")], p))
    if not cands:
        sys.exit(f"找不到 Claude 安装目录：{ROOT}\\app-*")
    cands.sort()
    return cands[-1][1]


def backup(path, bdir, dry):
    """把文件原样留底。"""
    if not os.path.exists(path):
        return
    rel = os.path.relpath(path, os.path.abspath(os.sep))
    dst = os.path.join(bdir, rel)
    if os.path.exists(dst):
        return
    if dry:
        print(f"    [dry] 备份 {path}")
        return
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(path, dst)


def claude_running():
    """检查 Claude 是否在运行。"""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq claude.exe", "/NH"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
        return "claude.exe" in out.lower()
    except Exception:
        return False


def kill_claude(dry):
    """自动清理并终止运行中的 Claude。"""
    print("\n[1/6] 检查 Claude 运行状态")
    if not claude_running():
        print("    Claude 未在运行，可安全修改。")
        return True
    if dry:
        print("    [dry] 检测到 Claude 正在运行，需终止其进程。")
        return True
    print("    检测到 Claude 正在后台运行，正在自动终止进程...")
    subprocess.run(["taskkill", "/F", "/IM", "claude.exe", "/T"], capture_output=True)
    time.sleep(1.5)
    if claude_running():
        print("    警告：部分 Claude 进程仍在运行，再次尝试关闭...")
        subprocess.run(["taskkill", "/F", "/IM", "claude.exe", "/T"], capture_output=True)
        time.sleep(2)
    print("    已成功关闭 Claude。")
    return True


def deploy_catalogs(app, bdir, dry):
    """铺设语言包。"""
    print("\n[2/6] 铺设外壳与渲染层语言包")
    res = os.path.join(app, "resources")
    for src, parts in SOURCES.items():
        s = os.path.join(HERE, src)
        if not os.path.exists(s):
            sys.exit(f"缺少源文件 {src}，请检查当前目录文件完整性。")
        for code in [LOCALE] + ALIASES:
            d = os.path.join(res, *parts)
            d = os.path.join(os.path.dirname(d), f"{code}.json")
            backup(d, bdir, dry)
            if dry:
                print(f"    [dry] {src} -> {os.path.relpath(d, res)}")
                continue
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(s, d)
            print(f"    {src} -> {os.path.relpath(d, res)}")

    # 空 overrides 避免 404
    for code in [LOCALE] + ALIASES:
        ov = os.path.join(res, "ion-dist", "i18n", f"{code}.overrides.json")
        backup(ov, bdir, dry)
        if not dry:
            with open(ov, "w", encoding="utf-8") as f:
                f.write("{}")
        print(f"    [] -> {os.path.relpath(ov, res)}")


def patch_shared2(app, bdir, dry):
    """修补渲染层核心 shared-2 JS bundle。

    这是汉化成功的决定性步骤：
      - 官方代码硬编码：var Jf = ["en-US", "de-DE", ...];
      - 若语言不在 Jf 中，Qf(e, Jf) 会无条件回退为 en-US。
      - 回退为 en-US 后，Claude 前端短路使用内嵌的 W_() 英文硬编码组件属性，
        根本不发起 /i18n/ 语言包网络请求。
      - 将 zh-CN / zh-Hans 加入 Jf 数组，并为 wp(e) 降级守卫加上中文放行保护。
    """
    print("\n[3/6] 修补渲染层 shared-2 核心支持库")
    v1_dir = os.path.join(app, "resources", "ion-dist", "assets", "v1")
    matches = glob.glob(os.path.join(v1_dir, "shared-2-*.js"))
    if not matches:
        print("    警告：找不到 shared-2-*.js，跳过 JS 修补")
        return False
    shared2_file = matches[0]
    with open(shared2_file, encoding="utf-8") as f:
        js = f.read()

    # 1. 扩充 Jf 语言表
    old_jf = '["en-US","de-DE","fr-FR","ko-KR","ja-JP","es-419","es-ES","it-IT","hi-IN","pt-BR","id-ID"]'
    new_jf = '["zh-CN","zh-Hans","en-US","de-DE","fr-FR","ko-KR","ja-JP","es-419","es-ES","it-IT","hi-IN","pt-BR","id-ID"]'

    # 2. 解除 wp 守卫降级
    old_wp = "function wp(e){return Xf(e)&&!Wf().includes(e)?Kf:e}"
    new_wp = 'function wp(e){return(e==="zh-CN"||e==="zh-Hans")?e:Xf(e)&&!Wf().includes(e)?Kf:e}'

    patched = False
    new_js = js

    if old_jf in new_js:
        new_js = new_js.replace(old_jf, new_jf, 1)
        patched = True
        print("    ✓ 成功将 zh-CN / zh-Hans 加入核心支持表 Jf")
    elif new_jf in new_js:
        print("    - 核心支持表 Jf 已包含 zh-CN")
    else:
        print("    警告：未匹配到目标 Jf 模式，可能是新版本混淆名变动")

    if old_wp in new_js:
        new_js = new_js.replace(old_wp, new_wp, 1)
        patched = True
        print("    ✓ 成功配置 wp 降级守卫放行规则")
    elif new_wp in new_js:
        print("    - wp 降级守卫已处于放行状态")

    if dry:
        print(f"    [dry] 修补 {os.path.basename(shared2_file)}")
        return True

    if patched:
        backup(shared2_file, bdir, dry)
        with open(shared2_file, "w", encoding="utf-8") as f:
            f.write(new_js)
        print(f"    ✓ 已写入修补后的 {os.path.basename(shared2_file)}")
    return True


def discover_locales(res):
    """列出语言包中已有的语言。"""
    d = os.path.join(res, "ion-dist", "i18n")
    out = []
    if not os.path.exists(d):
        return out
    for fn in sorted(os.listdir(d)):
        if fn.endswith(".json") and not fn.endswith(".overrides.json"):
            out.append(fn[:-5])
    return list(dict.fromkeys(out))


def patch_html(app, bdir, dry):
    """注入 i18n-catalogs meta 到 index.html。"""
    print("\n[4/6] 注入 i18n-catalogs 页面元数据")
    res = os.path.join(app, "resources")
    idx = os.path.join(res, "ion-dist", "index.html")
    if not os.path.exists(idx):
        print("    跳过：找不到 index.html")
        return
    with open(idx, encoding="utf-8") as f:
        html = f.read()

    locales = discover_locales(res)
    for l in [LOCALE, *ALIASES]:
        if l not in locales:
            locales.append(l)
    content = ",".join(f"{l}:1.1.1" for l in locales)
    tag = (
        f'<meta name="i18n-catalogs" '
        f'data-i18n-catalogs="{content}" content="{content}">'
    )

    if 'name="i18n-catalogs"' in html:
        html2 = re.sub(r'<meta name="i18n-catalogs"[^>]*>', tag, html)
    elif "</head>" in html:
        html2 = html.replace("</head>", tag + "</head>", 1)
    else:
        print("    跳过：没有 </head> 锚点")
        return

    if html2 == html:
        print("    i18n-catalogs 已是最新，无需修改。")
        return
    if dry:
        print(f"    [dry] 注入 meta（{len(locales)} 种语言）")
        return
    backup(idx, bdir, dry)
    with open(idx, "w", encoding="utf-8") as f:
        f.write(html2)
    print(f"    ✓ 已注入 i18n-catalogs meta（共 {len(locales)} 种语言）")


def patch_config(bdir, dry):
    """配置偏好设置语言为 zh-CN。"""
    print("\n[5/6] 设置用户偏好配置 (locale -> zh-CN)")
    found = [c for c in CONFIGS if os.path.exists(c)]
    if not found:
        print("    提示：未找到现有 config.json，将新建配置...")
        for c in CONFIGS:
            try:
                os.makedirs(os.path.dirname(c), exist_ok=True)
                if not dry:
                    with open(c, "w", encoding="utf-8") as f:
                        json.dump({"locale": LOCALE}, f, ensure_ascii=False, indent="\t")
                print(f"    新建配置 -> {c}")
            except Exception:
                pass
        return

    for cfg_path in found:
        tag = "第三方模式" if "Claude-3p" in cfg_path else "官方模式"
        try:
            with open(cfg_path, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
        old = cfg.get("locale")
        if old == LOCALE:
            print(f"    [{tag}] 已经是 {LOCALE}")
            continue
        if dry:
            print(f"    [dry][{tag}] locale: {old} -> {LOCALE}")
            continue
        backup(cfg_path, bdir, dry)
        cfg["locale"] = LOCALE
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent="\t")
        print(f"    [{tag}] locale: {old} -> {LOCALE}")


def clear_caches(dry):
    """清理 Chromium 缓存以强制重新加载渲染层。"""
    print("\n[6/6] 清理 Chromium 缓存 (避免加载旧英文缓存)")
    for cd in CACHE_DIRS:
        if os.path.exists(cd):
            if dry:
                print(f"    [dry] 清理 {cd}")
                continue
            try:
                shutil.rmtree(cd)
                print(f"    ✓ 已清除 {os.path.basename(os.path.dirname(cd))}\\{os.path.basename(cd)}")
            except Exception as e:
                print(f"    - 清理受限: {cd} ({e})")


def verify(app):
    """核对修改完整性。"""
    print("\n[校验检查]")
    res = os.path.join(app, "resources")
    checks = [
        ("外壳层菜单语言包", os.path.join(res, f"{LOCALE}.json")),
        ("渲染层主语言包", os.path.join(res, "ion-dist", "i18n", f"{LOCALE}.json")),
        ("渲染层动态文案包", os.path.join(res, "ion-dist", "i18n", "dynamic", f"{LOCALE}.json")),
        ("渲染层 overrides", os.path.join(res, "ion-dist", "i18n", f"{LOCALE}.overrides.json")),
    ]
    all_ok = True
    for label, path in checks:
        if os.path.exists(path):
            size = os.path.getsize(path)
            print(f"    ✓ {label}: 正常 ({size} 字节)")
        else:
            print(f"    ✗ {label}: 缺失 ({path})")
            all_ok = False
    return all_ok


def do_restore(dry):
    bdirs = sorted(glob.glob(os.path.join(HERE, ".backup-*")))
    if not bdirs:
        sys.exit("未找到备份目录。")
    b = bdirs[-1]
    print(f"正在从最新备份 {os.path.basename(b)} 还原...")
    n = 0
    for dirpath, _, files in os.walk(b):
        for fn in files:
            src = os.path.join(dirpath, fn)
            rel = os.path.relpath(src, b)
            dst = os.path.abspath(os.sep) + rel
            if dry:
                print(f"    [dry] -> {dst}")
                continue
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                print(f"    ✓ 还原 {dst}")
                n += 1
            except Exception as e:
                print(f"    ✗ 还原失败 {dst}: {e}")
    print(f"\n还原完成，共恢复 {n} 个文件。")


def main():
    ap = argparse.ArgumentParser(description="Claude Desktop 简体中文汉化工具")
    ap.add_argument("--dry-run", action="store_true", help="演练模式，不实际修改")
    ap.add_argument("--restore", action="store_true", help="从最近一次备份中恢复")
    ap.add_argument("--app-dir", help="指定 Claude app-XXXX 路径")
    ap.add_argument("--launch", action="store_true", help="完成后自动启动 Claude")
    a = ap.parse_args()

    print("=" * 60)
    print("       Claude Desktop 简体中文汉化安装工具")
    print("=" * 60)

    if a.restore:
        kill_claude(a.dry_run)
        do_restore(a.dry_run)
        return 0

    app = a.app_dir or find_app_dir()
    print(f"\n检测到 Claude 安装目录: {app}")
    bdir = os.path.join(HERE, f".backup-{time.strftime('%Y%m%d-%H%M%S')}")

    kill_claude(a.dry_run)
    deploy_catalogs(app, bdir, a.dry_run)
    patch_shared2(app, bdir, a.dry_run)
    patch_html(app, bdir, a.dry_run)
    patch_config(bdir, a.dry_run)
    clear_caches(a.dry_run)

    if a.dry_run:
        print("\n[dry-run] 演练完成，未实际写入。")
        return 0

    ok = verify(app)
    print("\n" + "=" * 60)
    if ok:
        print("  ✓ 汉化成功完成！现在打开 Claude 即可体验全中文界面。")
        print(f"  本次备份保存在: {os.path.basename(bdir)}")
        print("  如需还原，可随时运行: python install-zh.py --restore")
    else:
        print("  [!] 部分文件校验未通过，请检查上方日志。")
    print("=" * 60)

    if a.launch:
        launcher = os.path.join(ROOT, "claude.exe")
        if os.path.exists(launcher):
            print("\n正在启动 Claude Desktop...")
            subprocess.Popen([launcher])

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
