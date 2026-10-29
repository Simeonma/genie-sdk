"""sdk/genie/studio/kernel_worker — GUI Terminal 的常驻会话进程（真 SDK 执行面）。

站长裁决（2026-10-10，terminal 战役）：主区下半是 terminal；它背后是
一个**常驻** Python 进程，装真 genie SDK、连网关、带登录用户 key——
``f.run(1000)`` 真提交，job/结果操作与用户在自己电脑跑 SDK 同权同险。

协议（stdin/stdout 各一行一个 JSON；**写模型只有 patch/replace 两个原语**）：
  ← {"op":"attach","url":…,"key":…}        绑定网关+key，预置命名空间
  ← {"op":"exec","id":N,"code":…,"mode":…} 整段 exec（mode=run 在当前命名空间
                                            执行；build 在新命名空间执行，
                                            成功才原子提交）
  ← {"op":"run","id":N,"gpu":…}            提交当前模型（**不改模型**）
  ← {"op":"patch","id":N,"ops":[…]}        模型路径级增量（表单/树/工具条/
                                            agent）。一条 op =
                                            {path, value, verb?}，
                                            verb ∈ set|append|remove
                                            （缺省 set）。一批 op 全对或全错。
  ← {"op":"replace","id":N,"model":{…}}    整份替换（导入/搬运）
  → {"ev":"out","text":"…"[,"kind":…]}     捕获/语义发射（环条目结构化
                                            2026-10-11：kind ∈ note/error
                                            标注语义发射点；缺省 = stdout
                                            原样 chunk。text 逐字原文，
                                            kind 只是注解；时间戳由父层
                                            _emit 统一打，worker 不带）
  → {"ev":"cfg","cfg":{…},"origin":…}      会话 f 的 SimConfig 快照 + 来源标记
  → {"ev":"done","id":N,"ok":bool,"err":…} 纯 busy 信号（err 供 API/agent
                                            消费；terminal 渲染唯一走 out
                                            流——禁止把 done.err 渲染进环）
  → {"ev":"ack","id":-1,"ok":bool,"err":…}  控制面回执（attach/bind 的结果）：
                                            **不是 op 完成**，父层不得据此清忙

信任契约：这是用户 key 的**真执行面**——G174 后唯一执行引擎，无沙箱、
无 dry 面（曾经的 preview 零网络轨已随同裁决退役）；卡死由父层 kill+重生
（B2：输出历史跨重生保留在父层环落盘里，会话 f 由本进程新生时读
session_state.json 自动重放，父层会在 terminal 注明）。**没有本地墙钟**
（R41 定案 A+，2026-10-29）：等待时长只由两个真实机制管——用户停止
（A2）与服务端作业超时；本地 `signal.alarm` 整块机制退役（它曾误杀
"跑一个 30 分钟仿真"这类正常任务，且造成 Windows/Linux 行为分叉）。
停止（A2「长任务必须能掐」）= 父层发 SIGINT：exec 期 → StopExec 掐断
（回滚/done 照常，后端孤儿作业就地 cancel）；空闲期 → 忽略（仅提示
一行），worker 永不因 SIGINT 死亡。
"""
from __future__ import annotations

import io
import json
import os
import signal
import sys
import threading
import traceback
from pathlib import Path

_exec_busy = False   # exec 进行中标志（SIGINT handler 的状态感知依据；
                     # 进入 exec 置 True、finally 复位——模块级：handler
                     # 在 main() 里定义也要摸得到）
_stop_requested = False   # 停止收尾进行中（防二次 SIGINT 在 except 块内
                          # 嵌套 raise StopExec 冲出收尾链炸掉 worker）


class StopExec(BaseException):
    """用户停止（A2「长任务必须能掐」）——勿在用户代码语义中复用。

    派生自 BaseException 而非 Exception：与 KeyboardInterrupt 同理，
    用户代码的 ``except Exception`` 不得吞掉停止信号。"""


def _request_stop() -> str:
    """停止请求状态机（R41 补锁：从信号 handler 提出，跨平台可直测）。

    三态返回：
    - ``"stop"`` 语义 = **抛 StopExec**（exec 期首次请求）——由调用方
      （信号 handler）让异常冲出用户代码，被 exec 壳捕获后走收尾；
    - ``"silent"`` = 收尾中二次请求：静默吞掉（再无异常，否则从
      except 块内冲出收尾链会炸掉 worker）；
    - ``"idle"`` = 空闲期请求：不打断任何东西（SIGINT 默认处置 =
      KeyboardInterrupt 冲出 main() = worker 死亡，是必须拆掉的地雷）。

    由 ``signal.SIGINT``（POSIX）与 ``signal.SIGBREAK``（Windows
    CTRL_BREAK_EVENT）共用（G33）。
    """
    global _stop_requested
    if _exec_busy:
        if not _stop_requested:
            _stop_requested = True       # 本段 exec 只认第一次停止
            raise StopExec("用户停止")
        return "silent"
    return "idle"


class _Out:
    """exec 期间的 stdout/stderr 捕获：每个 write 实时回传父层（terminal
    滚动输出），f.run 的轮询进度 therefore 逐条滚出来。flush 为 no-op。"""

    def __init__(self, emit):
        self._emit = emit

    def write(self, t):
        if t:
            self._emit({"ev": "out", "text": t})
        return len(t)

    def flush(self):
        pass


def main(readline=None, emit_fn=None, expose=None) -> None:
    global _exec_busy, _stop_requested
    # 运行时缝（LEDGER G80）：readline / emit_fn 可注入——本机缺省 =
    # stdin/stdout 管道（子进程形态），网页 = 同进程队列。
    readline = readline or sys.stdin.readline
    real_out = sys.stdout            # IPC 通道本体（exec 期间被重定向走）
    # IPC 通道一律 UTF-8：Windows 控制台/管道缺省 ANSI 代码页（GBK），
    # emit 里的 emoji（🧹/⏱/✓）会 UnicodeEncodeError 炸 worker。
    if hasattr(real_out, "reconfigure"):
        real_out.reconfigure(encoding="utf-8")
    import genie                     # 真 SDK（宿主 venv editable 安装）

    _emit_lock = threading.Lock()    # G168/R34：IPC 行完整性（见 emit）

    def emit(obj):
        if emit_fn is not None:      # 同进程形态：直接回调，不碰 stdout
            emit_fn(obj)
            return
        # G168/R34（S5 收口）：sweep 并行点线程 / 停止收尸线程会与主线程
        # 并发 emit——单条 write 内部虽各持锁，跨 write 的 flush 交错仍
        # 可能拆行；IPC 是"一行一 JSON"协议，出口处加锁保行完整。
        with _emit_lock:
            real_out.write(json.dumps(obj, ensure_ascii=False,
                                      default=repr) + "\n")
            real_out.flush()

    ns: dict = {}
    sims: list = []          # 会话期间创建的全部 Simulation（grating 式
                             # 脚本每次 connect 开新实例——多实例按
                             # model_merge.merge_configs 合并成一份模型）
    sim = None
    sess_url = ""
    sess_key = ""
    first_connect_note = True
    restored: list = []      # B2：本次新生由落盘重放出来的实例（bind 判据）
    real_connect = genie._connect_direct   # 内部直连（公开 connect 已零参化）
    # B2 会话态落盘路径：管理器经 GENIE_STATE_FILE 显式传递（系统文件
    # 住系统空间，2026-10-13 搬家定案）；缺省回退 cwd 仅为独立调试形态
    # （_spawn_and_exec 等直驱 harness）。启动即解析为绝对路径——用户
    # 代码里 os.chdir 不影响恢复快照的读写落点。
    state_path = Path(os.environ.get("GENIE_STATE_FILE")
                      or Path.cwd() / "session_state.json")
    # 工作区锚（2026-10-20 结果配额修复）：启动即定格为绝对路径——
    # 结果实际由 _auto_save_result 落 **worker cwd** 的 genie_results/
    # （$GENIE_RESULT_DIR 未设时），且用户代码可 os.chdir，故清理目标
    # 只能锚定启动 cwd，不能锚定 state 目录（旧 bug：目标指向
    # session/<owner>/genie_results，该目录从未存在 → 配额从未生效）。
    workspace = Path.cwd()

    # session_connect 连出来的实例落这里。**build（脚本运行）期间临时改道**
    # 到一个 build 私有的列表——脚本建的实例在成功之前不属于会话（见 exec）。
    sink = {"sims": sims}

    def _new_sim():
        """新建一个会话实例（不自动入账——调用方决定落哪个 sink）。

        零网关形态（离线/直驱 harness）用 client=None：connect("") 会抛
        ValueError，空串还会回落 GENIE_URL（串网关风险），故显式分支。
        """
        s = (real_connect(sess_url, sess_key) if sess_url
             else genie.Simulation(client=None))
        # G148 腿 A（R24c 批 4）：会话内 f.visualize → ev:plot 事件 →
        # GUI Plot 面板自动开卡。实例属性遮蔽 Simulation.visualize 的
        # class 面（组合根未接线时的报错指路版本）——同 session_connect
        # 的接管模式。
        def _sim_visualize(mon, field=None, _s=s):
            emit({"ev": "out", "kind": "plot",
                  "data": {"mon": str(mon), "field": field,
                           "job_id": _s._job_id}})
        s.visualize = _sim_visualize
        return s

    # R24c（G148 批 4 同款接管模式）：sweep 的点实例走 _new_sim 工厂——
    # 会话凭据 + visualize 注入随点免费；点实例不进 sink（非会话模型）。
    # G168/R34（站长裁"汇总为王，点级降噪"）：sweep 点额外打标
    # ``_sweep_point``——run 补丁据此静默单作业级播报（提交/排队/进度/
    # job-done），终端只收每点一行终态 + 扫描级汇总（_on_sweep_event）。
    # 只包工厂注册这一处：_new_sim 另有 6 个调用点（replay/replace/
    # run_model 等），那些是普通会话实例，不得降噪。
    from genie.core import simulate as _sim_mod
    from genie.core import sweep as _sweep_mod

    def _sweep_point_sim():
        s = _new_sim()
        s._sweep_point = True
        return s

    _sim_mod.hooks["sweep_sim_factory"] = _sweep_point_sim

    # sweep 事件面（G168/R34）：每点一行终态（可数 = "跑完的有多少个"）
    # + sweep-progress 事件喂 chip（X/N · 失败数）；终态一行汇总（失败
    # 点点名 = 诊断入口——低频信息走终态行，不占常驻显示；弹窗/逐点
    # 进度条裁决不做，等 G4a GUI 扫参半边一起设计）。
    _sweep_final_seen: set = set()
    _sweep_bad_pts: dict = {}   # sid → [(点号, job_id)]（汇总行点名用）

    def _on_sweep_event(ev) -> None:
        if not isinstance(ev, dict):
            return
        et = ev.get("type")
        sid = ev.get("sweep_id") or ""
        total = ev.get("total")
        if et == "point":
            i, st = ev.get("idx"), ev.get("state") or "?"
            jid = ev.get("job_id") or ""
            n = (i + 1) if isinstance(i, int) else "?"
            if st == "succeeded":
                emit({"ev": "out", "kind": "job",
                      "text": f"✓ 点 {n}/{total} 完成（{jid}）\n"})
            elif st == "canceled":
                emit({"ev": "out", "kind": "job",
                      "text": f"· 点 {n}/{total} 已取消（{jid}）\n"})
            else:
                detail = str(ev.get("detail") or "").strip()
                emit({"ev": "out", "kind": "job",
                      "text": f"✗ 点 {n}/{total} 失败（{jid or '未提交'}）"
                              + (f"：{detail}\n" if detail else "\n")})
                _sweep_bad_pts.setdefault(sid, []).append((n, jid))
            emit({"ev": "out", "kind": "sweep-progress", "text": "",
                  "data": {"sweep_id": sid, "state": "running",
                           "total": total,
                           "rollup": ev.get("rollup") or {}}})
        elif et == "sweep_done":
            if sid in _sweep_final_seen:
                return          # cancel 后重入 wait 会二次 _finalize
            _sweep_final_seen.add(sid)
            r = ev.get("rollup") or {}
            bad = _sweep_bad_pts.pop(sid, [])
            named = "（" + "、".join(
                f"点 {n}{' ' + j if j else ''}" for n, j in bad[:4]
            ) + ("…" if len(bad) > 4 else "") + ")" if bad else ""
            ok_n, bad_n = r.get("succeeded", 0), sum(
                v for k, v in r.items() if k != "succeeded")
            fail_n = sum(v for k, v in r.items()
                         if k not in ("succeeded", "canceled"))
            if ev.get("state") == "canceled":
                emit({"ev": "out", "kind": "job",
                      "text": f"· 扫描已取消：{ok_n} 成功 · {bad_n} 未了结"
                              f"{named}\n"})
            elif ev.get("state") == "failed":
                # 中止路（环境级错误）不把"没跑的点"算成失败——它们没跑。
                text = f"✗ 扫描失败：{ok_n} 成功"
                if fail_n:
                    text += f" · {fail_n} 失败"
                if r.get("canceled"):
                    text += f" · {r['canceled']} 未运行"
                emit({"ev": "out", "kind": "error",
                      "text": text + f"{named}\n"})
            else:
                emit({"ev": "out", "kind": "job",
                      "text": f"✓ 扫描完成：{ok_n} 成功"
                              + (f" · {bad_n} 失败{named}\n" if bad_n else "\n")})
            emit({"ev": "out", "kind": "sweep-progress", "text": "",
                  "data": {"sweep_id": sid, "state": ev.get("state"),
                           "total": total, "rollup": r}})
            # 结果态切换平价（G168/R34）：修前每点的 job-done 顺带把 GUI
            # 切进结果态；降噪后由扫描终态一次性接手——最后成功点的
            # job-done 恰一条（term.js 由此记 __lastJobId + enterResult）。
            if ev.get("state") == "done" and ev.get("last_job_id"):
                emit({"ev": "out", "kind": "job-done", "text": "",
                      "data": {"job_id": ev["last_job_id"], "ok": True}})

    _sweep_mod.hooks["on_event"] = _on_sweep_event

    def _insts_of(namespace, sims_list) -> list:
        """某命名空间里"算数的实例全集"：connect 出来的 + 直建的 ns['f']。

        脚本可以 `f = Simulation(client=None)` 绕开 connect，所以不能只看
        sims_list；这是"提交/快照的唯一口径"。
        """
        out = [s for s in sims_list if s is not None]
        f = namespace.get("f") if namespace else None
        if f is not None and hasattr(f, "to_config") \
                and all(s is not f for s in out):
            out.append(f)
        return out

    def _namespace(f=None) -> dict:
        """会话命名空间——用户代码能看见的名字**只有这四个**。

        `run_model()` 是"提交当前模型"（= 工具栏 Run / POST /api/run）：
        终端里 `f.run()` 一个实例只能提交一次（SDK 契约），要再跑一次当前
        模型就用它——它不动 `f`。
        """
        d = {"__name__": "__console__", "genie": genie,
             "connect": session_connect, "run_model": _session_run}
        if f is not None:
            d["f"] = f
        return d

    def _merged(insts) -> dict:
        """若干实例 → 一份合并后的线格式模型（提交/快照唯一取值口径）。"""
        if not insts:
            return {}
        cfgs = [s.to_config() for s in insts]
        # R24（S1）：单实例也过 nan_to_none——JSON 安全化与实例数无关
        # （修前 len==1 直通原文，用户脚本算出的 nan 会以裸 NaN 字面量
        # 进 emit 的 json.dumps，浏览器 JSON.parse 丢整条 cfg 事件）。
        # R28 P3：run 只从实际 run 过的实例取末位（ran 判据 = _job_id
        # 非空）——修前未 run 实例的 dense 默认值静默 clobber 前面实例
        # 的显式 run（gpu/timeout_s）。
        # G174/R33：合并函数自 core/preview 搬至本层 model_merge
        # （preview 引擎退役，绝对导入——本文件无包上下文）。
        from genie.studio.model_merge import merge_configs, nan_to_none
        ran = [getattr(s, "_job_id", None) is not None for s in insts]
        return nan_to_none(merge_configs(cfgs, ran))

    def current_config() -> dict:
        """会话当前模型（合并后的线格式 dict）。"""
        return _merged(session_insts())

    def _index_of(node: list, seg: str) -> int:
        """数组段 → 下标。**数字当下标，其它当名字**（name 字段）。

        有了这条，路径可以写成 ``geometry.grating.priority`` / 删
        ``geometry.grating``——不用先读全表数下标。按下标删东西是
        "读→算→写"，别人刚插了一个，你删掉的就是别人的。
        """
        s = str(seg)
        try:
            return int(s)
        except ValueError:
            pass
        for i, it in enumerate(node):
            if isinstance(it, dict) and str(it.get("name") or "") == s:
                return i
        names = [str(it.get("name")) for it in node
                 if isinstance(it, dict) and it.get("name")]
        raise KeyError(f"{s!r} 不在这个数组里（有名字的：{names[:8] or '无'}）")

    def _set_path(root, path: str, value) -> None:
        """按点分路径写值（数组段可以是下标，也可以是名字）。定位不到就抛。"""
        keys = str(path).split(".")
        if not keys or not keys[0]:
            raise KeyError("空路径")
        cur = root
        for i, k in enumerate(keys[:-1]):
            nxt = keys[i + 1]
            if isinstance(cur, list):
                cur = cur[_index_of(cur, k)]
            elif isinstance(cur, dict):
                if cur.get(k) is None:
                    cur[k] = [] if nxt.isdigit() else {}
                cur = cur[k]
            else:
                raise KeyError(path)
        last = keys[-1]
        if isinstance(cur, list):
            cur[_index_of(cur, last)] = value
        elif isinstance(cur, dict):
            cur[last] = value
        else:
            raise KeyError(path)

    def _lookup(root, path: str):
        """按点分路径取**现有**节点（不创建、不兜底——定位不到就抛）。"""
        cur = root
        for k in str(path).split("."):
            if isinstance(cur, list):
                cur = cur[_index_of(cur, k)]
            elif isinstance(cur, dict):
                cur = cur[k]
            else:
                raise KeyError(f"{path}：{type(cur).__name__} 不是容器")
        return cur

    # 增量 op 的三个动词（M4）：set=写值（缺省）、append=往数组尾加一个、
    # remove=删数组里的一个。**只有这三个**——多一个都要问"为什么 set 不够"。
    _VERBS = ("set", "append", "remove")

    def _apply_op(root, op) -> str:
        """应用一条增量 op，返回已应用路径（路径级 provenance）。

        append/remove 存在的理由：数组若只能整份替换，"加一个源"在客户端就
        成了"读→改→写"，两个客户端同时加会丢一个。有了它们，"加一个"就是
        加一个，不需要读全表。
        """
        p = str((op or {}).get("path") or "")
        if not p:
            raise KeyError("空路径")
        verb = str((op or {}).get("verb") or "set").strip().lower() or "set"
        val = (op or {}).get("value")
        if verb == "set":
            _set_path(root, p, val)
            return p
        if verb == "append":
            node = _lookup(root, p)
            if not isinstance(node, list):
                raise TypeError(f"{p} 不是数组，不能 append")
            node.append(val)
            return f"{p}.{len(node) - 1}"
        if verb == "remove":
            head, _, last = p.rpartition(".")
            if not head:
                raise KeyError(f"{p}：remove 要指到元素（如 source.0 / parameters.d）")
            node = _lookup(root, head)
            if isinstance(node, dict):
                # G60①（R24c）：dict 键删除——参数表等 dict 节点的键移除
                # （GUI 参数页删除走这条；数组元素删除走下方下标分支）
                if last not in node:
                    raise KeyError(f"{head} 无键 {last!r}")
                del node[last]
                return p
            if not isinstance(node, list):
                raise TypeError(f"{head} 不是数组/dict，不能 remove")
            i = _index_of(node, last)
            if not -len(node) <= i < len(node):
                raise IndexError(f"{p}：下标越界（{head} 共 {len(node)} 个）")
            del node[i]
            return p
        raise ValueError(f"未知 verb {verb!r}（只认 {'/'.join(_VERBS)}）")

    def _session_run(*a, **kw):
        """提交**当前会话模型**（f 一个字节不动）。

        用一份临时实例承接提交（SDK 的"一实例一次提交"是传输层约束，不是
        会话语义）。这就是工具栏 Run / POST /api/run 的实现，也是终端里
        `run_model()` 的实现。
        """
        cfg = current_config()
        if not cfg:
            raise RuntimeError(
                "还没有模型可提交——先在表单里建域/网格，或跑一段脚本")
        s = _new_sim()
        s.load_config(cfg)
        return s.run(*a, **kw)

    def session_connect(*a, **k):
        """会话语境的 connect：调用方给的 URL/key（示例里多为占位符）
        一律忽略——用会话凭据开一个**全新**实例并纳入合并集。"""
        nonlocal first_connect_note
        s_new = _new_sim()
        sink["sims"].append(s_new)
        if first_connect_note:
            emit({"ev": "out", "kind": "note",
                  "text": "（会话 connect：URL/key 由会话接管——全新 f）\n"})
            first_connect_note = False
        return s_new

    def attach(msg):
        """凭据换绑（O2 核心）：**换 key 不换模型**，首次调用建空模型。

        旧协议 ``bind`` 的语义是"绑网关 + 预置命名空间"，且凭据变更会
        连带重生 worker（模型跟着 token 的寿命走）。现在拆成两件事：
        attach 只更新各实例 Client 的 X-API-Key 头；要不要新模型由上层
        的 session_id 决定（新 session_id = 新 worker = 新目录 = 无快照）。
        """
        nonlocal sim, sess_url, sess_key
        url = msg.get("url") or ""
        key = msg.get("key")
        if url:
            sess_url = url
        if key is not None:
            sess_key = key
        if restored and sim is None:
            # B2 重生重放后首个 attach：会话 f 已由落盘重建——只更新凭据
            # 并接管 connect，不再另立新 f（genie.connect 本身零网络，另立
            # 的只是空 f；若顶掉恢复态，「服务重启后 f 还在」不成立）。
            sim = restored[-1]
            _reauth(sess_key)
            genie.connect = session_connect   # 会话接管（worker 进程内补丁）
            emit({"ev": "out", "kind": "note",
                  "text": "↺ 凭据已接管（模型来自落盘恢复）\n"})
        elif not session_insts():
            sim = _new_sim()
            ready = ("会话已连接网关，f 就绪。\n" if sess_url else
                     "（无网关地址：会话离线，只能建模不能提交）\n")
            sink["sims"].append(sim)
            genie.connect = session_connect   # 会话接管（worker 进程内补丁）
            ns.clear()
            ns.update(_namespace(sim))
            emit({"ev": "out", "kind": "note", "text": ready})
        else:
            # 已有模型：只换凭据，一个字节的配置都不动。
            # 判据用 session_insts() 而非 sim is None——脚本直建的实例
            # （f = Simulation(...)，不进 sims）也算已有模型。
            _reauth(sess_key)
            if sim is None:
                sim = ns.get("f") or session_insts()[-1]
            emit({"ev": "out", "kind": "note",
                  "text": "凭据已更新（模型保留）\n"})
        # 登录即绑定（2026-10-21）：attach 之后必须回流一次快照，否则 GUI
        # 要等下一次 exec 才看得见模型——"不跑代码就没有模型"缺的正是这步。
        # 此时模型可能是**空壳**（域/网格/材料全空，无任何默认值），前端
        # 据此渲染可编辑的空文档。
        # 注意：**不落盘**——attach 不改模型，写快照只会制造"模型已存在"的
        # 假象（空壳快照 vs 真模型快照同路径）。
        snapshot("attach")

    def _reauth(key):
        """把新凭据写进会话内全部实例的 Client（换 key 不换模型）。"""
        for s in session_insts():
            cli = getattr(s, "_client", None)
            if cli is not None and hasattr(cli, "set_key"):
                cli.set_key(key)

    def session_insts() -> list:
        """会话实例全集（快照/落盘/提交同一口径）：sims 全量 + ns["f"]。"""
        return _insts_of(ns, sims)

    def persist_state():
        """会话 f 落盘（B2 恢复快照——**不是第二份可编辑设计状态**，
        铁律 1 边界：唯一可编辑真源永远是会话进程内的 f，本文件只服务
        「worker 重生 / session 服务重启」后的自动重放，从不回流编辑）。
        粒度在 worker 侧：逐实例 to_config 保真（manager 侧合并 cfg 会
        丢多实例拆分）。原子写范式参照 fdtd queue._atomic_json
        （tmp+rename），文件 0600。失败全吞（best-effort 快照）。"""
        insts = session_insts()
        if not insts:
            return
        try:
            # O2：盘上只留模型，**不留凭据**。凭据由 attach 在每次重生后
            # 注入——于是"拷走一个 json"泄露不了 token。
            data = {"sims": [s.to_config() for s in insts]}
            tmp = state_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False),
                           encoding="utf-8")
            os.chmod(tmp, 0o600)
            tmp.replace(state_path)
        except Exception:   # noqa: BLE001 — 落盘失败不炸会话
            pass

    def replay_state():
        """B2 重生重放（worker 新生且存在落盘状态时，main 末尾进循环前
        调一次）：读 session_state.json → 逐实例
        real_connect(url, "").load_config(c) 重建（rollback 同款路径，
        已被回滚实证）。

        后端地址从**环境**取（部署属性），不从快照取（O2）；凭据留空，
        等随后的 attach 注入。零网关形态（Simulation(client=None)）用
        genie.Simulation(client=None)——connect("") 会抛 ValueError、空串
        还会回落 GENIE_URL 环境变量（串网关风险），故按显式 url 分支。
        任何异常吞掉 + note，恢复失败绝不拦住会话。"""
        nonlocal sess_url, sess_key
        if not state_path.exists():
            return                     # 全新会话（无落盘态）：静默，非失败
        try:
            st = json.loads(state_path.read_text(encoding="utf-8"))
            cfgs = st.get("sims") or []
            if not isinstance(cfgs, list) or not cfgs:
                return
            sess_url = (sess_url or os.environ.get("GENIE_BACKEND") or "")
            if not sess_url:
                # 无 env 部署覆盖 → 落到本机身份配置的 gateway（G72a）
                try:
                    from ..net.config import load_config
                    gw = str(load_config().get("gateway") or "").strip()
                    sess_url = gw.rstrip("/") or ""
                except Exception:   # noqa: BLE001 —— 配置坏了不拦恢复
                    sess_url = ""
            for c in cfgs:
                s = (real_connect(sess_url, "") if sess_url
                     else genie.Simulation(client=None))
                restored.append(s.load_config(c))
            sims.extend(restored)
            ns.clear()
            ns.update(_namespace(restored[-1]))
            genie.connect = session_connect   # 会话接管（与 attach 同款补丁）
            emit({"ev": "out", "kind": "note",
                  "text": f"↺ 已从落盘恢复 {len(restored)} 个仿真（上次会话态）\n"})
            # 重启后**主动回流一次快照**：Studio 与我们会话同进程，重启后
            # 已开页面只会轮询；没有这一拍，页面看到的是"空模型"（假丢）。
            snapshot("restore")
        except Exception as e:   # noqa: BLE001 — 半途而废的恢复不留给 attach 判断
            restored.clear()
            emit({"ev": "out", "kind": "note",
                  "text": f"（落盘恢复失败：{e}）\n"})

    def snapshot(origin: str = "exec", paths: list | None = None):
        """回流一次模型快照。

        ``origin`` 是**来源标记**（M3）：patch/exec/build/restore/attach/load，
        以及 **reject**（这批写入被拒、模型没动——前端据此把本地改回服务端）。
        前端靠它区分"我自己写的回来了"和"别人把模型改了"——不再靠内容猜，
        也就不会因为一次自己的回声把整个面板重建掉。
        ``paths``：本次实际变动的路径（patch 用；其它来源为空）。
        """
        insts = session_insts()
        if not insts:
            return
        try:
            cfg = current_config()
            ev = {"ev": "cfg", "cfg": cfg, "origin": origin}
            if paths:
                ev["paths"] = list(paths)
            try:
                # resolved 显示副本（表达式槽
                # →数值，浏览器零求值——3D/表单吃 resolved，cfg 原文不动）
                from genie.models.resolve import resolve_parameters
                ev["resolved"] = resolve_parameters(cfg)
            except Exception:   # noqa: BLE001 — 解析失败退化 raw
                pass
            emit(ev)
        except Exception as e:   # noqa: BLE001 — 快照失败不炸会话
            emit({"ev": "out", "kind": "error",
                  "text": f"(cfg 快照失败: {e})\n"})

    def _cancel_orphan_jobs():
        """停止后的孤儿作业清理（best-effort，仅 StopExec 路径调）：
        f.run() 的等待是客户端轮询，被同步掐断后**后端作业还在**——它在
        独立进程组（start_new_session），SIGINT 传不到，会继续烧卡到
        作业超时。遍历会话期全部 Simulation 候选（ns["f"] + sims），对
        已提交（_job_id 非空）的逐个调新公开方法 cancel()（后端已有
        /api/jobs/{id}/cancel；409=已终态按成功忽略）。异常全吞——清理
        失败绝不炸会话。"""
        cap = _Out(emit)   # cancel() 内的 print 走同一捕获面——此刻 exec
                           # 的重定向已退出，裸 print 会直插 IPC 通道（协议
                           # 外裸奔行；虽然 pump 对非 JSON 行有兜底，但不脏它）
        seen: set[int] = set()
        f = ns.get("f")
        for s in ([f] if f is not None else []) + \
                 [s for s in sims if s is not f]:
            jid = getattr(s, "_job_id", None)
            if not jid or id(s) in seen:
                continue
            seen.add(id(s))
            try:
                with contextlib_redirect(cap):
                    s.cancel()
                emit({"ev": "out", "kind": "note",
                      "text": f"后端作业 {jid} 已取消\n"})
            except Exception as e:   # noqa: BLE001 — 409=已终态，按成功忽略
                if getattr(e, "status", None) == 409:
                    emit({"ev": "out", "kind": "note",
                          "text": f"后端作业 {jid} 已终态(忽略)\n"})
                else:
                    emit({"ev": "out", "kind": "error",
                          "text": f"取消失败: {e}\n"})
        # R29 C2（R30 S-B 最小修：只碰**有在途点**的句柄，终态句柄静默
        # 跳过——修前对已 done 的句柄也发"已取消"假 note；后台续跑的
        # 扫描若仍有在途点仍会被收（停止语义 = 掐断在途计算，跨 exec
        # 的取舍随 G168 事件设计一并裁）。点实例不进会话 sink（不污染
        # 模型合并），修前停止/超时后在途点作业孤儿烧卡（A2）。
        try:
            from genie.core.sweep import active_handles as _active_sweeps
            for h in _active_sweeps():
                try:
                    if not ({"pending", "running"}
                            & set(h.rollup().keys())):
                        continue                 # 终态句柄：静默跳过
                    h.cancel()
                    emit({"ev": "out", "kind": "note",
                          "text": f"扫参 {h.id} 已取消（在途点收尸）\n"})
                except Exception:   # noqa: BLE001
                    pass
        except Exception:           # noqa: BLE001 —— 登记表不可用不炸收尸
            pass

    def on_int(signum, frame):
        # 状态感知 SIGINT/SIGBREAK（A2）：状态机本体 = _request_stop
        # （模块级、跨平台可直测——R41 补锁把"Windows 停止"从不可测
        # 变可测）；这里只做"空闲期提示"的 emit 包装。
        try:
            state = _request_stop()
        except StopExec:
            raise        # exec 期首次停止：冲出用户代码，被 exec 壳捕获
        if state == "idle":
            try:
                emit({"ev": "out", "kind": "note",
                      "text": "（无正在执行的命令——忽略停止）\n"})
            except Exception:   # noqa: BLE001 — handler 内零异常外泄
                pass
        # state == "silent"（收尾中二次停止）：静默吞掉——此刻再 raise
        # 会从 except 块内冲出收尾链炸 worker

    signal.signal(signal.SIGINT, on_int)
    # Windows 没有 SIGINT（Popen.send_signal(SIGINT) 直接 ValueError），停止
    # 走 CTRL_BREAK_EVENT → 子进程收到 SIGBREAK。把它接到同一个处理器上，
    # "停止"按钮才在 Windows 真能用（G33）。
    if hasattr(signal, "SIGBREAK"):
        try:
            signal.signal(signal.SIGBREAK, on_int)
        except (OSError, ValueError):
            pass

    # ── 运行阶段提示（站长裁决「跑仿真/下载没提示，用户不知道在什么阶段」）：
    # 给会话内所有 run() 注入缺省 progress（用户显式传则尊重用户），状态
    # 变化才发言；下载前打点。worker 层运行时包装——零 SDK/内核文件改动。──
    _last_state = {"s": None, "phase": None}
    _last_step = {"n": None}
    def _emit_state(phase, job=None):
        """会话生命周期状态事件（2026-10 站长裁决「状态监测架构收敛」）：
        GUI 的按钮/进度条 = 本事件的纯投影——废除 termBusy/正则两处旁证。
        phase ∈ exec(脚本在跑) / submit(提交上传) / queue(云端排队) /
        run(计算) / download(取回结果) / idle。manager 跟踪现值并在
        /output 回快照（刷新即恢复），事件本体同时进环（增量驱动）。"""
        d = {"phase": phase}
        if job:
            d["job_id"] = job
        emit({"ev": "out", "kind": "state", "text": "", "data": d})
    def _run_progress(status):
        st = str(status.get("state") or status.get("status") or "")
        jid_s = status.get("job_id") or None
        # 云计算队列语义（2026-10 站长裁决）：作业已受理但没开算 = queue
        # （defined/built 都算——built 是网格已建、仍在等卡）。变化才发。
        if st in ("defined", "built", "pending", "queued"):
            if _last_state["phase"] != "queue":
                _last_state["phase"] = "queue"
                _emit_state("queue", jid_s)
        elif st == "running" and _last_state["phase"] != "run":
            _last_state["phase"] = "run"
            _emit_state("run", jid_s)
        if st and st != _last_state["s"]:
            _last_state["s"] = st
            extra = ""
            for k in ("executed_steps", "step"):
                if status.get(k) is not None:
                    extra = f"（{k}={status[k]}）"
                    break
            emit({"ev": "out", "kind": "job",
                  "text": f"⚙ 作业 {status.get('job_id','')} 状态：{st}{extra}\n"})
        # 运行中步进进度（2026-10-15 站长定案方案 A：GUI result-chip 进度
        # 条）。status 的 step/n_steps 由内核 runner 的 stdout 排水泵回写
        # （≈5% 一跳）；(step,n_steps) 变了才发 job-progress——终端零刷屏
        # （term.js 对该 kind 不落终端，只喂 chip），GUI 侧据此算 ETA。
        step, n_tot = status.get("step"), status.get("n_steps")
        if st == "running" and isinstance(step, int) and isinstance(n_tot, int):
            if (step, n_tot) != _last_step["n"]:
                _last_step["n"] = (step, n_tot)
                emit({"ev": "out", "kind": "job-progress", "text": "",
                      "data": {"job_id": status.get("job_id"),
                               "step": step, "n_steps": n_tot}})
    _orig_sim_run = genie.core.simulate.Simulation.run
    def _sim_run_with_progress(self, *a, **kw):
        if getattr(self, "_job_id", None) is not None:
            # 一个实例只能提交一次（SDK 契约，手册 07-run 页已写明）。会话里
            # "再跑一次当前模型"是 run 原语的事（工具栏 Run / POST /api/run /
            # 终端 run_model()）——**不在这里偷偷重建实例**：那会让用户手里
            # 的对象和别名静默失效，也会把"改完没生效"变成无声事故
            # （2026-10-13 站长实测：改 x_range 后 Run，旧文件名/plot 不变）。
            raise RuntimeError(
                f"这个实例已经提交过（作业 {self._job_id}）——"
                "要再跑当前模型：点工具栏 Run，或在终端里 run_model()")
        if getattr(self, "_sweep_point", False):
            # G168/R34（站长裁"汇总为王，点级降噪"）：sweep 点不做单作业级
            # 播报——提交/排队/状态/进度/job-done 全静默（终端不刷 N 份
            # 流水账、chip 不在 N 个作业间跳、S5 的跨作业去重串槽从根上
            # 不再可观察）。每点一行终态 + 扫描级汇总走
            # sweep.hooks["on_event"] → _on_sweep_event（见上）。
            return _orig_sim_run(self, *a, **kw)
        if kw.get("progress") is None:
            kw["progress"] = _run_progress
        _last_state["phase"] = "submit"
        emit({"ev": "out", "kind": "job", "text": "⏳ 提交作业…\n",
              "data": {"phase": "submit"}})
        _emit_state("submit")
        try:
            out = _orig_sim_run(self, *a, **kw)
        except BaseException as e:
            # 失败也发 job-done（ok:false）：结果模式切换的锚 = 「一次 run
            # 尝试已了结」这个事实——与 2026-10-14 裁决「快速失败也进结果
            # 态」一致；跑挂更该回结果视图看是哪步炸的。异常原样上抛。
            jid = getattr(self, "_job_id", None)
            _last_state["phase"] = None
            _emit_state("exec")
            emit({"ev": "out", "kind": "job-done",
                  "text": f"✗ 作业失败（{jid or '未提交'}）\n",
                  "data": {"job_id": jid or "", "ok": False}})
            # 连不上网关 = 机器层隧道断了（本地 Studio 形态下最常见的一类
            # "未提交"失败——隧道是机器基础设施，不是会话的一部分）。
            # 说人话 + 给下一步动作；traceback 原样保留供诊断（R7：只有
            # 一种措辞，这里不重复 detail）。判据走因链遍历：client 层把
            # 传输错误包成 GenieHTTPError（from e），顶层类型探不到。
            def _conn_dead(exc) -> bool:
                c = exc
                while c is not None:
                    if isinstance(c, (ConnectionError, OSError)):
                        return True
                    c = c.__cause__
                return False
            if _conn_dead(e):
                url = getattr(getattr(self, "_client", None),
                              "base_url", "") or "网关"
                emit({"ev": "out", "kind": "note",
                      "text": f"⚠ 连不上 {url}——本机隧道可能断了"
                              "（它是机器基础设施，重启 studio 救不了）："
                              "任务计划程序里运行任务 genie-tunnel，"
                              "或重开 start-studio。\n"})
            raise
        # G13② 结果事件：run 成功（含自动下载落盘）→ job-done 进环，
        # GUI 靠它切结果模式/按 job_id 读会话工作区的 h5（结果=会话状态）。
        jid = getattr(self, "_job_id", None)
        _last_state["phase"] = None   # 下次 run 的 queue/run 转换重新发言
        _emit_state("exec")           # 生命周期回脚本执行（job-done 随后）
        emit({"ev": "out", "kind": "job-done",
              "text": f"✓ 作业 {jid} 完成——结果已就绪\n",
              "data": {"job_id": jid or "", "ok": True}})
        return out
    genie.core.simulate.Simulation.run = _sim_run_with_progress
    from genie.net.client import Client as _Client   # import genie 不预载 net 子包
    _orig_fetch = _Client.fetch_result
    def _prune_results():
        # 云用户工作区配额（2026-10-13 站长裁决）：本地 SDK 用户不管；
        # 会话侧（=云用户与本地 studio 的运行面）按 mtime 最旧先清到
        # 限额内。GENIE_RESULT_QUOTA_GB 可调，缺省 2GB。
        # 目标目录（O2）：GENIE_RESULT_DIR（= 本会话的结果目录，管理进程
        # 显式传入）优先；SDK 直跑无此 env 时回退 cwd/genie_results
        # （老行为，workspace 锚见 main 头注）。
        from pathlib import Path as _P

        from genie.studio.results_view import prune_over_quota
        target = _P(os.environ.get("GENIE_RESULT_DIR")
                    or (workspace / "genie_results"))
        removed = prune_over_quota(
            target,
            int(float(os.environ.get("GENIE_RESULT_QUOTA_GB", "2"))
                * 1024 ** 3))
        if removed:
            emit({"ev": "out", "kind": "note",
                  "text": f"🧹 结果配额已满：清掉最旧 {len(removed)} 个"
                          f"（{', '.join(removed[:3])}"
                          f"{'…' if len(removed) > 3 else ''}）\n"})
        return removed
    def _fetch_with_note(self, *a, **kw):
        _emit_state("download", getattr(self, "_job_id", None))
        emit({"ev": "out", "kind": "job", "text": "⬇ 下载结果…\n"})
        out = _orig_fetch(self, *a, **kw)
        _prune_results()   # 下载后收口
        return out
    _Client.fetch_result = _fetch_with_note

    # （原"会话形态钉死 publish"的块已随本地 SDK 镜像通道退役而删除——
    # integrations/live 与 core 的 publish hook 于 G53 裁决中整体移除，
    # 2026-10-23；drafts 环保留，属云宿主 agent 面板的管道。）

    # ── 统一的执行壳（exec 与 run 共用）──────────────────────────────
    # 三件事只有一条路：捕获 stdout、状态相位、错误裁剪、停止语义。
    # **模型变不变由 fn 自己决定**，壳不管。
    # （R41/A+ 2026-10-29：本地墙钟整块退役——无 alarm、无 TimeoutError
    # 分支。等待由用户停止与服务端作业超时管。顺带修掉的暗病：用户代码
    # 自己抛的 TimeoutError（如 requests 超时）以前会被误认成"执行超时"
    # 并触发收尸，现在走普通异常路径。）
    def _guarded(fn):
        global _exec_busy, _stop_requested
        cap = _Out(emit)
        ok, err = True, ""
        _stop_requested = False   # 新一段执行：停止计数清零
        _exec_busy = True         # 先置标志：此后到达的 SIGINT 才必然走
                                  # StopExec 路径（空闲期只提示不打断）
        _emit_state("exec")
        try:
            with contextlib_redirect(cap):
                fn()
        except StopExec:
            # 用户停止（A2）：置于 BaseException 之前——后者会吞掉一切
            # BaseException，顺序即语义。
            ok, err = False, "用户停止"
            emit({"ev": "out", "kind": "note",
                  "text": "⏹ 已停止——会话仍在，可继续敲命令\n"})
            _cancel_orphan_jobs()   # 同步等待已断，后端作业在此收尸
        except BaseException as e:   # noqa: BLE001 — 用户代码异常回终端
            ok, err = False, f"{type(e).__name__}: {e}"
            tb = traceback.format_exc(limit=None).splitlines(keepends=True)
            # 只留用户代码帧——worker 内部帧是纯噪音（2026-10-11 站长裁决：
            # 报错要短、一次、只讲用户的事）。注意匹配「帧行」
            # （File "<terminal>"…）而非任何含 <terminal> 的行——worker 的
            # exec 源码行本身含该串，宽匹配曾把 worker 帧留下。
            cut = next((i for i, ln in enumerate(tb)
                        if ln.lstrip().startswith('File "<terminal>"')), None)
            emit({"ev": "out", "kind": "error", "text":
                  "Traceback (most recent call last):\n" + "".join(tb[cut:])
                  if cut is not None else "".join(tb)})
        finally:
            _exec_busy = False   # 空闲期 SIGINT 不得再冲进用户语义
            _stop_requested = False
            _last_state["phase"] = None
            _emit_state("idle")
        return ok, err

    # ── B2 重生重放：worker 新生且存在落盘状态 → 自动重建会话 f。放在
    # 进循环前：manager 重生后发来的 attach op 排在重放之后处理（attach 见
    # restored 分支——凭据接管，不顶掉恢复态）。 ──
    replay_state()
    # 清理锚（2026-10-20）：worker 重生即收口一次——不等下一次下载，
    # 否则只在 fetch 后清意味着「重生后永不下载则配额永不生效」。
    _prune_results()

    def _step(line):
        # 循环体（本机由下面的 while 驱动；网页同进程形态由 _runtime.InprocRuntime
        # 逐条调用——同一份代码，两个驱动方。见 LEDGER G80 第 4b 步）。
        nonlocal ns, sims, sim, sess_url
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            return
        op = msg.get("op")
        if op == "attach":                # （R41/S2：旧名 bind 已删——零发送方）
            try:
                attach(msg)
                emit({"ev": "ack", "id": -1, "ok": True, "err": ""})
            except Exception as e:   # noqa: BLE001 — 连接失败回终端，进程存活
                emit({"ev": "out", "kind": "error",
                      "text": "✗ 会话连接失败: "
                              + "".join(traceback.format_exception_only(e))})
                emit({"ev": "ack", "id": -1, "ok": False, "err": str(e)})
        elif op in ("patch", "replace"):
            # 两个模型写入原语（M3b）：
            #   patch   —— 路径级增量（表单/树/工具条的每一次改动）；
            #   replace —— 整份模型换成给定的一份（.gensim 导入等搬运）。
            # 都是**服务端权威**：客户端只发它改了什么，不再发整份文档。
            eid = msg.get("id", -1)
            if msg.get("url") and not sess_url:
                sess_url = msg["url"]
            if sim is None and msg.get("key"):
                attach({"url": sess_url, "key": msg.get("key")})
            applied: list = []
            attempted: list = []
            try:
                if op == "replace":
                    model_in = msg.get("model")
                    if not isinstance(model_in, dict):
                        raise ValueError("model 须 dict（SimConfig JSON）")
                    if not sess_url and sim is None:
                        raise RuntimeError(
                            "会话未绑定网关：先登录（或运行一次脚本）建立会话")
                    cfg_new = model_in
                    applied = ["*"]
                    attempted = ["*"]
                else:
                    ops = msg.get("ops") or []
                    if not isinstance(ops, list) or not ops:
                        raise ValueError("ops 须非空列表")
                    attempted = [str((o or {}).get("path") or "?") for o in ops]
                    cfg_new = current_config()
                    bad: list = []
                    for i, o in enumerate(ops, 1):
                        try:
                            applied.append(_apply_op(cfg_new, o))
                        except Exception as e:   # noqa: BLE001
                            bad.append(f"第 {i} 条 {attempted[i - 1]}：{e}")
                    if bad:
                        # 一批 op **全对或全错**。半落地最坏的后果不是模型
                        # 难看，而是调用方（尤其 agent）以为整批成了——于是
                        # 它后面的每一步都建立在"我以为已经改了"之上。
                        raise ValueError(
                            "；".join(bad[:3])
                            + ("" if len(bad) <= 3 else f" 等 {len(bad)} 条")
                            + "——整批未提交")

                # 提交：模型收成**一个实例**（与 build 提交同一条规则）。
                # 这一步是必要的：显示走 merge、写入走单实例，若只改其中
                # 一个，另一个会把它盖掉（多实例互遮的老坑）。
                one = _new_sim()
                one.load_config(cfg_new)
                ns["f"] = one
                sims = [one]
                sim = one
                sink["sims"] = sims

                snapshot("patch" if op == "patch" else "replace", applied)
                persist_state()
                emit({"ev": "done", "id": eid, "ok": True, "err": ""})
            except Exception as e:   # noqa: BLE001
                # 拒了也回声一次（origin=reject）：前端据此把本地那份改回
                # 服务端的值。少了这一步，一个非法字段会一直留在表单里，之
                # 后每次提交都带上它、每次都整批失败——表单从此存不进去。
                try:
                    snapshot("reject", attempted)
                except Exception:   # noqa: BLE001 — 回声失败不掩盖真错误
                    pass
                emit({"ev": "out", "kind": "error",
                      "text": f"({op} 失败: {e})\n"})
                emit({"ev": "done", "id": eid, "ok": False, "err": str(e)})
        elif op in ("exec", "run"):
            # 注意：ns/sims/sim 是 main() 的局部量，下面直接重新绑定——
            # 闭包（snapshot/session_insts/persist_state…）拿的是同一个 cell，
            # 所以"原子提交"= 重绑这两个名字，读者只会看到提交前或提交后。
            eid = msg.get("id", 0)
            if msg.get("url") and not sess_url:
                sess_url = msg["url"]     # 后端是部署属性：先记着，不建 f
            if sim is None and msg.get("key"):
                # 兜底：已登录但还没 attach 过就直接执行（刷新竞态/旧客户端）。
                # 无 key（离线/未登录）时**不建会话**——用户在代码里自己
                # 建实例即可，别凭空多一个空 f 污染 cfg 快照。
                attach({"url": sess_url, "key": msg.get("key")})

            if op == "run":
                # 提交当前模型：**不改模型**（f 一个字节不动）。
                # timeout_s 只作为**作业超时**上行（服务端语义），
                # 不再兼任本地等待墙钟（R41/A+）。
                ok, err = _guarded(
                    lambda: _session_run(gpu=msg.get("gpu"),
                                         timeout_s=msg.get("timeout_s")))
                emit({"ev": "done", "id": eid, "ok": ok, "err": err})
                return

            code = msg.get("code") or ""
            # mode 是**消费**不是归一（R41/S2）：管理器 k.s.submit 已按
            # 正典归一（session_service.exec_action"只认 build"），这里只
            # 读值分叉——旧前端 reset:true 别名由 /api/exec 转发层接住
            # （session_client），worker 不再抄第二套归一。
            mode = msg.get("mode")
            target_ns = ns
            build_ns = None
            build_sims: list = []
            if mode == "build":
                # 脚本运行 = **构造一个新模型**：在全新命名空间里执行，成功
                # 才原子替换当前模型。失败时旧模型一个字节都没被碰过——
                # 所以这里没有"回滚"这回事（旧实现靠内存备份+还原）。
                build_ns = _namespace()
                seed = _new_sim()
                build_sims.append(seed)
                build_ns["f"] = seed
                sink["sims"] = build_sims      # 脚本里的 connect() 落这里
                target_ns = build_ns
                emit({"ev": "out", "kind": "note",
                      "text": "── 脚本运行：构造新模型（成功才替换当前模型）\n"})

            def _body():
                exec(compile(code, "<terminal>", "exec"), target_ns)  # noqa: S102

            try:
                ok, err = _guarded(_body)
            finally:
                if mode == "build":
                    sink["sims"] = sims         # 无论成败都把落点归位

            if mode == "build":
                if ok:
                    # 提交：模型 = 本次命名空间里所有实例的**合并值**，并收成
                    # **一个实例**。N→1 只在提交点发生一次——旧实现"显示
                    # 合并、写入单实例"的口径分歧就是从这里漏出去的（表单
                    # 改一次就把脚本建的 N 个实例悄悄折成一个）。
                    one = _new_sim()
                    one.load_config(_merged(
                        _insts_of(build_ns, build_sims)) or {})
                    ns = _namespace(one)
                    sims = [one]
                    sim = one
                    sink["sims"] = sims
                else:
                    emit({"ev": "out", "kind": "note",
                          "text": "↩ 脚本失败——当前模型未被改动\n"})

            snapshot("build" if mode == "build" else "exec")
            persist_state()     # 模型变了才落盘（build 失败时提交前状态不变，
                                # 但 persist 是幂等快照，照写无妨）
            emit({"ev": "done", "id": eid, "ok": ok, "err": err})

    if expose is not None:
        # 同进程形态（网页 Pyodide）：不跑循环，把 _step 交出去，
        # 由调用方逐条驱动——执行逻辑与子进程形态是同一个 _step。
        expose(_step)
        return
    while True:
        line = readline()
        if not line:
            break
        _step(line)


def contextlib_redirect(out):
    import contextlib
    return contextlib.redirect_stdout(out)


if __name__ == "__main__":
    main()
