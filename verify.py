#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
复刻 Claude 的语言决策逻辑，验证汉化是否真的能生效。

只读，不改任何东西。主进程和渲染层两套逻辑都用真实数据跑一遍：
  1. 主进程  Xet() -> 读 config.json 的 locale -> Ket() 校验 -> 加载
  2. 渲染层  shared-2 bundle 核心 Jf 语言表与 wp 守卫判定
  3. 渲染层  index.html i18n-catalogs 元数据检查
"""

import glob
import json
import os
import re
import sys

# 保证 Windows 控制台输出 UTF-8 不报错
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.join(os.environ.get("LOCALAPPDATA", ""), "AnthropicClaude")
CONFIGS = [
    (os.path.join(os.environ.get("APPDATA", ""), "Claude", "config.json"), "官方模式"),
    (os.path.join(os.environ.get("LOCALAPPDATA", ""), "Claude-3p", "config.json"), "第三方模式"),
]

# 主进程 Uet() 用的正则
RE_FILENAME = re.compile(r"[a-z]{2}-[A-Z]{2}")
# 主进程 Ket() 用的路径校验
RE_VALID = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})+\.json$")

# 渲染层：已知语言表 Yf
KNOWN = {
    "zh-Hans", "zh-Hant", "zh", "am", "bn", "ja", "ko", "ru", "sv", "th",
    "tr", "uk", "vi", "fi", "nl", "nb", "da", "ta", "te", "ml", "mr"
}


def find_app():
    cands = []
    for p in os.listdir(ROOT):
        m = re.fullmatch(r"app-([\d.]+)", p)
        if m and os.path.isdir(os.path.join(ROOT, p, "resources")):
            cands.append(([int(x) for x in m.group(1).split(".")], p))
    if not cands:
        sys.exit("找不到 app-XXXX 目录")
    cands.sort()
    return os.path.join(ROOT, cands[-1][1])


def check_main(app, cfg_path, tag):
    print(f"\n  [{tag}] {os.path.basename(os.path.dirname(cfg_path))}")
    if not os.path.exists(cfg_path):
        print("    配置文件不存在，跳过")
        return None
    with open(cfg_path, encoding="utf-8") as f:
        locale = json.load(f).get("locale")
    print(f"    config.json locale = {locale!r}")

    res = os.path.join(app, "resources")
    names = set(os.listdir(res))

    # Ket(): 路径格式合法 + 文件确实存在
    ok = bool(locale) and bool(RE_VALID.match(f"{locale}.json")) and f"{locale}.json" in names
    print(f"    Ket({locale!r}) = {ok}")
    if not ok:
        print("    ✗ 配置里的语言不被接受，会回落到系统语言自动检测")
        return None

    print(f"    ✓ 会加载 {locale}")
    for rel in [
        os.path.join("i18n", f"{locale}.json"),
        os.path.join("i18n", "dynamic", f"{locale}.json"),
        os.path.join("i18n", f"{locale}.overrides.json"),
    ]:
        p = os.path.join(res, "ion-dist", rel)
        print(f"      {'✓' if os.path.exists(p) else '✗'} ion-dist/{rel}")
    return locale


def check_autodetect(app):
    """复刻主进程 Wet()：不看配置文件，纯按系统语言自动匹配。"""
    print("\n  [主进程-自动检测] Wet() 兜底路径")
    res = os.path.join(app, "resources")
    avail = {
        f[:-5]: True
        for f in os.listdir(res)
        if RE_FILENAME.search(f) and f.endswith(".json")
    }
    print(f"    可用语言: {', '.join(sorted(avail))}")

    preferred = ["zh-Hans-CN", "en-US"]
    print(f"    系统语言: {preferred}")
    for n in preferred:
        if avail.get(n):
            print(f"    ✓ 精确匹配 {n}")
            return n
        head = n.split("-")[0]
        hit = next((k for k in avail if k.startswith(head + "-")), None)
        if hit:
            print(f"    ✓ 前缀匹配 {n} -> {hit}")
            return hit
        print(f"    · 跳过 {n}")
    print("    ✗ 都匹配不上，会回落 en-US")
    return None


def check_shared2(app):
    print("\n  [渲染层核心] shared-2 Jf 核心支持表校验")
    v1_dir = os.path.join(app, "resources", "ion-dist", "assets", "v1")
    matches = glob.glob(os.path.join(v1_dir, "shared-2-*.js"))
    if not matches:
        print("    ✗ 找不到 shared-2-*.js")
        return False
    shared2_file = matches[0]
    with open(shared2_file, encoding="utf-8") as f:
        js = f.read()

    has_zh_cn = '"zh-CN"' in js and '"zh-Hans"' in js
    has_wp_bypass = 'e==="zh-CN"' in js or 'e==="zh-Hans"' in js
    if has_zh_cn:
        print("    ✓ shared-2 核心语言表已包含 zh-CN / zh-Hans")
    else:
        print("    ✗ shared-2 核心语言表未包含 zh-CN（会导致回退为 en-US）")

    if has_wp_bypass:
        print("    ✓ wp 降级守卫放行规则正常")
    else:
        print("    - wp 降级守卫未额外修补（若 meta 正常则不影响）")
    return has_zh_cn


def check_renderer(app, locale):
    print(f"\n  [渲染层守卫] index.html 元数据与 wp({locale!r}) 判定")
    idx = os.path.join(app, "resources", "ion-dist", "index.html")
    if not os.path.exists(idx):
        print("    ✗ 找不到 index.html")
        return False
    with open(idx, encoding="utf-8") as f:
        html = f.read()
    m = re.search(r'data-i18n-catalogs="([^"]*)"', html)
    if not m:
        print("    ✗ 没有 data-i18n-catalogs 属性")
        return False
    pat = re.compile(r"^([\w-]+):([\w-]+)\.([\w-]+)\.([\w-]+)$")
    published = [x.group(1) for x in (pat.match(p) for p in m.group(1).split(",")) if x]
    print(f"    meta 已发布语言: {', '.join(published)}")

    if locale in KNOWN and locale not in published:
        print(f"    ✗ 守卫会把它降级成 en-US")
        return False
    print(f"    ✓ 不会降级，保留 {locale}")
    return True


def main():
    print("=" * 60)
    print("           Claude Desktop 汉化生效性验证")
    print("=" * 60)
    app = find_app()
    print(f"\n安装目录: {app}")

    locales = []
    for cfg, tag in CONFIGS:
        r = check_main(app, cfg, tag)
        if r:
            locales.append(r)

    auto = check_autodetect(app)
    s2_ok = check_shared2(app)

    if not locales and not auto:
        print("\n✗ 配置和自动检测都拿不到中文")
        return 1

    targets = set(locales) | ({auto} if auto else set())
    render_ok = all(check_renderer(app, l) for l in targets)
    all_ok = render_ok and s2_ok

    print("\n" + "=" * 60)
    if all_ok:
        print("  ✓ 完整链路验证通过！Claude Desktop 界面将显示为简体中文。")
    else:
        print("  ✗ 存在问题，请按上方日志排查。")
    print("=" * 60)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
