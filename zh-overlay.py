#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
换内容方案：把 en-US 语言包的内容替换成中文。

为什么不改 locale：
  前几次尝试都是「改 config.json 的 locale + 注入 meta」，
  但应用会自己把 locale 改回 en-US，而且渲染层有守卫会降级。
  与其和它对抗，不如让它继续用 en-US —— 只是那份 en-US 的内容是中文。

这样还顺带满足「不影响模型」：
  发给服务端的语言标识仍然是 en-US，推理路径一个字节都没动。

可随时回滚：python zh-overlay.py --restore
"""

import argparse
import json
import os
import re
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(os.environ["LOCALAPPDATA"], "AnthropicClaude")
BACKUP = os.path.join(HERE, ".overlay-backup")

# 源文件在这里是 zh-Hans 命名，部署时改名成 en-US
# 第二列是相对 resources/ 的路径
TARGETS = [
    ("zh-Hans.json",         "ion-dist/i18n/en-US.json"),
    ("dynamic-zh-Hans.json", "ion-dist/i18n/dynamic/en-US.json"),
    ("shell-zh-Hans.json",   "en-US.json"),
]


def find_app():
    cands = []
    for p in os.listdir(ROOT):
        m = re.fullmatch(r"app-([\d.]+)", p)
        if m and os.path.isdir(os.path.join(ROOT, p, "resources")):
            cands.append(([int(x) for x in m.group(1).split(".")], p))
    if not cands:
        sys.exit(f"找不到安装目录: {ROOT}\\app-*")
    cands.sort()
    return os.path.join(ROOT, cands[-1][1])


def app_res(app):
    return os.path.join(app, "resources")


def check_icu(src, dst):
    """占位符必须一致，否则界面会显示成 {foo} 字面量。"""
    def groups(s):
        out, depth, start = [], 0, None
        for i, ch in enumerate(s):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start is not None:
                    out.append(s[start:i + 1])
                    start = None
        return out if depth == 0 else None

    def sig(s):
        g = groups(s)
        if g is None:
            return None
        out = []
        for x in g:
            m = re.match(r"^\{([\w.\-]+)", x)
            if m:
                out.append(m.group(1))
        return sorted(out)

    a, b = sig(src), sig(dst)
    return a is not None and b is not None and a == b


def do_install(app, dry):
    res = app_res(app)
    os.makedirs(BACKUP, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    print(f"\n安装目录: {app}\n")
    total = 0
    for src_name, dst_rel in TARGETS:
        src = os.path.join(HERE, src_name)
        dst = os.path.join(res, dst_rel.replace("/", os.sep))
        if not os.path.exists(src):
            print(f"  ✗ 缺源文件 {src_name}")
            continue
        if not os.path.exists(dst):
            print(f"  ✗ 目标不存在 {dst_rel}")
            continue

        zh = json.load(open(src, encoding="utf-8"))
        en = json.load(open(dst, encoding="utf-8"))

        # 只替换两边都有的键的「值」，不动键集合 —— 少一条就会显示原文
        merged, bad = {}, 0
        for k, v in en.items():
            if k in zh and isinstance(zh[k], str) and isinstance(v, str):
                if zh[k] != v and check_icu(v, zh[k]):
                    merged[k] = zh[k]
                    continue
                if zh[k] != v:
                    bad += 1
            merged[k] = v

        replaced = sum(1 for k in en if merged.get(k) != en.get(k))
        print(f"  {dst_rel}")
        print(f"      条目 {len(en)}，替换 {replaced}（占位符不符跳过 {bad}）")

        if dry:
            continue
        bdir = os.path.join(BACKUP, stamp)
        os.makedirs(os.path.dirname(os.path.join(bdir, dst_rel)), exist_ok=True)
        shutil.copy2(dst, os.path.join(bdir, dst_rel))
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, separators=(",", ":"))
        total += replaced
    if dry:
        print("\n[dry-run] 未落盘")
    else:
        print(f"\n完成，共替换 {total} 条。备份在 {os.path.relpath(BACKUP, HERE)}/{stamp}")
        print("重启 Claude 后生效。")


def do_restore(dry):
    if not os.path.isdir(BACKUP):
        sys.exit("没有备份")
    stamps = sorted(os.listdir(BACKUP))
    if not stamps:
        sys.exit("备份目录为空")
    stamp = stamps[-1]
    root = os.path.join(BACKUP, stamp)
    print(f"从备份 {stamp} 还原\n")
    n = 0
    for dirpath, _, files in os.walk(root):
        for fn in files:
            src = os.path.join(dirpath, fn)
            rel = os.path.relpath(src, root)
            # 备份里的相对路径就是 resources/ 下的路径
            dst = os.path.join(find_app(), "resources", rel)
            if dry:
                print(f"  [dry] {rel}")
                continue
            shutil.copy2(src, dst)
            print(f"  还原 {rel}")
            n += 1
    print(f"\n完成，共 {n} 个文件。重启 Claude 后生效。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--restore", action="store_true")
    a = ap.parse_args()
    print("=" * 56)
    print("  换内容方案（不动 locale）")
    print("=" * 56)
    if a.restore:
        do_restore(a.dry_run)
    else:
        do_install(find_app(), a.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
