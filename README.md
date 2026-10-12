# zusage

> 看看 ZCode 每个项目、每个对话框、每一轮问答，到底烧了多少 token。
>
> See how many tokens every ZCode project / dialog / Q&A turn burns.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.8+-blue)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)
![Deps](https://img.shields.io/badge/依赖-零-orange)

单文件命令行工具，**零依赖**（纯 Python 标准库），以**只读**方式读取 ZCode Desktop 自带的用量统计数据库，按 项目 → 对话框 → 问答轮次 三级展示 token 消耗。数字与 ZCode 内部记账同源，不是估算。

## 示例 / Demo

**`zusage proj my-app`** — 看某个项目里各个对话框的分账（按消耗排序）：

```text
      ID     最后活跃  轮数   调用    输入/万    输出/万     总计/万  项目/标题
───────────────────────────────────────────────────────────────────────────────
a1b2c3d4  03-15 14:22     1     28    134.0万      6.9万     140.9万  [my-app/web] 修复用户登录态丢失的问题
e5f6a7b8  03-15 14:38     1      4      7.3万       1968       7.5万  [my-app/web] 重构数据库连接池
c9d0e1f2  03-15 15:09     2      2      2.4万       2785       2.7万  [my-app/web] 这个接口的超时时间在哪里改？
b3c4d5e6  03-15 16:06     6     80    477.8万     26.6万     504.3万  [my-app/web] 生成接口文档

项目 "my-app" 共 4 个对话框, 按消耗排序; 看某对话框每轮: zusage turns <ID>
```

**`zusage turns e5f6a7b8`** — 看某个对话框里每一轮问答各耗多少（显示你的原话提问）：

```text
会话 e5f6a7b8 · [my-app/web] 重构数据库连接池

  #         时间  调用         输入      输出         总计      耗时  提问
──────────────────────────────────────────────────────────────────────────
  1  03-15 14:38     4       73,097     1,968       75,065     1m21s  把数据库连接池抽成公共模块，注意别改现有行为
──────────────────────────────────────────────────────────────────────────
合计 1 轮 · 输入 73,097 (含缓存读取) · 输出 1,968 (3%) · 总计 75,065
```

**`zusage proj`** — 所有项目按总消耗排名；**`zusage day`** — 按天汇总。

## 安装 / Install

只需要 Python 3.8+（Windows/macOS/Linux 均可）：

```bash
git clone https://github.com/<你的用户名>/zusage.git
cd zusage
python zusage.py list          # 直接就能跑
```

建议设个命令别名（任选其一）：

```powershell
# PowerShell — 加进 $PROFILE
function zusage { python & "路径\zusage.py" @args }
```

```bash
# Git Bash / Linux / macOS — 加进 ~/.bashrc
alias zusage='python "/路径/zusage.py"'
```

如果系统 Python 的 sqlite3 模块损坏（某些 Anaconda 环境如此），用 [uv](https://docs.astral.sh/uv/) 跑：

```bash
uv run --no-project python zusage.py list
```

## 命令 / Commands

| 命令 | 作用 |
|---|---|
| `zusage list [N]` | 最近 N 个对话框的用量（默认 15） |
| `zusage list <关键字>` | 按项目名/路径/标题筛选对话框 |
| `zusage proj` | 所有项目的总消耗排名 |
| `zusage proj <关键字>` | 某个项目内各对话框的分账，按消耗排序 |
| `zusage turns <ID前缀>` | 某个对话框每一轮问答的精确消耗 |
| `zusage day [N]` | 最近 N 天的每日总量 |
| `zusage help` / `--version` | 帮助 / 版本 |

- **关键字**不分大小写，匹配项目名、目录路径或对话标题，如 `zusage proj my-app`。
- **一个对话框**按整条会话链合计（含后台子任务、上下文续接段）；若跨多个会话段，`turns` 视图会用 `── 会话段 xxx ──` 分隔标注。
- 大数单位为**万**（`75.4万` = 754,000），对齐按中文显示宽度计算，表头不会错位。

## 工作原理 / How it works

ZCode Desktop 会在本地维护一份用量统计 SQLite 库（默认 `~/.zcode/cli/db/db.sqlite`），内含：

| 表 | 内容 |
|---|---|
| `session` | 会话（对话框）：标题、目录、项目、父子关系 |
| `turn_usage` | 每轮问答的用量：模型调用次数、输入/输出/缓存/总计 token |
| `model_usage` | 每次模型调用的明细（含来源：主对话/压缩/标题生成） |

zusage 只以 **`mode=ro` 只读 URI** 打开它，不写入任何东西，对话进行中也可随时查询。上下文压缩（compact）消耗的 token 同样记录在账，不会被漏算。数据库路径不同时可用环境变量 `ZUSAGE_DB` 覆盖。

## 常见问题 / FAQ

**为什么"输入"远大于"输出"？**
"输入"包含缓存读取部分（计费远低于普通输入）。Agent 式工作流每轮都要携带完整上下文，输入占比高是正常的。`turns` 视图底部会给出精确的输入/输出拆分。

**数字会和压缩后的对不上吗？**
不会。上下文压缩只是缩短后续对话的上下文，此前每一轮的用量在发生时就已逐次累加，压缩调用本身的消耗也在账内（已与 `model_usage` 明细核对一致）。

**非项目对话怎么区分？**
默认工作区（`~/.zcode/workspace/default`）和 `~/ZCodeProject` 下的对话视为杂项，直接显示对话标题；其余显示 `[项目名] + 对话标题`。

**这是官方工具吗？**
不是。数据是 ZCode 自己记录的（所以数字可信），但本工具是社区实现，数据库结构并未官方文档化，ZCode 大版本更新后表结构若变化，可能需要小改适配。

## 适用范围 / Compatibility

在 **Windows + ZCode Desktop** 上开发验证；macOS/Linux 路径一致（`~/.zcode/cli/db/db.sqlite`），理论上直接可用，欢迎提 issue 反馈。

## License

[MIT](LICENSE)
