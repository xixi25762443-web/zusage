#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""zusage - See how many tokens each ZCode project / dialog / Q&A turn burns.

Reads ZCode Desktop's built-in usage database (~/.zcode/cli/db/db.sqlite)
in read-only mode. No dependency, standard library only.

用法 / Usage:
  zusage list [N]           最近 N 个对话框的用量 (默认 15)
  zusage list <关键字>      按项目名/路径/标题筛选对话框
  zusage proj               各项目的总消耗
  zusage proj <关键字>      某个项目内各对话框的用量
  zusage turns <id前缀>     某个对话框中每一轮问答的用量
  zusage day [N]            最近 N 天每天的总量 (默认 10)

对话框 = 同一会话链 (含后台子任务/上下文续接段) 的合计。
关键字不分大小写, 匹配项目名、目录路径或对话标题, 如: zusage proj my-app

数据库路径可用环境变量 ZUSAGE_DB 覆盖。
若系统 Python 的 sqlite3 不可用, 可用 uv 运行: uv run --no-project python zusage.py
"""
import json
import os
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

__version__ = "0.1.0"

DB = os.environ.get("ZUSAGE_DB") or str(
    Path.home() / ".zcode" / "cli" / "db" / "db.sqlite"
)


# ---------- 显示宽度工具 (中文按 2 列计, 保证表头与数据对齐) ----------
def dw(s):
    return sum(2 if unicodedata.east_asian_width(c) in "FW" else 1 for c in s)


def trunc(s, w):
    """按显示宽度截断, 超出补 …"""
    if dw(s) <= w:
        return s
    out, width = "", 0
    for c in s:
        cw = 2 if unicodedata.east_asian_width(c) in "FW" else 1
        if width + cw > w - 1:
            return out + "…"
        out += c
        width += cw
    return out


def pl(s, w):  # 左对齐
    return pad(trunc(str(s), w), w)


def pr(s, w):  # 右对齐
    s = str(s)
    return " " * max(0, w - dw(trunc(s, w))) + trunc(s, w)


def pad(s, w):
    return s + " " * max(0, w - dw(s))


# ---------- 数字 / 时间格式 ----------
def w(n):
    """大数缩写: 754427 -> 75.4万"""
    if n is None:
        return "-"
    if n < 10000:
        return str(n)
    if n < 100000000:
        return f"{n / 10000:.1f}万"
    return f"{n / 100000000:.2f}亿"


def fmt(n):
    return f"{n:,}" if n is not None else "-"


def ts(ms):
    if not ms:
        return "-"
    return datetime.fromtimestamp(ms / 1000).strftime("%m-%d %H:%M")


def dur(ms):
    if not ms:
        return "-"
    s = int(ms / 1000)
    if s < 60:
        return f"{s}s"
    m, sec = divmod(s, 60)
    if m < 60:
        return f"{m}m{sec:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


# ---------- 项目识别 ----------
def _project_slug(p):
    """复现 ZCode 由目录路径生成 project_id 的规则 ( proj_<小写slug> )"""
    return "proj_" + re.sub(r"[^a-z0-9.]+", "-", str(p).lower()).strip("-")


# 默认工作区/杂项目录不算"项目" → 显示对话标题
MISC_PROJECT_IDS = {
    _project_slug(Path.home() / ".zcode" / "workspace" / "default"),
    _project_slug(Path.home() / "ZCodeProject"),
}


def proj_tag(project_id, directory):
    """返回项目标签前缀如 [my-app/web] ; 非项目返回 None"""
    if not directory or (project_id and project_id in MISC_PROJECT_IDS):
        return None
    parts = [p for p in directory.replace("/", "\\").split("\\") if p]
    if len(parts) >= 2 and len(parts[-2]) <= 20:
        return f"[{parts[-2]}/{parts[-1]}] "
    return f"[{parts[-1]}] " if parts else None


def connect():
    if not os.path.exists(DB):
        sys.exit(f"找不到 ZCode 用量数据库: {DB}\n(可用环境变量 ZUSAGE_DB 指定路径)")
    return sqlite3.connect(f"file:{DB.replace(os.sep, '/')}?mode=ro", uri=True)


# ---------- 会话家族 (一个对话框 = 根 + 全部子会话) ----------
def family_root_map(con):
    """session_id -> 所在对话框的根 session_id"""
    rows = con.execute(
        """
        WITH RECURSIVE fam(id, root) AS (
            SELECT id, id FROM session
            UNION ALL
            SELECT s.id, f.root FROM session s JOIN fam f ON s.parent_id = f.id
        )
        SELECT id, root FROM fam
        """
    ).fetchall()
    return dict(rows)


def family_aggregates(con, kw=None):
    """按对话框(会话家族)聚合用量; kw 筛选项目/目录/标题。
    返回 [(root_short, title, tag, turns, reqs, tin, tout, total, last_ms), ...]"""
    rootmap = family_root_map(con)
    per_sess = con.execute(
        """SELECT session_id, COUNT(*), SUM(model_request_count),
                  SUM(input_tokens), SUM(output_tokens), SUM(computed_total_tokens),
                  MAX(started_at)
           FROM turn_usage GROUP BY session_id"""
    ).fetchall()
    fam = {}
    for sid, turns, reqs, tin, tout, total, last in per_sess:
        r = rootmap.get(sid, sid)
        acc = fam.setdefault(r, [0, 0, 0, 0, 0, 0])
        acc[0] += turns
        acc[1] += reqs or 0
        acc[2] += tin or 0
        acc[3] += tout or 0
        acc[4] += total or 0
        acc[5] = max(acc[5], last or 0)
    out = []
    for root, acc in fam.items():
        info = con.execute(
            """SELECT substr(id,6,8), COALESCE(NULLIF(title,''),'(未命名)'),
                      directory, project_id
               FROM session WHERE id = ?""",
            (root,),
        ).fetchone()
        if not info:
            continue
        if kw:
            hay = " ".join(filter(None, [info[1], info[2], info[3]])).lower()
            if kw.lower() not in hay:
                continue
        out.append((info[0], info[1], proj_tag(info[3], info[2])) + tuple(acc))
    return out


def print_dialog_rows(rows, limit, order_by_total=False):
    rows = sorted(rows, key=lambda r: r[8], reverse=not order_by_total)[:limit]
    W = dict(id=8, time=11, turn=4, call=5, inp=9, out=9, total=10)
    head = (
        pr("ID", W["id"]) + "  " + pr("最后活跃", W["time"]) + "  "
        + pr("轮数", W["turn"]) + "  " + pr("调用", W["call"]) + "  "
        + pr("输入/万", W["inp"]) + "  " + pr("输出/万", W["out"]) + "  "
        + pr("总计/万", W["total"]) + "  项目/标题"
    )
    print(head)
    print("─" * dw(head))
    for sid, title, tag, turns, reqs, tin, tout, total, last in rows:
        label = trunc((tag or "") + title, 58)
        print(
            pr(sid, W["id"]) + "  " + pr(ts(last), W["time"]) + "  "
            + pr(turns, W["turn"]) + "  " + pr(reqs, W["call"]) + "  "
            + pr(w(tin), W["inp"]) + "  " + pr(w(tout), W["out"]) + "  "
            + pr(w(total), W["total"]) + "  " + label
        )
    return len(rows)


# ---------- 子命令 ----------
def cmd_list(args):
    kw = args[1] if len(args) > 1 and not args[1].isdigit() else None
    limit = int(args[1]) if len(args) > 1 and args[1].isdigit() else 15
    con = connect()
    rows = family_aggregates(con, kw)
    n = print_dialog_rows(rows, limit, order_by_total=bool(kw))
    tip = f"匹配 “{kw}” 的" if kw else ""
    print(f"\n共 {n} 个{tip}对话框 (含后台子任务/续接段); 看单轮明细: zusage turns <ID>")


def cmd_proj(args):
    kw = args[1] if len(args) > 1 else None
    con = connect()
    if kw:  # 项目内: 各对话框分账
        rows = family_aggregates(con, kw)
        if not rows:
            sys.exit(f"没有匹配 “{kw}” 的对话框 (可先 zusage proj 查看项目名)")
        n = print_dialog_rows(rows, 30, order_by_total=True)
        print(f"\n项目 “{kw}” 共 {n} 个对话框, 按消耗排序; 看某对话框每轮: zusage turns <ID>")
        return
    # 项目间: 总消耗排序
    rows = family_aggregates(con)
    groups = {}
    for sid, title, tag, turns, reqs, tin, tout, total, last in rows:
        g = groups.setdefault(tag or "杂项(非项目对话)", [0, 0, 0, 0, 0, 0])
        g[0] += 1
        g[1] += turns
        g[2] += reqs
        g[3] += tin
        g[4] += tout
        g[5] += total
    W = dict(name=36, n=4, turn=5, call=6, inp=9, out=9, total=10)
    head = (
        pl("项目", W["name"]) + "  " + pr("对话", W["n"]) + "  " + pr("轮数", W["turn"])
        + "  " + pr("调用", W["call"]) + "  " + pr("输入/万", W["inp"]) + "  "
        + pr("输出/万", W["out"]) + "  " + pr("总计/万", W["total"])
    )
    print(head)
    print("─" * dw(head))
    for name, (n, turns, reqs, tin, tout, total) in sorted(
        groups.items(), key=lambda kv: kv[1][5], reverse=True
    ):
        print(
            pl(name, W["name"]) + "  " + pr(n, W["n"]) + "  " + pr(turns, W["turn"])
            + "  " + pr(reqs, W["call"]) + "  " + pr(w(tin), W["inp"]) + "  "
            + pr(w(tout), W["out"]) + "  " + pr(w(total), W["total"])
        )
    print("\n看某项目内的各个对话框: zusage proj <项目名关键字>, 如 zusage proj my-app")


def fetch_previews(con, turns):
    previews = {}
    mids = [t[10] for t in turns if t[10]]
    if mids:
        qmarks = ",".join("?" * len(mids))
        for mid, data in con.execute(
            f"SELECT message_id, data FROM part WHERE message_id IN ({qmarks})", mids
        ):
            if mid in previews:
                continue
            try:
                p = json.loads(data)
            except ValueError:
                continue
            if p.get("type") == "text" and p.get("text"):
                previews[mid] = p["text"].split("\n")[0]
    return previews


def cmd_turns(prefix):
    con = connect()
    rootmap = family_root_map(con)
    hits = con.execute(
        "SELECT id FROM session WHERE id LIKE ?", (f"sess_{prefix}%",)
    ).fetchall()
    if not hits:
        sys.exit(f"找不到会话: {prefix}")
    if len(hits) > 1:
        print("前缀不唯一, 请选择:")
        for (sid,) in hits:
            print(" ", sid[6:14])
        sys.exit(1)
    members = [s for s, r in rootmap.items() if r == hits[0][0]]
    qmarks = ",".join("?" * len(members))
    turns = con.execute(
        f"""SELECT session_id, turn_id, model_request_count, input_tokens, output_tokens,
                   reasoning_tokens, cache_read_input_tokens, computed_total_tokens,
                   started_at, duration_ms, user_message_id
            FROM turn_usage WHERE session_id IN ({qmarks}) ORDER BY started_at""",
        members,
    ).fetchall()
    info = con.execute(
        """SELECT substr(id,6,8), COALESCE(NULLIF(title,''),'(未命名)'),
                  directory, project_id FROM session WHERE id = ?""",
        (hits[0][0],),
    ).fetchone()
    tag = proj_tag(info[3], info[2])
    multi = len({t[0] for t in turns}) > 1
    previews = fetch_previews(con, turns)
    W = dict(no=3, time=11, call=4, inp=11, out=8, total=11, dur=8)
    head = (
        pr("#", W["no"]) + "  " + pr("时间", W["time"]) + "  "
        + pr("调用", W["call"]) + "  " + pr("输入", W["inp"]) + "  "
        + pr("输出", W["out"]) + "  " + pr("总计", W["total"]) + "  "
        + pr("耗时", W["dur"]) + "  提问"
    )
    print(f"会话 {info[0]} · {trunc((tag or '') + info[1], 60)}\n")
    print(head)
    print("─" * dw(head))
    tsum = [0, 0, 0]
    prev_sess = None
    for i, (sess, tid, reqs, tin, tout, trea, tcache, total, st, durms, mid) in enumerate(turns, 1):
        if multi and sess != prev_sess:
            print(f"  ── 会话段 {sess[6:14]} ──")
            prev_sess = sess
        q = trunc(previews.get(mid, ""), 70)
        print(
            pr(i, W["no"]) + "  " + pr(ts(st), W["time"]) + "  "
            + pr(reqs, W["call"]) + "  " + pr(fmt(tin), W["inp"]) + "  "
            + pr(fmt(tout), W["out"]) + "  " + pr(fmt(total), W["total"]) + "  "
            + pr(dur(durms), W["dur"]) + "  " + q
        )
        tsum[0] += tin or 0
        tsum[1] += tout or 0
        tsum[2] += total or 0
    pct = (tsum[1] / tsum[2] * 100) if tsum[2] else 0
    print("─" * dw(head))
    print(
        f"合计 {len(turns)} 轮 · 输入 {fmt(tsum[0])} (含缓存读取) · 输出 {fmt(tsum[1])} ({pct:.0f}%) · 总计 {fmt(tsum[2])}"
    )


def cmd_day(limit):
    con = connect()
    rows = con.execute(
        """SELECT date(started_at/1000,'unixepoch','localtime'),
                  COUNT(*), SUM(model_request_count),
                  SUM(input_tokens), SUM(output_tokens), SUM(computed_total_tokens)
           FROM turn_usage GROUP BY 1 ORDER BY 1 DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    W = dict(d=10, turn=4, call=5, inp=9, out=9, total=10)
    head = (
        pr("日期", W["d"]) + "  " + pr("轮数", W["turn"]) + "  " + pr("调用", W["call"])
        + "  " + pr("输入/万", W["inp"]) + "  " + pr("输出/万", W["out"]) + "  "
        + pr("总计/万", W["total"])
    )
    print(head)
    print("─" * dw(head))
    for d, turns, reqs, tin, tout, total in rows:
        print(
            pr(d, W["d"]) + "  " + pr(turns, W["turn"]) + "  " + pr(reqs, W["call"])
            + "  " + pr(w(tin), W["inp"]) + "  " + pr(w(tout), W["out"]) + "  "
            + pr(w(total), W["total"])
        )


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = sys.argv[1:]
    cmd = args[0] if args else "list"
    if cmd in ("-h", "--help", "help"):
        print(__doc__)
    elif cmd in ("-V", "--version"):
        print(f"zusage {__version__}")
    elif cmd in ("list", "ls", "sessions"):
        cmd_list(args)
    elif cmd in ("proj", "p", "project"):
        cmd_proj(args)
    elif cmd in ("turns", "t", "session"):
        if len(args) < 2:
            sys.exit("用法: zusage turns <会话ID前缀>   (先用 zusage list 查 ID)")
        cmd_turns(args[1])
    elif cmd in ("day", "daily"):
        cmd_day(int(args[1]) if len(args) > 1 else 10)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
