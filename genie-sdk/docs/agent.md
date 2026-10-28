# 给 agent 的入口 —— 用代码驱动 Studio

> 这一页写给**模型/agent**（写给人看的是 [快速上手](../manual/commands/quickstart.html)）。
> 读它就够开始干活；字段级语义见随包手册（[manual/commands/](../manual/commands/index.html)
> 的 00–12 参考页与教程）。

前提：用户已经把 `genie studio` 跑起来了，浏览器里有一份**他正在看的模型**。
你的任务是改那一份，**不是另起一份**。

## 1. 接上

```python
from genie.studio.session_client import SessionClient

cli = SessionClient.discover()      # 读门牌（~/.genie/studio.json）
if cli is None:
    raise SystemExit("没有在跑的 Studio——让用户先启动 genie studio")
cli.health()                        # 不可达会抛 503，别静默继续
```

`discover()` 把 `owner` / `session_id` 一并带出来，后面所有调用都不用重给。
门牌里还有本机会话口令——**不需要**用户的 API key，会话已经持有了。

## 2. 看一眼现在的模型

```python
out = cli.output()
model = out["cfg"]        # 线格式 SimConfig（字段见手册参考页）
out["busy"]               # True = 有人在跑东西（exec/run/写模型）
out["phase"]              # exec|submit|queue|run|download|idle
out["job_id"]             # 最近一次作业
```

`cfg` 为 `None` / `{}` = 会话还没有模型（先 `replace` 一份或跑一段 `exec`）。

## 3. 改一处 —— 默认用最小的那个

```python
cli.patch([{"path": "mesh.dx", "value": 5e-8}])
cli.patch([{"verb": "append", "path": "monitor",
            "value": {"type": "time", "name": "t1", "field": "Ey"}}])
cli.patch([{"verb": "remove", "path": "geometry.grating"}])   # 名字，不是下标
```

- `verb` ∈ `set`（缺省）/ `append` / `remove`；细节见上文 §3。
- 数组那一段**可以是名字**（对象的 `name`）：改/删优先用名字。按下标是
  "读→算→写"，别人刚插了一个，你动到的就是别人。
- `append` 应用后的回执路径是**新元素的下标**（往 `monitor` 加一个，
  回执路径 `monitor.1`）；`remove` 删掉中间元素后，**后续下标自动前移**——
  删前记住的下标随即作废，别存着复用（要用名字）。
- `set` 沿路**自动创建缺失的中间节点**（下一段是数字建 list，否则建 dict），
  所以 `set` 可以直接写深路径；`append` / `remove` 只认**已存在**的数组，
  不会替你建。数组名解析失败时报错会列出该数组现有名字（前 8 个）——
  拿不准就照着报错里的名字写。
- **一批 op 全对或全错**：被拒时模型不动，报错点名第几条、哪个路径。
- 落地后的快照 `origin=patch` 带 `paths`=本次**实际变动的路径列表**，
  界面只刷新那几处，不整份回读；`origin=reject` 是这批被拒（模型没动），
  界面据此回滚——同 §7，别把本地那份当真。
- 不要为了改一个字段去 `replace` 整份模型，也不要"读整份 → 改 → 写回"
  （会盖掉别人同时做的改动）。改动的代价阶梯——**用最小那条原语**：

  | 原语 | 代价 |
  |---|---|
  | `patch` | 增量解析+校验（最廉） |
  | `replace` | 全量解析+校验 |
  | `exec(mode="build")` | 脚本执行+整体重建 |

写入是**异步**的：`patch` 立刻返回，改动稍后落地。等它：

```python
import time

def wait(cli, seq, tries=200):
    """等一次写入落地：cfg_seq 前进且不在忙。"""
    for _ in range(tries):
        out = cli.output()
        if out.get("cfg_seq") != seq and not out.get("busy"):
            return out
        time.sleep(0.05)
    raise RuntimeError("写入没落地——看 output()['lines'] 里的 error")

seq = cli.output()["cfg_seq"]
cli.patch([{"path": "domain.cfl", "value": 0.8}])
out = wait(cli, seq)      # 新的 cfg 就在 out["cfg"]
```

## 4. 跑

```python
cli.run()                       # 提交**当前模型**（不改模型）
```

要循环、扫参、按数据生成几何，就发一段代码。会话的命名空间里已经绑好
`f`（当前模型）、`genie`、`connect`、`run_model`：

```python
cli.exec("""
best = None
for p in [2e-7, 3e-7, 4e-7]:
    f.geometry[0].x_range = (0, p)
    jid = run_model()                 # 提交；别用 f.run()
    print(p, jid)
""", mode="run")
```

两种 `mode` 的差别（选错会很难查）：

| mode | 语义 | 用在哪 |
|---|---|---|
| `run`（缺省） | 在当前命名空间执行，改 `f` 就是改模型 | 循环、扫参、试探 |
| `build` | 在**新**命名空间执行，成功才整体提交 | 想"要么全成要么别动"的整段构造 |

## 5. 取结果

```python
job = cli.output()["job_id"]
cli.result_meta(job_id=job)                      # 有哪些监视器/字段
cli.result_series(job_id=job, mon="t1", field="Ey")
```

## 6. 别做这些

- **别自己起一个新会话**（`genie studio` / 新 session_id）：那是另一份模型，
  用户在浏览器里看不到。
- **别调 `connect()`**：会话已经有凭据，`connect()` 只会多开一个实例进合并集。
- **别用 `f.run()`**：一个实例只能提交一次，第二次会报错。用 `run_model()`。
- **别猜字段名**：服务端对未知字段一律 422。不确定就查手册参考页。
- **别吞错误**：`patch` 被拒、422、503 都要往上报，别重试到没反应。

## 7. 报错怎么读

| 现象 | 含义 | 怎么办 |
|---|---|---|
| `HTTP 422` + 字段名 | 值/形状不合法（或字段不存在） | 按字段改，查手册对应参考页 |
| `HTTP 503` 会话不可达 | Studio 没在跑 / 门牌过期 | 让用户重启，不要自己起服务 |
| 终端里 `(patch 失败: … 整批未提交)` | 这批 op 全被拒，模型没动 | 按点名的第几条修 |
| `origin=reject` 的快照 | 同上，这次是界面视角 | 别把本地那份当真 |

改完模型建议**看一眼 `cfg`** 再提交——你改的和用户看到的是同一份，
但你的读写之间，用户可能也动了一下。

## 8. 如果你是通过 MCP 接入（不能跑代码）

工具面就是上面那几条路的工具版，语义一一对应：

| 工具 | 干什么 | 对应本文 |
|---|---|---|
| `get_model` | 读当前模型 + 忙闲/作业 | §2 |
| `edit_model` | 一条 op 改一处（`set`/`append`/`remove`）；被拒如实报错 | §3 |
| `run_script` | 发一段脚本（循环/扫参/程序化）；`mode=build` 是事务 | §4 |
| `run_model` | 提交当前模型（不改模型） | §4 |
| `get_results` | 结果清单 / 曲线 | §5 |

（`check_script` 已随 G174 退役：自纠 = `run_script(mode="build")` 读
带行号的错误；"看模型不跑" = build 成功后 `get_model`。）

工具描述本身就是 prompt，先读它再调。写是异步的，但 `edit_model` /
`run_script` 会**等到落地才返回**，所以不用自己写轮询。

两条省轮次/省上下文的约定：

- `edit_model` 的回执只带改动的几处（`{路径: 改后的值}`），不刷整份模型；
  要看全貌用 `get_model`，大模型可以 `section="monitor"` 只取一节。
- `run_model` 提交失败会报 `ok=false`——别把它当成功然后一直等作业。
