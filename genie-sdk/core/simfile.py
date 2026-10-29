"""仿真文件 ``.gensim`` 容器——**格式正典**（G4a 前置件，2026-10-30）。

一个 ``.gensim`` = 一个仿真（2026-10 站长裁决）。zip 容器，成员固定四类：

- ``simconfig.json``（必选）——SimConfig JSON；
- ``workspace/<basename>``（可选，平铺、无子目录）——工作区文件快照；
- ``results/<basename>``（可选）——随仿真带的结果 h5；
- ``state.json``（可选，与 ``results/`` 成对）——``job_id`` / ``state`` /
  ``executed_steps`` / ``dt`` / ``grid_shape``。

SDK 公开面 = ``Simulation.save(path)`` / ``genie.open(path)``；Studio 的
``/api/sim/export|import`` 只是本模块的 HTTP 薄壳。成员白名单、路径守卫、
原子写**只在这里维护一份**（单一来源纪律，G87）。

**不设大小上限**（2026-10-30 站长裁）：容器装的是用户自己的仿真产物
（结果 h5 可能很大），"能装多少"是用户磁盘的事，不在格式层设保姆式闸门。

写法纪律：先写 ``.part`` 再 ``os.replace``——跑一半崩了不留烂档。
"""

from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path

__all__ = [
    "SimfileError", "SimfileFormatError",
    "SIMCONFIG", "STATE",
    "member_kind", "validate_structure", "read_cfg", "inspect",
    "write", "result_state", "result_bytes", "state",
]

SIMCONFIG = "simconfig.json"
STATE = "state.json"


class SimfileError(ValueError):
    """.gensim 容器错误基类（SDK 面按 ValueError 捕获）。"""


class SimfileFormatError(SimfileError):
    """结构/成员名非法（Studio 映射 422）。"""


def _ensure_suffix(target) -> Path:
    p = Path(target)
    return p if p.suffix.lower() == ".gensim" else p.with_name(p.name + ".gensim")


def member_kind(name: str) -> str:
    """成员名 → 类别（``cfg`` / ``state`` / ``workspace`` / ``result``）。

    结构非法（子目录、``..``、未知目录/根级成员）→ ``SimfileFormatError``。
    """
    if not isinstance(name, str) or not name:
        raise SimfileFormatError("仿真文件含空成员名——拒绝")
    if "/" not in name:
        if name == SIMCONFIG:
            return "cfg"
        if name == STATE:
            return "state"
        raise SimfileFormatError(
            f"仿真文件含未知根级成员（{name}）——只认 "
            f"{SIMCONFIG} / {STATE}")
    prefix, _, base = name.partition("/")
    if name.endswith("/"):
        raise SimfileFormatError(f"仿真文件含目录成员（{name}）——只认文件")
    if (not base or "/" in base or base in (".", "..")
            or base.startswith("/") or "\\" in base):
        raise SimfileFormatError(
            f"仿真文件含容器外成员（{name}）——拒绝（成员必须平铺、无子目录）")
    if prefix == "workspace":
        return "workspace"
    if prefix == "results":
        return "result"
    raise SimfileFormatError(
        f"仿真文件含未知成员目录（{prefix}/）——只认 workspace/ 与 results/")


def validate_structure(zf: zipfile.ZipFile) -> list[str]:
    """结构校验（不读内容）：必选成员存在、成员名合法、results↔state 成对。"""
    names = zf.namelist()
    if SIMCONFIG not in names:
        raise SimfileFormatError(
            f"仿真文件缺 {SIMCONFIG}（不是 .gensim 容器？）")
    for n in names:
        member_kind(n)
    if any(n.startswith("results/") for n in names) and STATE not in names:
        raise SimfileFormatError(
            "容器含 results/ 但缺 state.json（job_id）——无法还原结果态")
    if STATE in names and not any(n.startswith("results/") for n in names):
        raise SimfileFormatError(
            "容器含 state.json 但无 results/——结果与元信息必须成对")
    return names


def _open_zip(source):
    """source = 路径 / 字节 / 可读文件对象 → 打开的 ZipFile（坏容器→格式错）。"""
    try:
        return zipfile.ZipFile(source)
    except (zipfile.BadZipFile, OSError) as e:
        raise SimfileFormatError(
            f"不是合法的仿真文件（.gensim 应为 zip 容器）：{e}") from e


def read_cfg(source) -> dict:
    """读容器里的 SimConfig（不校验 schema——调用方用同一双 validate 手）。"""
    with _open_zip(source) as zf:
        validate_structure(zf)
        try:
            cfg = json.loads(zf.read(SIMCONFIG).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise SimfileFormatError(
                f"{SIMCONFIG} 不是合法 JSON（{e}）") from e
    if not isinstance(cfg, dict):
        raise SimfileFormatError(f"{SIMCONFIG} 须是 SimConfig JSON 对象")
    return cfg


def result_bytes(source) -> bytes | None:
    """容器里随仿真带的结果 h5 字节（**P1 读取原语**，G182）。

    没有 ``results/`` → None（未跑过/失败点的文档）；多个结果成员 =
    格式错（一个仿真一份结果，不猜）。
    """
    with _open_zip(source) as zf:
        names = validate_structure(zf)
        hits = [n for n in names if n.startswith("results/")]
        if not hits:
            return None
        if len(hits) > 1:
            raise SimfileFormatError(
                f"仿真文件含 {len(hits)} 个结果成员——一个仿真只认一份结果")
        data = zf.read(hits[0])
        return data


def state(source) -> dict:
    """容器里的 ``state.json``（结果元信息：job_id/executed_steps/dt/
    grid_shape）；没有 → ``{}``（未跑过的文档）。P3 定位器用它比对 job_id。"""
    with _open_zip(source) as zf:
        names = validate_structure(zf)
        if STATE not in names:
            return {}
        try:
            data = json.loads(zf.read(STATE).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
    return data if isinstance(data, dict) else {}


def inspect(path) -> dict:
    """容器摘要：``{cfg, members, job_id, has_results}``（打开前预览用）。"""
    with _open_zip(path) as zf:
        names = validate_structure(zf)
        cfg = read_cfg(path)
        job_id = ""
        if STATE in names:
            try:
                st = json.loads(zf.read(STATE).decode("utf-8"))
                job_id = str((st or {}).get("job_id") or "")
            except (UnicodeDecodeError, json.JSONDecodeError):
                job_id = ""
    return {"cfg": cfg, "members": names, "job_id": job_id,
            "has_results": any(n.startswith("results/") for n in names)}


def result_state(source, *, job_id: str = "") -> dict:
    """读结果 h5 根 attrs → ``state.json`` 形态（读不到就给最小 dict）。

    ``source`` 可以是路径，也可以是**结果 h5 字节**（G182 P2：run 成功时
    结果在内存里，零临时文件直接写文档）。
    """
    name = ""
    if isinstance(source, (bytes, bytearray)):
        source = io.BytesIO(source)
    else:
        try:
            name = Path(source).name
        except TypeError:
            name = ""
    st: dict = {"job_id": job_id or name.removesuffix("_results.h5"),
                "state": "done"}
    try:
        import h5py
        with h5py.File(source, "r") as h5:
            a = h5.attrs
            for k, cast in (("executed_steps", int), ("dt", float)):
                if k in a:
                    st[k] = cast(a[k])
            if "grid_shape" in a:
                st["grid_shape"] = [int(v) for v in a["grid_shape"]]
    except Exception:   # noqa: BLE001 —— best-effort：坏 h5 不该拦存档
        pass
    return st


def write(target, cfg: dict, *, result_path=None, result_data: bytes | None = None,
          state: dict | None = None, files=None) -> Path:
    """写一个 .gensim（原子替换）。返回最终路径。

    Parameters
    ----------
    target : str | Path
        目标路径；不带 ``.gensim`` 后缀时自动补。
    cfg : dict
        非空 SimConfig JSON（调用方负责先 validate——本函数只管容器）。
    result_path : str | Path | None
        结果 h5 文件；给了就写 ``results/`` + ``state.json``。
    result_data : bytes | None
        结果 h5 **字节**（G182 P2：run 成功时在内存里，避免先落临时文件）；
        与 ``result_path`` 二选一。成员名用 ``state["job_id"]``。
    state : dict | None
        ``state.json`` 内容；缺省从结果（路径或字节）的 h5 根 attrs 反推
        （``result_state``）。
    files : iterable[str | Path] | None
        工作区文件快照（只取基名，平铺进 ``workspace/``；点文件与
        ``.part`` 跳过）。
    """
    if not isinstance(cfg, dict) or not cfg:
        raise SimfileError("cfg 须非空 SimConfig dict")
    if result_path is not None and result_data is not None:
        raise SimfileError("result_path 与 result_data 二选一")
    target = _ensure_suffix(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".part")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as zf:
            zf.writestr(SIMCONFIG,
                        json.dumps(cfg, ensure_ascii=False, indent=2))
            for p in files or []:
                p = Path(p)
                if (not p.is_file() or p.name.startswith(".")
                        or p.name.endswith(".part")):
                    continue
                zf.write(p, arcname="workspace/" + p.name)
            if result_data is not None:
                st = dict(state or result_state(result_data))
                jid = str(st.get("job_id") or "").strip()
                if not jid:
                    raise SimfileError(
                        "结果字节缺 job_id——state 里必须有 job_id 才能命名成员")
                zf.writestr("results/" + f"{jid}_results.h5", result_data)
                zf.writestr(STATE, json.dumps(st, ensure_ascii=False, indent=2))
            elif result_path is not None:
                rp = Path(result_path)
                if not rp.is_file():
                    raise SimfileError(f"结果文件不存在：{rp}")
                zf.write(rp, arcname="results/" + rp.name)
                st = dict(state or result_state(rp))
                zf.writestr(STATE, json.dumps(st, ensure_ascii=False, indent=2))
        os.replace(tmp, target)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return target
