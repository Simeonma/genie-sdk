"""genie.worker.run — 动作面：f.submit（融合）+ 读数四动词 + 内部原语
+ 等待循环 + 落位 / 灌 results + 文档命令（⚑P6 第一版）。

- 公开动词一个 ``f.submit(n_gpu=None)``（Q2.2 收口；2026-11-05 站长
  改拍收窄**单仿真**）：升格当前模型 + ``validate_for_ui`` → ``/gw``
  提交（线格式批 1）→ ``.gensim`` 落位（有模型、无 results、inflight
  记本批——「提交即作废」的实现；落位算一次保存，digest = 落位
  指纹）→ 等终态 → 灌 ``results/<job>.h5``（archive.inject_result，
  一步原子；成功清 inflight）→ 返回 job id（⚑P7：``str``）；失败 /
  取消抛 ``GenieJobError``。批量（含异配置）不在公开面——唯一入口
  ``f.param_sweep``（Q2.2-6 改拍）；主文档模型恒 = 提交的模型。
  实现即原语组合：``_submit``（静默提交）→ 落位 → ``_wait``（全仓
  唯一等待实现）→ 灌。
- 内部原语（Q8 下划线，名字站长核准 2026-11-04）：``_submit``（立即返
  句柄，不落位不等待）/ ``_wait``（按完成序收终态，只报不判——失败
  策略归驱动层，Q2.2-6）/ ``_cancel``（砍在飞）——全程静默（终端零
  文字；``_wait`` 等待期间照刷进度卡片）。
- 读数四动词（Q4.5；解析口径住 core/results，本模块只做「选成员 +
  开文件」）：``f.results()`` → SimData 惰性视图（**无参 = 当下那份**
  ——submit 收窄后档恒单成员，2026-11-05 同款收窄；多成员 = 档异常
  一句话，点名通道删除）；``f.get_time / get_freq / get_index(name)``
  → Time/Freq/IndexResult（eager 解析、读缓存即用即清；name 缺省 =
  该域单组直取，多组点名）。无结果成员一句话明说（零状态守卫，
  不谈 state）；读数零终端行。
- 终端五行（worker 图 §4 唯一一份，golden 锁字面；全英文、动词开头）：
  ``submitted job {id}`` / ``done job {id}: result saved`` /
  ``failed job {id}: {detail}`` / ``canceled job {id}``（终态 killed /
  canceled 皆此措辞，Q2.2-3）/ ``post failed {name} job {id}: {detail}``
  （Q11 第五行——仅 post 失败时发，成功零行；submit 照常返回）。内部原语
  零终端行；``echo=False`` 只关用户 stdout，worker 自己的行不受影响。
- 后处理四动词（Q11 五拍：add / update / run / delete）：
  ``f.add_post_analyze(name)`` 新建**空**配方（占位）；``f.update_post_analyze
  (name, script_location)`` 把 py 文件全文导入档里代码位（**白名单门**：
  ``f`` 只许读结果四动词，其余拒；档内代码 = 正本，本地再改不 update
  不生效）；``f.run_post_analyze(name, script)`` **直接调用**计算（script =
  调用表达式如 ``calc_q(a=1e-6)``，返回值即产出 dict → ``post/<name>.npz``，
  调用行记进档；``run(name)`` 重跑、``run()`` 跑全部完整配方）；
  ``f.delete_post_analyze(name)`` 删（档同步 + 孤儿 npz 移除）。执行核心
  ``_post_out``：ns 浅拷贝 + 注入 ``d``（当下结果，全局变量）→ exec 档内
  代码 → eval 调用行；恢复 = ``load_recipes``（条目 ``{code, script}``）。
  submit 尾部 ``_run_post`` 对**完整**配方逐条自动跑（占位静默跳过；单条
  失败一行、不连坐）。
"""
from __future__ import annotations

import ast
import io
import os
import shutil
import tempfile
import time
from pathlib import Path

import numpy as np

from genie import ipc
from genie.core import archive
from genie.core import material as core_material
from genie.core import results as core_results
from genie.core.model import Model
from genie.core.model.draft import digest
from genie.net import (TERMINAL_BAD, TERMINAL_OK, GenieConfigError,
                       GenieJobError)
from genie_protocol.resolve import validate_for_ui

__all__ = ["F", "bind"]

#: 等待循环节拍（秒）——每秒自查一次 /gw（站长终版 2026-11-04）。
#: 固定常量不是旋钮（Q2.2-4：不进签名）；测试改模块属性。
_POLL_S = 1.0

#: 文档格式版本（state.json format 栏）
_FORMAT = 2

# 终端五行（唯一一份；test_run_journey_wording 锁字面；第五行 Q11——仅
# post 失败时发，成功零行；三拍起带配方名）
ROW_SUBMIT_ONE = "submitted job {id}"
ROW_DONE = "done job {id}: result saved"
ROW_FAILED = "failed job {id}: {detail}"
ROW_CANCELED = "canceled job {id}"
ROW_POST_FAILED = "post failed {name} job {id}: {detail}"

#: ``f.add_post_analyze`` 哨兵（区分「无参」与显式值）。
_UNSET = object()


def _normalize(model, cfg_or_cfgs):
    """参数归一：None = 升格活模型；dict = 单；list = 批。→ (single, [cfg…])。"""
    if cfg_or_cfgs is None:
        return True, [model.to_config()]
    if isinstance(cfg_or_cfgs, dict):
        return True, [cfg_or_cfgs]
    return False, list(cfg_or_cfgs)


def _validate(cfgs) -> None:
    """提交前预检（Q4.4 唯一校验面）；不过 → GenieConfigError（错误路径
    逐字段带上，用户不开浏览器也排得了查）。"""
    errs = []
    for cfg in cfgs:
        errs.extend(validate_for_ui(cfg))
    if errs:
        raise GenieConfigError(
            "run.precheck: " + "; ".join(
                f"{e.get('loc') or 'cfg'}: {e.get('msg')}" for e in errs),
            detail=errs)


def _payload(cfgs, n_gpu):
    """动作参数只进提交载荷、不回写模型（Q7.4：参数集 = RunSpec 字段
    一比一，现仅 n_gpu）。"""
    if n_gpu is None:
        return cfgs
    return [{**c, "run": {**(c.get("run") or {}), "n_gpu": n_gpu}}
            for c in cfgs]


def _state_for(model, cfg: dict, inflight, staged_digest=None) -> dict:
    return {"format": _FORMAT,
            "allocator": model.last_id,
            "digest": staged_digest or digest(cfg),
            "inflight": list(inflight)}


def _stage(env, model, ids) -> str:
    """落位（Q4.3 补）：整份写、无 results（提交即作废——旧 post 产出
    一并消失，Q11 细节 9）、inflight 记本批；digest = 落位指纹（落位算
    一次保存——失败停留 = 模型不 dirty）；配方集合随内存现值入档
    （Q11 三拍）。"""
    cfg = model.to_config()
    archive.save(env.doc_path, simconfig=cfg,
                 state=_state_for(model, cfg, ids),
                 post_recipes=env.post_recipes)
    return cfg


def _progress_row(by_id: dict) -> dict:
    """等待循环每拍的 rollup（ipc kind=progress；只显示不判终态——批 3）。"""
    done = sum(1 for s in by_id.values() if s["state"] in TERMINAL_OK)
    failed = sum(1 for s in by_id.values() if s["state"] in TERMINAL_BAD)
    jobs = {}
    for jid, s in by_id.items():
        item = {"state": s["state"]}
        if "progress" in s:
            item["progress"] = s["progress"]
        jobs[jid] = item
    return ipc.out(f"{done + failed}/{len(by_id)}",
                   kind=ipc.OUT_KIND_PROGRESS,
                   data={"total": len(by_id), "done": done,
                         "failed": failed, "jobs": jobs})


def _resolve_result(env) -> str:
    """当下结果 = 档里唯一 results 成员（submit 收窄后恒单份；
    2026-11-05 同款收窄：无 job_id——多成员 = 档异常一句话，点名
    通道删除）。无结果成员一句话明说（零状态守卫：不谈 state、
    不谈终态词）。"""
    doc = env.doc_path
    jobs = archive.result_jobs(doc) if doc and os.path.isfile(doc) else []
    if not jobs:
        raise RuntimeError(
            "no results in this .gensim — never succeeded or "
            "invalidated by a newer batch")
    if len(jobs) > 1:
        raise RuntimeError(
            f"multiple results members ({', '.join(jobs)}) in this "
            f".gensim — v2 holds exactly one (the current run)")
    return jobs[0]


def load_model(doc_path) -> tuple[Model, dict]:
    """``.gensim`` → (Model, state)：发号器取 max（Q1.2.5）——启动恢复
    与 ``f._open`` 的单一实现（批 9b；main 薄壳化调用本函数）。读不进
    （缺文件 / 非 .gensim / 非四栏 state）→ RuntimeError 一句话明报。"""
    try:
        cfg = archive.read_config(doc_path)
        state = archive.read_state(doc_path)
    except Exception as e:                      # noqa: BLE001 —— 一句话明报
        raise RuntimeError(f"cannot open {str(doc_path)!r}: {e}") from e
    return Model.from_config(cfg, next_id=state.get("allocator")), state


def load_recipes(doc_path) -> dict:
    """``.gensim`` → 具名配方集合 ``{name: {code, script}}``；无档 /
    成员缺席 / 读不进 → ``{}``（Q11 细节 8：启动恢复与 ``f._open`` 的
    单一实现）。旧形态条目（纯 script 字符串 / 四拍 {script, files}）
    归一为五拍条目（code 取不到 = 空——重新 update 导入）。"""
    if doc_path and os.path.isfile(doc_path):
        try:
            raw = archive.read_recipes(doc_path)
        except Exception:                       # noqa: BLE001 —— 恢复面宽容
            return {}
        out = {}
        for name, val in raw.items():
            if isinstance(val, str):            # 三拍前旧档条目
                val = {"script": val}
            out[name] = {"code": val.get("code", ""),
                         "script": val.get("script", "")}
        return out
    return {}


#: 配方合约白名单（Q11 五拍）：``f`` 在配方代码里只许读结果四动词；
#: 其余（submit / 建模四集合 / 文档命令 / post 动词）一律拒装。
_POST_F_OK = ("results", "get_time", "get_freq", "get_index")


def _gate_recipe(code: str, where: str) -> None:
    """白名单门（Q11 五拍）：``f`` 的引用只许 ``_POST_F_OK`` 读动词；
    其他属性 / 裸 ``f`` → RuntimeError 一句话明报（update 拒装、run
    拒跑）。"""
    tree = ast.parse(code)
    ok_ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) \
                and isinstance(node.value, ast.Name) and node.value.id == "f":
            if node.attr not in _POST_F_OK:
                raise RuntimeError(
                    f"{where}: f.{node.attr} is not allowed in a "
                    f"post_analyze recipe — recipes may only read results "
                    f"({', '.join('f.' + a for a in _POST_F_OK)}); keep "
                    f"recipe files pure (read results + post-process)")
            ok_ids.add(id(node.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "f" \
                and isinstance(node.ctx, ast.Load) and id(node) not in ok_ids:
            raise RuntimeError(
                f"{where}: bare 'f' is not allowed in a post_analyze "
                f"recipe — recipes may only read results via "
                f"{', '.join('f.' + a for a in _POST_F_OK)}")


def _check_post_out(out: dict) -> None:
    """npz 值域（Q11 细节 10）：标量 / ndarray / str 收，嵌套 dict / list
    等拒收——一句话明报（键名 + 实际类型）。"""
    for key, val in out.items():
        if isinstance(val, (str, bool, int, float, complex,
                            np.generic, np.ndarray)):
            continue
        raise RuntimeError(
            f"post_out[{key!r}]: npz values must be scalar / ndarray / "
            f"str, got {type(val).__name__}")


class F:
    """f = core ``Model`` + 动作（Q6.10：语义住 core、绑定与动作实现住
    worker）。属性读写全部委托模型；动作七个（submit / _submit / _wait /
    _cancel / _save / _open / _dirty）+ 读数四动词 + 材料五动词
    （Q1.5：fit_nk / assemble_nk / load_nk / mat_from_nk /
    list_library_materials）；**没有 cancel**——公开取消是 session 拦截
    的命令，不在 worker（Q2.2-3）。"""

    __slots__ = ("_model", "_env")

    def __init__(self, model, env):
        object.__setattr__(self, "_model", model)
        object.__setattr__(self, "_env", env)

    def __getattr__(self, name):
        return getattr(self._model, name)

    def __setattr__(self, name, value):
        setattr(self._model, name, value)

    def __repr__(self):
        return f"<f model={self._model!r}>"

    # -- 公开动词（唯一，Q2.2 收口；2026-11-05 收窄单仿真）-----------

    def submit(self, *, n_gpu=None):
        """跑我的模型一次（2026-11-05 收窄：单仿真——无 cfg 参数，升格
        当前模型提交；批量 = ``f.param_sweep``）。升格预检 → 提交（线
        格式批 1）→ 落位（提交即作废，Q4.3）→ 等终态 → 灌 result 自动
        存 → 返回 job id（``str``，⚑P7）；失败 / 取消抛 ``GenieJobError``
        （failed / canceled 两款措辞）。"""
        env = self._env
        cfg = self._model.to_config()                 # 升格：活模型 → 严格配置
        _validate([cfg])
        jid = self._submit(cfg, n_gpu=n_gpu)          # 静默提交（批 1 线形）
        env.emit(ipc.out(ROW_SUBMIT_ONE.format(id=jid)))
        staged = _stage(env, self._model, [jid])      # 落位：出手即作废旧批
        staged_digest = digest(staged)
        outcome = self._wait([jid])[0]                # 全仓唯一等待实现
        if outcome["state"] in TERMINAL_OK:
            env.emit(ipc.out(ROW_DONE.format(id=jid)))
            archive.inject_result(env.doc_path, jid,
                                  env.client().stream_result(jid),
                                  _state_for(self._model, staged, [],
                                             staged_digest))
            self._run_post(jid)                       # Q11 尾钩（配方存在才跑）
            return jid
        if outcome["state"] == "failed":
            env.emit(ipc.out(ROW_FAILED.format(
                id=jid, detail=outcome.get("error") or outcome["state"])))
        else:                                          # killed / canceled
            env.emit(ipc.out(ROW_CANCELED.format(id=jid)))
        raise GenieJobError(jid, outcome["state"], error=outcome.get("error"))

    # -- 读数四动词（Q4.5；解析口径住 core/results，零终端行）---------

    def results(self):
        """当下那份结果 → SimData 惰性包装（``data.<mon>.<field>`` 直达；
        无参——submit 单仿真后档恒单成员，2026-11-05 收窄）。读缓存
        目录归返回对象（close 即清）。"""
        env = self._env
        jid = _resolve_result(env)
        scratch = tempfile.mkdtemp(prefix="genie-results.")
        path = archive.extract_result(env.doc_path, scratch, jid)
        return core_results.open_result(path, job_id=jid, scratch=scratch)

    def get_time(self, name=None):
        """时域监视器 → TimeResult（eager 解析；缺省 = 该域单组直取，多组点名）。"""
        return self._read_monitor("t", name)

    def get_freq(self, name=None):
        """频域监视器 → FreqResult（eager 解析；缺省 = 该域单组直取，多组点名）。"""
        return self._read_monitor("f", name)

    def get_index(self, name=None):
        """index 监视器 → IndexResult（eager 解析；缺省 = 该域单组直取，多组点名）。"""
        return self._read_monitor("i", name)

    def _read_monitor(self, domain, name):
        env = self._env
        jid = _resolve_result(env)
        scratch = tempfile.mkdtemp(prefix="genie-results.")
        try:
            path = archive.extract_result(env.doc_path, scratch, jid)
            data = core_results.open_result(path, job_id=jid)
            try:
                return data.get_time(name) if domain == "t" else \
                    data.get_freq(name) if domain == "f" else \
                    data.get_index(name)
            finally:
                data.close()
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    # -- 后处理四动词（Q11 五拍：add / update / run / delete）----------

    def add_post_analyze(self, name):
        """**新建空配方**（Q11 五拍）：在档里新建一条**空** post_analyze
        （无代码、无调用行——占位，代码走 ``update`` 导入、调用行走
        ``run`` 记录）。已存在同名 → 一句话明报。"""
        env = self._env
        if not name:
            raise TypeError("add_post_analyze(name): name is required")
        if name in env.post_recipes:
            raise RuntimeError(
                f"post_analyze {name!r} already exists — "
                f"update_post_analyze to change its code")
        env.post_recipes[name] = {"code": "", "script": ""}
        self._sync_recipes()
        return None

    def update_post_analyze(self, name, script_location):
        """**导入代码**（Q11 五拍）：把 ``script_location`` 处 py 文件的
        **全部内容**写进档里该配方的代码位（正本——本地文件再改不
        update 不生效）。**白名单门在此执行**（``f`` 只许读结果四动词，
        其余拒）。要求配方已存在（先 add）；立即写档；不跑。"""
        env = self._env
        self._require_recipe(name)
        try:
            code = Path(script_location).read_text(encoding="utf-8")
        except OSError as e:
            raise RuntimeError(
                f"cannot read recipe file {str(script_location)!r}: "
                f"{e}") from e
        _gate_recipe(code, f"recipe file {str(script_location)!r}")
        env.post_recipes[name]["code"] = code
        self._sync_recipes()
        return None

    def run_post_analyze(self, name=None, script=_UNSET):
        """**调用与计算**（Q11 五拍）：``run_post_analyze(name, script)``
        ——script = **对函数的直接调用**（如 ``calc_q(a=1e-6)``，表达式；
        返回值即产出 dict → ``post/<name>.npz``）并**记进档**（下次
        ``run(name)`` / submit 尾钩自动重跑都用它）；``run(name)`` =
        用存的调用行重跑；``run()`` = 跑全部**完整**配方 →
        ``{name: out}``（不完整的静默跳过——占位语义）。环境：``d`` =
        当下结果（SDK 注入的全局变量，函数体内直接用）+ **档内代码为
        正本**（exec 覆盖同名现场函数）。异常上抛（exec 红）。"""
        env = self._env
        if name is None:
            if script is not _UNSET:
                raise TypeError("run_post_analyze: script requires a name")
            complete = {k: v for k, v in env.post_recipes.items()
                        if v["code"] and v["script"]}
            if not complete:
                raise RuntimeError(
                    "no complete post_analyze recipes — add_post_analyze, "
                    "update_post_analyze(code file), run_post_analyze(name, "
                    "script)")
            jid = _resolve_result(env)
            out = {each: self._post_one(each, entry, jid)
                   for each, entry in complete.items()}
            self._sync_recipes()
            return out
        self._require_recipe(name)
        entry = env.post_recipes[name]
        if script is not _UNSET:
            if not isinstance(script, str):
                raise TypeError("run_post_analyze: script must be str")
            _gate_recipe(script, f"recipe script {name!r}")
            entry["script"] = script          # 记调用行（先记后跑——跑失败
            self._sync_recipes()              # 也保留，尾钩 / 重跑用得上）
        if not entry["code"]:
            raise RuntimeError(
                f"post_analyze {name!r} has no code — update_post_analyze"
                f"(name, script_location) first")
        if not entry["script"]:
            raise RuntimeError(
                f"post_analyze {name!r} has no call script — "
                f"run_post_analyze(name, script) first")
        jid = _resolve_result(env)
        out = self._post_one(name, entry, jid)
        self._sync_recipes()
        return out

    def delete_post_analyze(self, name):
        """**删一条**（Q11）：内存注册表移除；档在则同步——配方成员
        重写 + ``post/<name>.npz`` 移除（否则 `_save` 照搬会把孤儿 npz
        又带走）。幂等：未知名静默。"""
        env = self._env
        env.post_recipes.pop(name, None)
        doc = env.doc_path
        if doc and os.path.isfile(doc):
            archive.write_recipes(doc, env.post_recipes)
            archive.remove_post(doc, name)
        return None

    def _require_recipe(self, name) -> None:
        if name not in self._env.post_recipes:
            raise RuntimeError(
                f"no post_analyze named {name!r} — add_post_analyze(name) "
                f"first")

    def _sync_recipes(self) -> None:
        """注册表 → 档（在档时立即原子写；无档 = 随落位 / `_save`）。"""
        doc = self._env.doc_path
        if doc and os.path.isfile(doc):
            archive.write_recipes(doc, self._env.post_recipes)

    def _post_one(self, name, entry, jid):
        out, blob = self._post_out(name, entry, jid)
        archive.inject_post(self._env.doc_path, name, blob)
        return out

    def _run_post(self, jid: str) -> None:
        """submit 尾部自动跑（Q11：唯一挂点）：对**完整**配方逐条跑
        （code + script 都在——add 出来的空占位静默跳过）；单条失败
        一行 ``post failed {name}``、其余照跑、submit 照常返回（内核
        成功就是成功——不连坐的强形式）；成功零行。"""
        env = self._env
        for name, entry in env.post_recipes.items():
            if not (entry["code"] and entry["script"]):
                continue                      # 占位 / 未完整：跳过
            try:
                _out, blob = self._post_out(name, entry, jid)
                archive.inject_post(env.doc_path, name, blob)
            except Exception as e:             # noqa: BLE001 —— 一行明报不连坐
                env.emit(ipc.out(ROW_POST_FAILED.format(
                    name=name, id=jid,
                    detail=f"{type(e).__name__}: {e}")))

    def _post_out(self, name, entry, jid: str) -> "tuple[dict, bytes]":
        """配方执行核心（Q11 五拍）：ns = 会话命名空间浅拷贝 + 注入
        ``d``（当下结果 SimData，**全局变量**——函数体内直接用）→
        **exec 档内代码**（正本，覆盖同名现场函数；白名单门在 update
        时已保证无 f 动作）→ **eval 调用行**（直接调用表达式，返回值即
        产出；dict + 值域校验）→ (dict, npz bytes)。异常原样上抛；ns
        用完即弃（复制是、合并否）。"""
        env = self._env
        scratch = tempfile.mkdtemp(prefix="genie-post.")
        try:
            path = archive.extract_result(env.doc_path, scratch, jid)
            data = core_results.open_result(path, job_id=jid,
                                            scratch=scratch)
            try:
                ns = dict(env.current_ns())       # 浅拷贝：现场常量打底
                ns["d"] = data                    # d = 当下结果（全局注入）
                exec(compile(entry["code"],
                             f"<genie-post-code:{name}>", "exec"), ns)
                out = eval(compile(entry["script"],
                                   f"<genie-post-run:{name}>", "eval"), ns)
                if not isinstance(out, dict):
                    raise RuntimeError(
                        f"post_analyze {name!r} call must return a dict, "
                        f"got {type(out).__name__}")
                _check_post_out(out)
                buf = io.BytesIO()
                np.savez(buf, **out)
                return out, buf.getvalue()
            finally:
                data.close()                      # scratch 连目录一起清
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    # -- 材料动词（Q1.5；实现住 core/material，零终端行）------------

    def fit_nk(self, wl, n, k, fit_range=(400e-9, 800e-9), n_drude=0,
               n_lorentz=2, maxfev=5000):
        """**单分量** nk → 拟合数值（纯标量纯数值，Q1.5 二拍+三拍；
        六键无节点——材料节点唯一工厂 = ``f.assemble_nk``，iso 亦经
        组装）。"""
        return core_material.fit_nk(wl, n, k, fit_range=fit_range,
                                    n_drude=n_drude, n_lorentz=n_lorentz,
                                    maxfev=maxfev)

    def assemble_nk(self, fits, name=None):
        """逐分量 fit_nk 结果 → 单个材料节点（对角 xx/yy/zz 必给、
        非对角上三角一名镜像 mount 保对称；rank-1 = 内核原生形态）。"""
        return core_material.assemble_nk(fits, name=name)

    def load_nk(self, name, component=None):
        """本地 n-k 库 → (wl_m, n, k)（iso 直取；复合材料必须点名分量
        ——单直多报，四拍③）。"""
        return core_material.load_nk(name, component=component)

    def mat_from_nk(self, name):
        """库名 → 材料节点一条龙（零旋钮便利层，四拍①；入模一行
        ``f.mat.add_lorentz(node)``）。"""
        return core_material.mat_from_nk(name)

    def list_library_materials(self):
        """本地库材料名清单（sorted）。"""
        return core_material.list_library_materials()

    # -- 内部原语（Q8 下划线；驱动编排用，全程静默）-------------------

    def _submit(self, cfg_or_cfgs=None, *, n_gpu=None):
        """提交即返句柄（不落位、不等待、终端零文字；单 = 批 1、与公开面
        同形）。落位是主文档语义（Q4.3），驱动层的点文档归驱动自己。"""
        single, cfgs = _normalize(self._model, cfg_or_cfgs)
        _validate(cfgs)
        ids = self._env.client().submit(_payload(cfgs, n_gpu))  # 体恒批形
        if not isinstance(ids, list):
            ids = [ids]
        self._env.inflight.update(ids)
        return ids[0] if single else ids

    def _wait(self, handles):
        """等一批 job 终态，按完成顺序收；等待期间刷进度卡片（终端零
        文字）。只报终态不判失败——失败策略归驱动层（Q2.2-6：
        ``on_failure`` abort = 驱动用 ``_cancel`` 砍剩余在飞）。
        返回 ``[{job_id, state, error?}]``（完成序）。"""
        ids = [handles] if isinstance(handles, str) else list(handles)
        client = self._env.client()
        order: list = []
        states: dict = {}
        while len(order) < len(ids):
            by_id = {s["job_id"]: s for s in client.job_statuses(ids)}
            self._env.emit(_progress_row(by_id))
            for jid in ids:
                if jid in states:
                    continue
                st = by_id[jid]["state"]
                if st in TERMINAL_OK or st in TERMINAL_BAD:
                    states[jid] = by_id[jid]
                    order.append(jid)
            if len(order) < len(ids):
                time.sleep(_POLL_S)
        self._env.inflight.difference_update(ids)
        return [{"job_id": j, "state": states[j]["state"],
                 **({"error": states[j]["error"]}
                    if states[j].get("error") else {})}
                for j in order]

    def _cancel(self, handles=None) -> None:
        """取消在飞（经 /gw 直拨；终端零文字）。``handles=None`` = 本 f
        全部在飞（exec 面追踪的 inflight）。"""
        ids = ([handles] if isinstance(handles, str) else list(handles)) \
            if handles is not None else list(self._env.inflight)
        client = self._env.client()
        for jid in ids:
            client.cancel(jid)
        self._env.inflight.difference_update(ids)

    # -- 文档命令（⚑P6 第一版实装：_save + 启动恢复；其余随真需求长）--

    def _save(self, path=None):
        """保存 / 另存为（不带路径 = 保存）。digest 更新为保存时指纹、
        inflight 清空、结果与 post 产出照搬（当前批）；配方集合 = 内存
        现值入档（Q11 细节 9）。另存为后活文档路径跟随。"""
        env = self._env
        target = path or env.doc_path
        cfg = self._model.to_config()
        archive.save(target, simconfig=cfg,
                     state=_state_for(self._model, cfg, []),
                     carry_result_from=env.doc_path,
                     post_recipes=env.post_recipes)
        env.doc_path = target
        return target

    def _open(self, path):
        """打开既有 `.gensim`（批 9b）：整份换模型（发号器取 max）+
        活文档路径跟随 + 即刻重注入 `f` + 配方集合随档换（Q11 细节 8）；
        结果成员留在档；未保存改动随换丢弃（Q1.3）。打不开 → 一句话
        明报，模型与路径都不动。"""
        env = self._env
        model, _state = load_model(path)        # 失败在赋值前：全不动
        env.model = model
        env.doc_path = str(path)
        env.post_recipes = load_recipes(str(path))
        env._reinject_f()                       # 同一 exec 后续语句即见新模型
        return str(path)

    def _dirty(self) -> bool:
        """模型指纹 ≠ 当前 `.gensim` 的 state.digest → True；无档 /
        档读不进 = True（落位算一次保存的对称面，Q4.3）。"""
        env = self._env
        doc = env.doc_path
        if not doc or not os.path.isfile(doc):
            return True
        try:
            state = archive.read_state(doc)
        except Exception:                       # noqa: BLE001 —— 无可对照保存
            return True
        return digest(self._model.to_config()) != state.get("digest")


def bind(model, env) -> F:
    """f 组合体（exec.py 只造绑定，动作实现住本模块——worker 图 §2）。"""
    return F(model, env)
