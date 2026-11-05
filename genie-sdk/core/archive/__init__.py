"""genie.core.archive — `.gensim` 文档容器原语。

契约 → ``MODMAP.md``（站长逐条核毕 2026-11-04；批 6 扩批 N）：

- 三成员布局 ``simconfig.json + results/ + state.json``（Q4.1）；
- 压缩按成员分策略：结果 h5 一律 STORED、小成员 DEFLATED（Q2.2-1）；
- 原子落盘 = 写 temp 完整文档再 rename；中途失败原文件完好、temp 清理
  （Q2.2-1「temp doc + rename」，``test_gensim_atomic_write`` 锁）；
- 结果成员流式写、不整包缓冲（``test_gensim_stream_write`` 锁）；
- **只保留当前批**（Q4.2 下午补注）：批内每个 job 各一个
  ``results/<job>.h5`` 成员，落位（无 results）即作废旧批；
  ``inject_result`` = 「灌一个成员 + 更新 state」一步原子（Q4.3 补 2 ③）；
- **对成员内容不透明**（不解释、不校验语义；h5 归 results、恢复模型归
  worker）；**零 genie 依赖**（仅标准库，``test_archive_zero_genie_imports``
  锁）；**不长业务状态机、不读环境**（路径显式传入）。
"""
from __future__ import annotations

import json
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Iterable

__all__ = ["STATE_KEYS", "Writer", "save", "inject_result", "read_config",
           "read_state", "result_jobs", "extract_result", "read_recipes",
           "write_recipes", "inject_post", "remove_post", "post_names"]

_CONFIG_MEMBER = "simconfig.json"
_STATE_MEMBER = "state.json"
_RESULT_PREFIX = "results/"
_RESULT_SUFFIX = ".h5"
#: Q11 post 成员（五拍 2026-11-05，档为正本）：档根配方成员
#: ``post_analyze.json``（具名条目 ``{name: {code, script}}``）+
#: ``post/<name>.npz`` 产出成员（名即键——收窄后档恒单结果）。
_RECIPE_MEMBER = "post_analyze.json"
_POST_PREFIX = "post/"
_POST_SUFFIX = ".npz"
_COPY_CHUNK = 1 << 20                      # 流式复制块（照搬结果成员用）

#: state.json 键集（站长拍定 2026-11-04；批 6 补第四栏，Q4.3 补 2 ②）：
#: format=格式版本（无此键 = v1 旧档）、allocator=发号器值（Q1.2.5）、
#: digest=保存时模型指纹（Q1.3 dirty 基准；落位即写 = 落位算一次保存）、
#: inflight=在飞批痕迹（本批 job id 清单；成功终态清空、失败/取消停留）。
#: job 绑定不进——results/ 成员的条目名即绑定（Q4.2）。
STATE_KEYS = ("format", "allocator", "digest", "inflight")


def _check_state(state: dict) -> dict:
    if set(state) != set(STATE_KEYS):
        raise ValueError(
            f"state.json keys must be exactly {sorted(STATE_KEYS)}, "
            f"got {sorted(state)}")
    if not isinstance(state["inflight"], list):
        raise ValueError("state.json 'inflight' must be a list of job ids")
    return state


class Writer:
    """原子写整份 `.gensim`：temp 落盘 → 成功才 rename（Q2.2-1）。

    用法（worker/run 的常规保存与「run 成功自动存」都走这里）::

        with Writer(path) as w:
            w.write_config(cfg)
            w.write_state({"format": 2, "allocator": 25, "digest": "…"})
            w.carry_result(old_path)             # 模型改动保存：结果照搬
            w.stream_result(job_id, chunk_iter)  # 新结果：流式写（二选一）

    异常退出 = 磁盘上原 `.gensim` 逐字节完好、temp 残件清理。
    一文档最多一个结果成员（Q4.2；形状守卫，不是业务时序）。
    """

    def __init__(self, path) -> None:
        self._path = Path(path)
        self._tmp: Path | None = None
        self._zf: zipfile.ZipFile | None = None
        self._result_names: set = set()

    # -- 生命周期 -----------------------------------------------------

    def __enter__(self) -> "Writer":
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent),
                                    prefix=self._path.name + ".",
                                    suffix=".tmp")
        os.close(fd)
        self._tmp = Path(tmp)
        self._zf = zipfile.ZipFile(self._tmp, "w", allowZip64=True)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        tmp, zf = self._tmp, self._zf
        self._tmp = self._zf = None
        if exc_type is not None:                       # 失败：清 temp，原文件不动
            try:
                if zf is not None:
                    zf.close()
            finally:
                tmp.unlink(missing_ok=True)
            return False
        try:
            zf.close()
            os.replace(tmp, self._path)                # 原子替换
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return False

    # -- 成员写入 -----------------------------------------------------

    def write_config(self, cfg: dict) -> None:
        """simconfig.json（小成员，DEFLATED）。内容不透明：dict 直接序列化。"""
        self._write_small(_CONFIG_MEMBER, cfg)

    def write_state(self, state: dict) -> None:
        """state.json（小成员，DEFLATED）。只认四栏形状（STATE_KEYS）。"""
        self._write_small(_STATE_MEMBER, _check_state(state))

    def _write_small(self, member: str, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False, indent=1).encode("utf-8")
        self._zf.writestr(member, data,
                          compress_type=zipfile.ZIP_DEFLATED)

    def write_recipes(self, recipes: dict) -> None:
        """``post_analyze.json``（档根小成员，DEFLATED）——Q11 具名配方
        集合 ``{name: {code, script}}``（Q11 五拍条目形态），内容不透明；
        ≥1 条才写（空 = 不写该
        成员 = 清除语义）。"""
        self._write_small(_RECIPE_MEMBER, recipes)

    def _claim_result(self, name: str) -> None:
        if name in self._result_names:
            raise ValueError(
                f"results member {name!r} already written "
                f"(one member per job, no re-inject in one write)")

    def stream_result(self, job_id: str, chunks: Iterable[bytes]) -> None:
        """`results/<job>.h5`：STORED、流式（逐块写，不整包缓冲）。
        批内每个 job 各一个成员（Q4.2 下午补注）；同名重复灌入拒收。"""
        name = _RESULT_PREFIX + job_id + _RESULT_SUFFIX
        self._claim_result(name)
        # ZipFile.open(w) 的成员压缩 = 构造器的 ZIP_STORED（缺省）
        with self._zf.open(name, "w") as fh:
            for chunk in chunks:
                fh.write(chunk)
        self._result_names.add(name)

    def carry_result(self, source) -> None:
        """从现有 `.gensim` 照搬**全部**结果成员 + `post/*.npz` 产出成员
        （常规保存：模型改了，结果没动——当前批照搬，Q4.2 下午补注；
        post 产出随结果同款照搬，Q11 细节 9）。源文件无相关成员 =
        静默无操作。流式复制，不整包缓冲。"""
        src = Path(source)
        if not src.exists():
            return
        with zipfile.ZipFile(src) as z:
            for name in z.namelist():
                is_result = (name.startswith(_RESULT_PREFIX)
                             and name.endswith(_RESULT_SUFFIX))
                is_post = (name.startswith(_POST_PREFIX)
                           and name.endswith(_POST_SUFFIX))
                if not (is_result or is_post):
                    continue
                self._claim_result(name)
                with z.open(name) as r, self._zf.open(name, "w") as w:
                    while True:
                        buf = r.read(_COPY_CHUNK)
                        if not buf:
                            break
                        w.write(buf)
                self._result_names.add(name)


def save(path, *, simconfig: dict, state: dict,
         carry_result_from=None, result=None, post_recipes=None) -> None:
    """一步保存（Writer 的组合壳；落位 = 本函数无结果参数的那一档）。
    ``result = (job_id, chunks)`` 写新结果（流式）；
    ``carry_result_from = 路径`` 照搬旧结果（含 post 产出）；
    ``post_recipes = {name: {code, script}}`` 写配方成员（None / 空 =
    不写 =
    清除语义；内存现值由调用方传入，Q11 三拍）；result 与 carry 互斥
    （用法形状，非成员上限——批 N 由 Writer 多次 ``stream_result`` /
    多次 ``inject_result`` 达成）。"""
    if carry_result_from is not None and result is not None:
        raise ValueError("save: result and carry_result_from are mutually "
                         "exclusive (用法形状；批 N 走多次 stream/inject)")
    with Writer(path) as w:
        w.write_config(simconfig)
        w.write_state(state)
        if post_recipes:
            w.write_recipes(post_recipes)
        if carry_result_from is not None:
            w.carry_result(carry_result_from)
        if result is not None:
            job_id, chunks = result
            w.stream_result(job_id, chunks)


# ── 读取 ---------------------------------------------------------------


def read_config(path) -> dict:
    """simconfig.json → dict（内容不透明：校验与恢复归调用方）。"""
    with zipfile.ZipFile(path) as z:
        return json.loads(z.read(_CONFIG_MEMBER).decode("utf-8"))


def read_state(path) -> dict:
    """state.json → dict；键集不是四键（如 v1 旧档）→ ValueError。"""
    with zipfile.ZipFile(path) as z:
        state = json.loads(z.read(_STATE_MEMBER).decode("utf-8"))
    return _check_state(state)


def result_jobs(path) -> "list[str]":
    """results/ 全部成员的 job id（排序返回）——**条目名即 job 绑定**
    （Q4.2；批 N = 当前批全体成员）。"""
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist()
                 if n.startswith(_RESULT_PREFIX) and n.endswith(_RESULT_SUFFIX)]
    return sorted(n[len(_RESULT_PREFIX):-len(_RESULT_SUFFIX)] for n in names)


def read_recipes(path) -> dict:
    """``post_analyze.json`` → ``{name: {code, script}}``（五拍条目）；
    成员缺席 → ``{}``
    （Q11 三拍；内容不透明，恢复归 worker）。"""
    with zipfile.ZipFile(path) as z:
        if _RECIPE_MEMBER not in z.namelist():
            return {}
        return json.loads(z.read(_RECIPE_MEMBER).decode("utf-8"))


def post_names(path) -> "list[str]":
    """post/ 全部产出成员的配方名（排序）——名即键（Q11 三拍；GUI
    直读 npz 的口子，v1 无读动词）。"""
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist()
                 if n.startswith(_POST_PREFIX) and n.endswith(_POST_SUFFIX)]
    return sorted(n[len(_POST_PREFIX):-len(_POST_SUFFIX)] for n in names)


def extract_result(path, dest_dir, job_id=None) -> Path:
    """把结果成员解到 ``dest_dir/<job>.h5`` 并返回路径（Q2.2-1「读缓存，
    不是外部存储」：生命周期归调用方，读完删）。流式，不整包缓冲。
    ``job_id=None`` = 恰一个成员时解它，零个或多于一个 → ValueError。"""
    jobs = result_jobs(path)
    if job_id is None:
        if len(jobs) != 1:
            raise ValueError(
                f"expected exactly one results member, got {jobs}; "
                f"pass job_id explicitly")
        job_id = jobs[0]
    dest = Path(dest_dir) / (job_id + _RESULT_SUFFIX)
    with zipfile.ZipFile(path) as z:
        with z.open(_RESULT_PREFIX + job_id + _RESULT_SUFFIX) as r, \
                open(dest, "wb") as w:
            while True:
                buf = r.read(_COPY_CHUNK)
                if not buf:
                    break
                w.write(buf)
    return dest


# ── 终态灌入（Q4.3 补 2 ③）─────────────────────────────────────────


def inject_result(path, job_id: str, chunks: Iterable[bytes],
                  state: dict) -> None:
    """往现有 `.gensim` 灌**一个** `results/<job>.h5` 成员 + 更新
    state——一步原子（temp + rename；语义上不重写模型：simconfig 与
    既有成员原样照搬）。中途失败 = 原文档逐字节完好、temp 清理。"""
    path = Path(path)
    _check_state(state)
    member = _RESULT_PREFIX + job_id + _RESULT_SUFFIX
    fd, tmp = tempfile.mkstemp(dir=str(path.parent),
                               prefix=path.name + ".", suffix=".tmp")
    os.close(fd)
    try:
        with zipfile.ZipFile(path) as src, \
                zipfile.ZipFile(tmp, "w", allowZip64=True) as dst:
            for info in src.infolist():
                if info.filename == _STATE_MEMBER \
                        or info.filename == member:
                    continue                        # state 重写；成员不重灌
                if info.filename.startswith(_RESULT_PREFIX):
                    with src.open(info) as r, dst.open(info.filename, "w") as w:
                        while True:                # 结果成员：流式照搬（STORED）
                            buf = r.read(_COPY_CHUNK)
                            if not buf:
                                break
                            w.write(buf)
                else:                              # 小成员：按原压缩照搬
                    dst.writestr(info, src.read(info.filename),
                                 compress_type=info.compress_type)
            data = json.dumps(state, ensure_ascii=False, indent=1).encode("utf-8")
            dst.writestr(_STATE_MEMBER, data,
                         compress_type=zipfile.ZIP_DEFLATED)
            with dst.open(member, "w") as fh:      # 新结果：流式（STORED）
                for chunk in chunks:
                    fh.write(chunk)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def inject_post(path, name: str, data: bytes) -> None:
    """往现有 `.gensim` 灌 / 换一个 ``post/<name>.npz`` 成员——一步原子
    （temp + rename；Q11 三拍：state 不动，simconfig 与既有成员原样
    照搬；**同名换新** = 重跑语义）。中途失败 = 原文档逐字节完好、
    temp 清理。npz 已是压缩内容 → STORED。"""
    path = Path(path)
    member = _POST_PREFIX + name + _POST_SUFFIX
    fd, tmp = tempfile.mkstemp(dir=str(path.parent),
                               prefix=path.name + ".", suffix=".tmp")
    os.close(fd)
    try:
        with zipfile.ZipFile(path) as src, \
                zipfile.ZipFile(tmp, "w", allowZip64=True) as dst:
            for info in src.infolist():
                if info.filename == member:
                    continue                    # 同名换新（重跑）
                if info.filename.startswith(_RESULT_PREFIX) \
                        or info.filename.startswith(_POST_PREFIX):
                    with src.open(info) as r, dst.open(info.filename, "w") as w:
                        while True:            # 大成员：流式照搬（STORED）
                            buf = r.read(_COPY_CHUNK)
                            if not buf:
                                break
                            w.write(buf)
                else:                          # 小成员：按原压缩照搬
                    dst.writestr(info, src.read(info.filename),
                                 compress_type=info.compress_type)
            dst.writestr(member, data,
                         compress_type=zipfile.ZIP_STORED)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_recipes(path, recipes: dict) -> None:
    """把配方集合原子写进现有 `.gensim`（**只换** ``post_analyze.json``
    成员；Q11 三拍 = ``f.update_post_analyze`` 的立即入档）。空 dict =
    移除该成员。其余成员（模型 / state / results / post 产出）原样
    照搬。"""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent),
                               prefix=path.name + ".", suffix=".tmp")
    os.close(fd)
    try:
        with zipfile.ZipFile(path) as src, \
                zipfile.ZipFile(tmp, "w", allowZip64=True) as dst:
            for info in src.infolist():
                if info.filename == _RECIPE_MEMBER:
                    continue                    # 配方成员换新
                if info.filename.startswith(_RESULT_PREFIX) \
                        or info.filename.startswith(_POST_PREFIX):
                    with src.open(info) as r, dst.open(info.filename, "w") as w:
                        while True:            # 大成员：流式照搬（STORED）
                            buf = r.read(_COPY_CHUNK)
                            if not buf:
                                break
                            w.write(buf)
                else:                          # 小成员：按原压缩照搬
                    dst.writestr(info, src.read(info.filename),
                                 compress_type=info.compress_type)
            if recipes:
                data = json.dumps(recipes, ensure_ascii=False,
                                  indent=1).encode("utf-8")
                dst.writestr(_RECIPE_MEMBER, data,
                             compress_type=zipfile.ZIP_DEFLATED)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def remove_post(path, name: str) -> None:
    """从现有 `.gensim` 移除 ``post/<name>.npz`` 成员——一步原子
    （Q11 delete 的档同步；成员缺席 = 无操作）。其余成员原样照搬。"""
    path = Path(path)
    member = _POST_PREFIX + name + _POST_SUFFIX
    with zipfile.ZipFile(path) as z:
        if member not in z.namelist():
            return
    fd, tmp = tempfile.mkstemp(dir=str(path.parent),
                               prefix=path.name + ".", suffix=".tmp")
    os.close(fd)
    try:
        with zipfile.ZipFile(path) as src, \
                zipfile.ZipFile(tmp, "w", allowZip64=True) as dst:
            for info in src.infolist():
                if info.filename == member:
                    continue                    # 移除目标
                if info.filename.startswith(_RESULT_PREFIX) \
                        or info.filename.startswith(_POST_PREFIX):
                    with src.open(info) as r, dst.open(info.filename, "w") as w:
                        while True:            # 大成员：流式照搬（STORED）
                            buf = r.read(_COPY_CHUNK)
                            if not buf:
                                break
                            w.write(buf)
                else:                          # 小成员：按原压缩照搬
                    dst.writestr(info, src.read(info.filename),
                                 compress_type=info.compress_type)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
