#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
构建 Claude Desktop 外壳中文目录 (shell-zh-Hans.json)

外壳目录 = 主进程用的 703 条文案（托盘菜单 / 原生对话框 / 系统通知）。
官方没有发布 zh-Hans 的这一层，所以：
  1. 能对上官方在线渲染层译文的，直接复用（高保真）
  2. 剩下的 507 条走本地网关翻译

输入:
  <app>/resources/en-US.json        官方英文外壳目录
  zh-Hans.json                      官方在线中文渲染层目录（用于比对复用）
输出:
  shell-zh-Hans.json                外壳中文目录（703 条）
"""

import json
import os
import re
import sys
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "shell-zh-Hans.json")
CACHE = os.path.join(HERE, ".translate-cache.json")

BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "http://127.0.0.1:8902")
API_KEY = os.environ.get("TRANSLATE_API_KEY", "sk-cliproxy-local-cde11995c72e43fe")
MODEL = os.environ.get("TRANSLATE_MODEL", "claude-sonnet-4-6")

BATCH = 20
WORKERS = 4


# ---------- 官方渲染层目录（复用已有译文） ----------

def norm(s: str) -> str:
    """归一化：统一引号/破折号、压空白、小写，用于跨目录比对同一句话。"""
    s = (s.replace("’", "'").replace("‘", "'")
          .replace("“", '"').replace("”", '"')
          .replace("—", "-").replace("–", "-"))
    return re.sub(r"\s+", " ", s).strip().lower()


# ---------- 占位符 / ICU 结构校验 ----------

def top_level_groups(s: str):
    """取出顶层 {...} 组（含嵌套），用于校验 ICU 结构没被翻译破坏。"""
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
    return out


def structure_ok(src: str, dst: str) -> bool:
    """ICU 结构必须完整保留：占位符名与数量一致，复数分支不能丢。"""
    if not dst.strip():
        return False

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
        # 括号不配对，说明结构被改坏
        return None if depth != 0 else out

    ga, gb = groups(src), groups(dst)
    if ga is None or gb is None:
        return False

    def sig(groups_):
        """把占位符规约成可比对的签名：{name} / {name, plural}，忽略内部文案。"""
        out = []
        for g in groups_:
            m = re.match(r"^\{([\w.\-]+)\s*(?:,\s*(\w+))?", g)
            if not m:
                return None
            name, kind = m.group(1), m.group(2)
            # 复数/选择结构内部还嵌套着分支组，需递归进去看
            if kind in ("plural", "select", "selectordinal"):
                inner = re.findall(r"\{([^{}]*)\}", g)
                nested = []
                for s in inner:
                    nested.extend(re.findall(r"\{([\w.\-]+)\s*(?:,\s*(\w+))?", s))
                out.append((name, kind, tuple(sorted(x[0] for x in nested))))
            else:
                out.append((name, kind, ()))
        return tuple(sorted(out, key=repr))

    sa, sb = sig(ga), sig(gb)
    if sa is None or sb is None:
        return False
    # 顶层占位符集合一致；若原文有 plural，译文也必须保留对应 plural 结构
    if [g[0] for g in sa] != [g[0] for g in sb]:
        return False
    for x, y in zip(sa, sb):
        if x[1] != y[1]:
            return False
    return True


# ---------- 翻译 ----------

def call_model(prompt: str) -> str:
    # 输出长度随输入规模走，避免长文案批次撞上 max_tokens
    max_tokens = max(4000, min(16000, len(prompt) * 3))
    body = json.dumps({
        "model": MODEL,
        "max_tokens": max_tokens,
        "temperature": 0,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        BASE_URL.rstrip("/") + "/v1/messages",
        data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": API_KEY,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.load(resp)
    return "".join(b.get("text", "") for b in data.get("content", []))


SYS = (
    "你是软件界面本地化译者。输入是若干行「编号<|>英文界面文案」，"
    "请把每行文案译成简体中文。\n"
    "输出规则（必须严格遵守）：\n"
    "1. 每行输出「编号<|>译文」，编号和分隔符原样照抄，行数与原输入完全一致，顺序不变。\n"
    "2. 不要输出任何解释、注释、代码块标记或空行。\n"
    "3. 不要给译文加引号，不要转义任何字符——原文里的引号原样保留。\n"
    "4. 保持 {placeholder}、{count, plural, one {...} other {...}} 等 ICU 结构不变，"
    "只翻译其中自然语言部分；占位符名字不能改动。\n"
    "5. 保持 <tag> 标签、\\n、网址、快捷键（如 Ctrl+S）、文件名原样；"
    "结尾的省略号/冒号/问号风格保持一致。\n"
    "6. 术语统一：Settings=设置，Extension=扩展，Session=会话，Workspace=工作区，"
    "Device=设备，Sign in=登录，Update=更新，Folder=文件夹，Account=账户。\n"
    "7. 简洁自然，符合中文桌面软件习惯，不要逐字硬译。"
)

SEP = "<|>"
# 换行与分隔符会破坏行协议，发送前转义，收回来还原
_ESC = [("\r\n", "\\r\\n"), ("\n", "\\n"), ("<|>", "<\\|>")]


def esc(s: str) -> str:
    for a, b in _ESC:
        s = s.replace(a, b)
    return s


def unesc(s: str) -> str:
    for a, b in reversed(_ESC):
        s = s.replace(b, a)
    return s


def translate_batch(items):
    """items: [(key, text)] -> {key: 译文}。用消息键做行标识，比序号更稳。"""
    keys = [k for k, _ in items]
    src = dict(items)
    listing = "\n".join(f"{esc(k)}{SEP}{esc(s)}" for k, s in items)
    raw = call_model(f"{SYS}\n\n输入：\n{listing}")

    result = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or SEP not in line:
            continue
        head, _, body = line.partition(SEP)
        head = unesc(head.strip().strip("`").strip())
        if head not in src:
            continue
        text = unesc(body.strip())
        if text and structure_ok(src[head], text):
            result[head] = text

    # 模型偶尔改回用序号作答，兜底认一下
    if not result:
        by_num = {i: k for i, k in enumerate(keys)}
        for line in raw.splitlines():
            head, sep, body = line.strip().partition(SEP)
            if not sep:
                continue
            h = head.strip().strip("`").lstrip("#-* ").strip()
            if not h.isdigit() or int(h) not in by_num:
                continue
            k = by_num[int(h)]
            text = unesc(body.strip())
            if text and structure_ok(src[k], text):
                result[k] = text
    return result


def main():
    appdir = sys.argv[1] if len(sys.argv) > 1 else None
    if not appdir:
        print("用法: build-shell-catalog.py <app-XXXX 目录>", file=sys.stderr)
        return 2

    src = os.path.join(appdir, "resources", "en-US.json")
    with open(src, encoding="utf-8") as f:
        shell = json.load(f)

    # 官方渲染层英文/中文目录，用于复用
    renderer_en = os.path.join(appdir, "resources", "ion-dist", "i18n", "en-US.json")
    with open(renderer_en, encoding="utf-8") as f:
        en_r = json.load(f)
    with open(os.path.join(HERE, "zh-Hans.json"), encoding="utf-8") as f:
        zh_r = json.load(f)
    official = {}
    for k, v in en_r.items():
        if isinstance(v, str) and isinstance(zh_r.get(k), str) and zh_r[k] != v:
            official.setdefault(norm(v), zh_r[k])

    cache = {}
    if os.path.exists(CACHE):
        with open(CACHE, encoding="utf-8") as f:
            cache = json.load(f)

    out, todo = {}, []
    for k, v in shell.items():
        if not isinstance(v, str):
            continue
        if k in cache:
            out[k] = cache[k]
        elif norm(v) in official:
            out[k] = official[norm(v)]
            cache[k] = out[k]
        else:
            todo.append((k, v))

    print(f"外壳总数 {sum(1 for v in shell.values() if isinstance(v, str))} 条")
    print(f"复用官方译文 {sum(1 for k in out)} 条")
    print(f"需翻译 {len(todo)} 条")

    if todo:
        batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
        print(f"分 {len(batches)} 批（每批 {BATCH} 条，并发 {WORKERS}）")

        def work(batch):
            out = {}
            # 长文案批次容易整体超限，失败就对半拆开重试，直到能过
            def run(chunk, depth=0):
                if not chunk:
                    return
                for att in range(2):
                    try:
                        res = translate_batch(chunk)
                        if res:
                            out.update(res)
                            return
                    except Exception as e:  # noqa: BLE001
                        if att == 1 and depth < 4:
                            mid = len(chunk) // 2
                            run(chunk[:mid], depth + 1)
                            run(chunk[mid:], depth + 1)
                            return
                        if att == 1:
                            print(f"  批次失败({len(chunk)}条): {e}", file=sys.stderr)
                if depth < 4 and len(chunk) > 1:
                    mid = len(chunk) // 2
                    run(chunk[:mid], depth + 1)
                    run(chunk[mid:], depth + 1)
            run(batch)
            return batch, out

        done = 0
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            for batch, res in ex.map(work, batches):
                done += 1
                for k, v in res.items():
                    cache[k] = v
                    out[k] = v
                print(f"  批次 {done}/{len(batches)} 完成 (+{len(res)})")
                with open(CACHE, "w", encoding="utf-8") as f:
                    json.dump(cache, f, ensure_ascii=False, indent=1)

    missing = [k for k, v in shell.items() if isinstance(v, str) and k not in out]
    if missing:
        print(f"警告: {len(missing)} 条未翻译，回退英文")
        for k in missing:
            out[k] = shell[k]

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    translated = sum(1 for k in out if out[k] != shell.get(k))
    print(f"\n已写出 {OUT}")
    print(f"  {len(out)} 条，其中已汉化 {translated} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
