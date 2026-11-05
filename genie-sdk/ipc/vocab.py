"""genie.ipc.vocab — host↔worker 管子的词与构造函数（唯一的家）。

契约（词表 / 字段集 / 禁令）→ ``../MODMAP.md``；本模块只有实现：

- 零 IO：不 import 任何东西，只造 dict（可直接 ``json.dumps``）；
- 不做 schema / 版本 / 握手（两端同包同 commit，Q6.2）；
- 结构化 kind 冻结在 ``OUT_KINDS``——加词 = 改 MODMAP 同轮。

发送侧（session）只造 op；接收侧（worker）只造 ev；两端代码里
不得出现消息字面量（发现即收编进这里）。
"""
from __future__ import annotations

__all__ = [
    # 词
    "OP_EXEC", "EV_READY", "EV_OUT", "EV_CFG", "EV_DONE",
    "OUT_KIND_PLOT", "OUT_KIND_ERROR", "OUT_KIND_PROGRESS", "OUT_KINDS",
    # 构造函数（每个消息一个）
    "exec", "ready", "out", "cfg", "done",
]

# ── 词（唯一的家）────────────────────────────────────────────────

OP_EXEC = "exec"

EV_READY = "ready"
EV_OUT = "out"
EV_CFG = "cfg"
EV_DONE = "done"

OUT_KIND_PLOT = "plot"       # data = {mon, field, job_id}（引用，不进字节）
OUT_KIND_ERROR = "error"     # 红行；text 即载荷
# 当前批 rollup（批 6 入表；发射方 = worker/run 等待循环，消费方 = 卡片）：
# data = {total, done, failed, jobs: {job_id: {state, progress?}}}——
# progress 可选（批 3：只显示不判终态）；单跑即批长 1
OUT_KIND_PROGRESS = "progress"
OUT_KINDS = frozenset({OUT_KIND_PLOT, OUT_KIND_ERROR, OUT_KIND_PROGRESS})


# ── op（host → worker；session 造）──────────────────────────────

def exec(id, code, *, fresh=False, echo=True):
    """唯一写通道。``fresh=True`` = 在老命名空间的浅拷贝里执行、成功才
    把拷贝整份替换为当前命名空间并原子替换模型（Run 语义，Q6.1 补
    2026-11-04）；``echo=False`` = 静默执行（表单 delta：stdout 不进终端环）。
    ``id`` 由 session 发号，``done`` 原样带回。
    """
    return {"op": OP_EXEC, "id": int(id), "code": str(code),
            "fresh": bool(fresh), "echo": bool(echo)}


# ── ev（worker → host；worker 造）────────────────────────────────

def ready():
    """worker 就绪信号（session 的 spawn 等待消费）。"""
    return {"ev": EV_READY}


def out(text, kind=None, data=None):
    """终端环条目。``kind`` 只许 ``OUT_KINDS`` 冻结集；``None`` 的键不出现；
    ``data`` 必须随结构化 ``kind``（plot 的引用字段集见 MODMAP §4）。
    """
    if kind is None:
        if data is not None:
            raise ValueError("out: data requires a structured kind")
    elif kind not in OUT_KINDS:
        raise ValueError(f"out: unknown kind {kind!r} "
                         f"(frozen set: {sorted(OUT_KINDS)})")
    m = {"ev": EV_OUT, "text": str(text)}
    if kind is not None:
        m["kind"] = kind
        if data is not None:
            m["data"] = data
    return m


def cfg(snapshot):
    """模型投影（SimConfig 形状的 dict，原样上管；只带快照，不带元字段）。"""
    return {"ev": EV_CFG, "cfg": snapshot}


def done(id, ok, err=None):
    """exec 完成信号（busy 解除）；``err`` 是给人看的短句。"""
    m = {"ev": EV_DONE, "id": int(id), "ok": bool(ok)}
    if err is not None:
        m["err"] = str(err)
    return m
