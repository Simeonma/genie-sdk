from __future__ import annotations

import re

"""八节生成器 —— SimConfig 各节 → 脚本行组。

domain / mesh / materials / geometry / source / monitor / run /
results——每函数对应一节（golden 语料按节覆盖），
"等于默认则省略"判据的默认值正典 = 协议 pydantic Field（_DEF，G99）。
"""
from ...models import BC_KINDS, FACES, LIBRARY_FIT_DEFAULTS
from ...models import _wire as _w
from ._emit import (_chain, _eq3, _norm_pol, _num, _num_arr, _num_or_list,
                    _pole_line, _slot_arr, _vertex_list)
from ._numfmt import _js_string, py_num, py_str


def _field_default(model, name):
    """协议 Field 默认值（省略判据的唯一真源——G99 清账 2026-10-27）。

    此前渲染器/构造器把默认值抄成字面量（0.9/12/128/…），协议默认一改，
    渲染器按旧值判省略、配置被静默改写，golden 也按旧口径锁不住。现在
    判据读 Field：协议默认一改，判据自动跟随；若字节因此变化，golden
    当场红，逼着重生成+评审，不再静默。
    """
    return model.model_fields[name].default


# 省略判据正典表（模块加载时从协议推导一次）。
_DEF = {
    "cfl": _field_default(_w.DomainSpec, "cfl"),
    "layers": _field_default(_w.BoundarySpec, "layers"),
    "kappa_max": _field_default(_w.BoundarySpec, "kappa_max"),
    "order": _field_default(_w.BoundarySpec, "order"),
    "max_refinement": _field_default(_w.MeshSpec, "max_refinement"),
    "min_cells": _field_default(_w.MeshSpec, "min_cells"),
    "growth_rate": _field_default(_w.RefineSpec, "growth_rate"),
    "mu": _field_default(_w.ConstMatSpec, "mu"),
    "eps_inf": _field_default(_w.LorentzMatSpec, "eps_inf"),
    "mu_inf": _field_default(_w.LorentzMatSpec, "mu_inf"),
    "priority": _field_default(_w.BoxSpec, "priority"),
    "delay": _field_default(_w.SoftSourceSpec, "delay"),
    "phase": _field_default(_w.SoftSourceSpec, "phase"),
    "amplitude": _field_default(_w.SoftSourceSpec, "amplitude"),
    "timeout_s": _field_default(_w.RunSpec, "timeout_s"),
    # G132（R17，G99 残余出清）：以下四处此前是渲染器本地字面量。
    "angle": _field_default(_w.PlaneWaveSpec, "theta"),   # theta/phi/psi 同默认
    "mon_size": _field_default(_w.TimeMonSpec, "size"),    # (0,0,0) 点缺省
    "stride": _field_default(_w.DownSampleSpec, "stride"),
    # polarization 缺省 = 线格式归一形态（Field 默认 None 的正典化）
    "pol_default": _w._norm_polarization(None),
    # R22 G159（R17 收口残余）：center 缺省（geometry/source/monitor
    # 三处此前各写 [0,0,0] 字面量）；index 监视器 size 缺省 = 全 None
    # 槽（与 time/freq 的 mon_size=(0,0,0) 是两个不同的正典值）。
    "center": _field_default(_w.SoftSourceSpec, "center"),
    "index_size": _field_default(_w.IndexMonSpec, "size"),
}

# soft_source 的 kind 判别值（R22 G159：判别与发射同源，不再双写）。
_KIND_MAGNETIC = "magnetic"


def _var_name(prefix: str, name, used: set) -> str:
    base = re.sub(r"[^A-Za-z0-9_]", "_", _js_string(name or ""))
    if not base or base[0].isdigit():
        base = "m" + base
    v = prefix + "_" + base
    k = 2
    while v in used:
        v = prefix + "_" + base + "_" + str(k)
        k += 1
    used.add(v)
    return v

def _gen_domain(d: dict, W: list, env: dict) -> list[str]:
    out = []
    d = d if isinstance(d, dict) else {}
    for ax in ("x", "y", "z"):
        r = _num_arr(d.get(ax + "_range"), 2, env, W, f"domain.{ax}_range")
        if r:
            out.append(f"f.domain.{ax}_range = ({r[0]}, {r[1]})")
        else:
            W.append(f"domain.{ax}_range 未设置/非法")
    tt = _num(d.get("total_time"), env, W, "domain.total_time")
    if tt:
        out.append("f.domain.total_time = " + tt)
    else:
        W.append("domain.total_time 未设置/非法")

    cfl = _num(d.get("cfl"), env, W, "domain.cfl")
    if d.get("cfl") is not None and cfl is not None \
            and d.get("cfl") != _DEF["cfl"]:
        out.append("f.domain.cfl = " + cfl)

    b = d.get("boundary") or {}
    for face in FACES:
        s = b.get(face)
        if not s or not isinstance(s, dict):
            continue
        if s.get("kind") not in BC_KINDS:
            W.append(f"domain.boundary.{face}.kind 非法")
            continue
        kw = []
        if s["kind"] == "pml":
            ly = _num(s.get("layers"), env, W, f"domain.boundary.{face}.layers")
            if ly is not None and s.get("layers") != _DEF["layers"]:
                kw.append("layers=" + ly)
            km = _num(s.get("kappa_max"), env, W, f"domain.boundary.{face}.kappa_max")
            if km is not None and s.get("kappa_max") != _DEF["kappa_max"]:
                kw.append("kappa_max=" + km)
            od = _num(s.get("order"), env, W, f"domain.boundary.{face}.order")
            if od is not None and s.get("order") != _DEF["order"]:
                kw.append("order=" + od)
        elif s["kind"] == "bloch":
            k = _num_arr(s.get("k"), 3, env, W, f"domain.boundary.{face}.k")
            if k:
                kw.append("k=(" + ", ".join(k) + ")")
            if s.get("from_source") is True:
                kw.append("from_source=True")
            nb = _num(s.get("n_background"), env, W, f"domain.boundary.{face}.n_background")
            if nb is not None:
                kw.append("n_background=" + nb)
        out.append("f.domain.boundary_condition(" + py_str(face) + ", "
                   + py_str(s["kind"]) + (", " + ", ".join(kw) if kw else "") + ")")
    return out

def _gen_mesh(m: dict, W: list, env: dict) -> list[str]:
    out = []
    m = m if isinstance(m, dict) else {}
    dx = _num(m.get("dx"), env, W, "mesh.dx")
    if dx:
        out.append("f.mesh.dx = " + dx)
    else:
        W.append("mesh.dx 未设置/非法")
    # dy/dz：Optional=None 默认，显式设置必生成（==dx 也生成）
    dy = _num(m.get("dy"), env, W, "mesh.dy")
    dz = _num(m.get("dz"), env, W, "mesh.dz")
    if dy:
        out.append("f.mesh.dy = " + dy)
    if dz:
        out.append("f.mesh.dz = " + dz)
    # 2026-09 方言开放：显式设置且 ≠ 默认才生成
    mr = _num(m.get("max_refinement"), env, W, "mesh.max_refinement")
    if mr is not None and m.get("max_refinement") != _DEF["max_refinement"]:
        out.append("f.mesh.max_refinement = " + mr)
    mc = _num(m.get("min_cells"), env, W, "mesh.min_cells")
    if mc is not None and m.get("min_cells") != _DEF["min_cells"]:
        out.append("f.mesh.min_cells = " + mc)
    for r in (m.get("refine") or []):
        if not isinstance(r, dict):    # G126：条目非对象不裸崩
            W.append("mesh.refine 条目不是对象（"
                     + _js_string(type(r).__name__) + "），已跳过")
            continue
        xr = _num_arr(r.get("x_range"), 2, env, W, "mesh.refine.x_range")
        yr = _num_arr(r.get("y_range"), 2, env, W, "mesh.refine.y_range")
        zr = _num_arr(r.get("z_range"), 2, env, W, "mesh.refine.z_range")
        f = None
        rf = r.get("factor")
        if isinstance(rf, (int, float)) and not isinstance(rf, bool):
            fs_scalar = _num(rf, env, W, "mesh.refine.factor")
            if fs_scalar is not None:
                f = fs_scalar
        elif isinstance(rf, (list, tuple)) and len(rf) == 3:
            fs = _num_arr(rf, 3, env, W, "mesh.refine.factor")
            if fs:
                f = "[" + ", ".join(fs) + "]"
        if not xr or not yr or not zr or not f:
            W.append("mesh.refine 条目不完整（range/factor 非法），已跳过")
            continue
        kw = ["factor=" + f]
        gr = _num(r.get("growth_rate"), env, W, "mesh.refine.growth_rate")
        if gr is not None and r.get("growth_rate") != _DEF["growth_rate"]:
            kw.append("growth_rate=" + gr)
        out.append(f"f.mesh.refine(({', '.join(xr)}), ({', '.join(yr)}), "
                   f"({', '.join(zr)}), {', '.join(kw)})")
    return out

def _gen_materials(list_, W: list, env: dict) -> list[str]:
    out = []

    def tag(i, name=None):
        return f"materials[{i}]" + (f" {name}" if name else "")

    for i, mt in enumerate(list_ or []):
        if not isinstance(mt, dict):   # G126：条目非对象不裸崩
            W.append(f"materials[{i}] 不是对象（"
                     + _js_string(type(mt).__name__) + "），已跳过")
            continue
        if mt.get("type") == "const":
            parts = []
            if py_str(mt.get("name")):
                parts.append(".name(" + py_str(mt.get("name")) + ")")
            else:
                W.append(tag(i) + "缺 name")
            eps_s = _num_or_list(mt.get("eps"), env, W, f"materials[{i}].eps")
            if eps_s is not None:
                parts.append(".eps(" + eps_s + ")")
            else:
                # G130（R17）：eps 是必填槽——缺 name 有 W，必填槽反而
                # 哑；保留骨架渲染（半成品容错语义）但必须出声。
                W.append(tag(i) + "缺 eps（必填槽——表单补齐后自动消失）")
            mu_s = _num_or_list(mt.get("mu"), env, W, f"materials[{i}].mu")
            if mu_s is not None and mt.get("mu") != _DEF["mu"]:
                parts.append(".mu(" + mu_s + ")")
            out.extend(_chain("f.mat.add_const()", parts))
        elif mt.get("type") == "lorentz":
            parts = []
            if py_str(mt.get("name")):
                parts.append(".name(" + py_str(mt.get("name")) + ")")
            else:
                W.append(tag(i) + "缺 name")
            ei = _num_or_list(mt.get("eps_inf"), env, W, f"materials[{i}].eps_inf")
            if ei is not None and mt.get("eps_inf") != _DEF["eps_inf"]:
                parts.append(".eps_inf(" + ei + ")")
            mi = _num_or_list(mt.get("mu_inf"), env, W, f"materials[{i}].mu_inf")
            if mi is not None and mt.get("mu_inf") != _DEF["mu_inf"]:
                parts.append(".mu_inf(" + mi + ")")
            for p in (mt.get("poles") or []):
                line = _pole_line(p, "eps", env, W, f"materials[{i}].poles")
                if line:
                    parts.append("." + line)
                else:
                    W.append(tag(i, mt.get("name")) + " pole 参数不全（gamma/omega 非法），已跳过")
            for p in (mt.get("mu_poles") or []):
                line = _pole_line(p, "mu", env, W, f"materials[{i}].mu_poles")
                if line:
                    parts.append("." + line)
                else:
                    W.append(tag(i, mt.get("name")) + " mu_pole 参数不全，已跳过")
            out.extend(_chain("f.mat.add_lorentz()", parts))
        elif mt.get("type") == "from_library":
            # D2/D5（ADR）定案：from_library 的渲染形态（fit 默认值的
            # 省略判据见 models.LIBRARY_FIT_DEFAULTS）；改形态必须同步
            # 重生成 golden 并逐条评审 diff。
            lib = py_str(mt.get("library"))
            nk = mt.get("nk")
            if not (isinstance(nk, dict)
                    and isinstance(nk.get("wavelength_m"), list)
                    and isinstance(nk.get("n"), list)
                    and isinstance(nk.get("k"), list)):
                nk = None
            if not lib and not nk:
                W.append(tag(i) + "缺 library（或 nk 数据），已跳过整条材料")
                continue
            lines = []
            lines.append("# 材料拟合（客户端 genie.material.fit_nk）")
            if lib:
                lines.append("_nk = genie.material.load_library_nk(" + lib + ")")
            else:
                wl = _num_arr(nk["wavelength_m"], len(nk["wavelength_m"]), env, W, f"materials[{i}].nk.wavelength_m")
                nn = _num_arr(nk["n"], len(nk["n"]), env, W, f"materials[{i}].nk.n")
                kk = _num_arr(nk["k"], len(nk["k"]), env, W, f"materials[{i}].nk.k")
                if wl is None or nn is None or kk is None:
                    W.append(tag(i) + "nk 数据含非法数值，已跳过整条材料")
                    continue
                lines.append("_nk = ([" + ", ".join(wl) + "], [" +
                             ", ".join(nn) + "], [" + ", ".join(kk) + "])")
            fit_kw = []
            fr = _num_arr(mt.get("fit_range"), 2, env, W, f"materials[{i}].fit_range")
            _fr_def = LIBRARY_FIT_DEFAULTS["fit_range"]
            # G126（R17）：省略判据只做相等比较，不做算术——fit_range 是
            # 合法表达式串槽，此前 `mt[...][0] - _fr_def[0]` 对 str 裸崩
            # TypeError（全文件唯一对槽值做算术的判据）。表达式串与任何
            # 默认值都不等 → 恒渲染，语义正确。
            fr_default = (fr is not None and mt.get("fit_range") is not None
                          and mt["fit_range"][0] == _fr_def[0]
                          and mt["fit_range"][1] == _fr_def[1])
            if fr is not None and not fr_default:
                fit_kw.append("fit_range=(" + ", ".join(fr) + ")")
            nd = _num(mt.get("n_drude"), env, W, f"materials[{i}].n_drude")
            if nd is not None and mt.get("n_drude") != LIBRARY_FIT_DEFAULTS["n_drude"]:
                fit_kw.append("n_drude=" + nd)
            nl = _num(mt.get("n_lorentz"), env, W, f"materials[{i}].n_lorentz")
            if nl is not None and mt.get("n_lorentz") != LIBRARY_FIT_DEFAULTS["n_lorentz"]:
                fit_kw.append("n_lorentz=" + nl)
            mf = _num(mt.get("maxfev"), env, W, f"materials[{i}].maxfev")
            if mf is not None and mt.get("maxfev") != LIBRARY_FIT_DEFAULTS["maxfev"]:
                fit_kw.append("maxfev=" + mf)
            lines.append(
                "_fit = genie.material.fit_nk(_nk"
                + (", " + ", ".join(fit_kw) if fit_kw else "") + ")")
            mat_name = py_str(mt.get("name")) or lib or "'fitted'"
            lines.append("_mat = f.mat.add_lorentz().name(" + mat_name +
                         ").eps_inf(_fit['eps_inf'])")
            lines.append("for _p in _fit['poles']:")
            lines.append("    if _p['kind'] == 'drude':")
            lines.append("        _mat.add_eps_drude_pole(_p['omega_p'], _p['gamma'])")
            lines.append("    else:")
            lines.append("        _mat.add_eps_lorentz_pole("
                         "_p['omega_0'], _p['gamma'], _p['delta_eps'])")
            out.extend(lines)
        else:
            W.append(tag(i) + "未知材料类型 " + _js_string(mt.get("type")) + "，已跳过")
    return out

def _gen_geometry(list_, W: list, env: dict) -> list[str]:
    out = []
    for i, g in enumerate(list_ or []):
        if not isinstance(g, dict):    # G126：条目非对象不裸崩
            W.append(f"geometry[{i}] 不是对象（"
                     + _js_string(type(g).__name__) + "），已跳过")
            continue

        def warn(msg, i=i):
            W.append(f"geometry[{i}] " + msg)

        mat = py_str(g.get("material"))
        first = ("f.geom.add_" + _js_string(g.get("type")) + "()") \
            + (".material(" + mat + ")" if mat else "")
        if not mat:
            warn("缺 material")
        parts = []
        prio = _num(g.get("priority"), env, W, f"geometry[{i}].priority")
        if g.get("type") == "box":
            for ax in ("x", "y", "z"):
                r = _num_arr(g.get(ax + "_range"), 2, env, W, f"geometry[{i}].{ax}_range")
                if r:
                    parts.append(f".{ax}_range({r[0]}, {r[1]})")
                else:
                    warn(f"{ax}_range 非法")
        elif g.get("type") in ("sphere", "cylinder", "ngon_z",
                               "ellipse_z", "ring_z"):
            c = _num_arr(g.get("center"), 3, env, W, f"geometry[{i}].center")
            if c and not _eq3(g.get("center"), _DEF["center"]):
                parts.append(".center(" + ", ".join(c) + ")")
            if g.get("type") == "sphere":
                rad = _num(g.get("radius"), env, W, f"geometry[{i}].radius")
                if rad:
                    parts.append(".radius(" + rad + ")")
                else:
                    warn("radius 非法")
            elif g.get("type") == "cylinder":
                rad = _num(g.get("radius"), env, W, f"geometry[{i}].radius")
                if rad:
                    parts.append(".radius(" + rad + ")")
                else:
                    warn("radius 非法")
                h = _num(g.get("height"), env, W, f"geometry[{i}].height")
                if h:
                    parts.append(".height(" + h + ")")
                else:
                    warn("height 非法")
            elif g.get("type") == "ellipse_z":
                rx = _num(g.get("rx"), env, W, f"geometry[{i}].rx")
                if rx:
                    parts.append(".rx(" + rx + ")")
                else:
                    warn("rx 非法")
                ry = _num(g.get("ry"), env, W, f"geometry[{i}].ry")
                if ry:
                    parts.append(".ry(" + ry + ")")
                else:
                    warn("ry 非法")
                h = _num(g.get("height"), env, W, f"geometry[{i}].height")
                if h:
                    parts.append(".height(" + h + ")")
                else:
                    warn("height 非法")
            elif g.get("type") == "ring_z":
                ri = _num(g.get("inner_radius"), env, W, f"geometry[{i}].inner_radius")
                if ri:
                    parts.append(".inner_radius(" + ri + ")")
                else:
                    warn("inner_radius 非法")
                ro = _num(g.get("outer_radius"), env, W, f"geometry[{i}].outer_radius")
                if ro:
                    parts.append(".outer_radius(" + ro + ")")
                else:
                    warn("outer_radius 非法")
                h = _num(g.get("height"), env, W, f"geometry[{i}].height")
                if h:
                    parts.append(".height(" + h + ")")
                else:
                    warn("height 非法")
            else:  # ngon_z
                ns = _num(g.get("n_sides"), env, W, f"geometry[{i}].n_sides")
                if ns:
                    parts.append(".n_sides(" + ns + ")")
                else:
                    warn("n_sides 非法")
                cr = _num(g.get("circumradius"), env, W, f"geometry[{i}].circumradius")
                if cr:
                    parts.append(".circumradius(" + cr + ")")
                else:
                    warn("circumradius 非法")
                h = _num(g.get("height"), env, W, f"geometry[{i}].height")
                if h:
                    parts.append(".height(" + h + ")")
                else:
                    warn("height 非法")
        elif g.get("type") in ("polygon_z", "polygon_swept"):
            vs = _vertex_list(g.get("vertices"))
            if vs:
                parts.append(".vertices([" + ", ".join(vs) + "])")
            else:
                warn("vertices 非法（须 (x, y) 对列表）")
            zr = _num_arr(g.get("z_range"), 2, env, W, f"geometry[{i}].z_range")
            if zr:
                parts.append(f".z_range({zr[0]}, {zr[1]})")
            else:
                warn("z_range 非法")
            if g.get("type") == "polygon_swept" and g.get("tilt") is not None:
                t = _num_arr(g.get("tilt"), 2, env, W, f"geometry[{i}].tilt")
                if t:
                    parts.append(f".tilt({t[0]}, {t[1]})")
                else:
                    warn("tilt 非法")
        else:
            # audit 批 α（2026-09）：未知 geometry type 从 warn+跳过升级
            # 为 ValueError——配置来源不可信（agent/外部 JSON）时，静默
            # 丢弃整条几何或渲染出任意 add_<type>() 方法名都是注入面；
            # 已知 8 type 白名单（box/sphere/cylinder/ngon_z/polygon_z/
            # ellipse_z/ring_z/polygon_swept）外即拒。
            # 【字节锁注意】本处改动会改渲染字节——必须
            # GENIE_REGEN_GOLDEN=1 重生成 golden，逐条评审 diff 后入库。
            raise ValueError(
                f"geometry[{i}] 未知形状类型 "
                + _js_string(g.get("type"))
                + "（合法: box/sphere/cylinder/ngon_z/polygon_z/"
                "ellipse_z/ring_z/polygon_swept）")
        if prio is not None and g.get("priority") != _DEF["priority"]:
            parts.append(".priority(" + prio + ")")
        out.extend(_chain(first, parts))
    return out

def _gen_source(list_, W: list, env: dict) -> list[str]:
    out = []
    if not (list_ or []):
        W.append("source 列表为空（至少需要一个源）")
    for i, s in enumerate(list_ or []):
        if not isinstance(s, dict):    # G126：条目非对象不裸崩
            W.append(f"source[{i}] 不是对象（"
                     + _js_string(type(s).__name__) + "），已跳过")
            continue

        def warn(msg, i=i):
            W.append(f"source[{i}] " + msg)

        parts = []
        freq = _num(s.get("freq"), env, W, f"source[{i}].freq")
        bw = _num(s.get("bandwidth"), env, W, f"source[{i}].bandwidth")
        if freq:
            parts.append(".freq(" + freq + ")")
        else:
            warn("freq 非法")
        if bw:
            parts.append(".bandwidth(" + bw + ")")
        else:
            warn("bandwidth 非法")
        dl = _num(s.get("delay"), env, W, f"source[{i}].delay")
        if dl is not None and s.get("delay") != _DEF["delay"]:
            parts.append(".delay(" + dl + ")")
        ph = _num(s.get("phase"), env, W, f"source[{i}].phase")
        if ph is not None and s.get("phase") != _DEF["phase"]:
            parts.append(".phase(" + ph + ")")
        if py_str(s.get("name")):
            parts.append(".name(" + py_str(s.get("name")) + ")")
        if s.get("type") == "soft_source":
            first = "f.src.add_soft_source()"
            c = _num_arr(s.get("center"), 3, env, W, f"source[{i}].center")
            if c and not _eq3(s.get("center"), _DEF["center"]):
                parts.append(".center(" + ", ".join(c) + ")")
            sz = _slot_arr(s.get("size"), 3, "None", env, W, f"source[{i}].size")
            size = s.get("size") or []
            # 点缺省 (0,0,0) = 模型默认（各槽 ≤0 单格）→ 省略（判据 =
            # _DEF["mon_size"]，G132；与监视器/cfl 同款纪律；阶段 ④
            # to_config() 换 model_dump 后默认值显式在场，此处省略保证
            # 渲染字节跨换心不变——实证 E3 唯一字节差即此）
            if sz and list(size) != list(_DEF["mon_size"]):
                parts.append(".size(" + ", ".join(sz) + ")")
            pol = _norm_pol(s.get("polarization") if s.get("polarization") is not None
                            else _DEF["pol_default"])
            if s.get("polarization") is not None and pol is None:
                # G130（R17）：畸形 polarization 此前静默回落缺省 x̂——
                # 宽容可以，哑不行。
                warn("polarization 畸形，已回落缺省 x̂ 线偏振")
            if pol:
                has_imag = any(p[1] != 0 for p in pol)
                is_default = pol == _DEF["pol_default"] and not has_imag
                if not is_default:
                    parts.append(".polarization(" + (
                        ", ".join(f"complex({py_num(p[0])}, {py_num(p[1])})" for p in pol)
                        if has_imag else
                        ", ".join(py_num(p[0]) for p in pol)) + ")")
            am = _num(s.get("amplitude"), env, W, f"source[{i}].amplitude")
            if am is not None and s.get("amplitude") != _DEF["amplitude"]:
                parts.append(".amplitude(" + am + ")")
            if s.get("kind") == _KIND_MAGNETIC:
                parts.append(f".kind('{_KIND_MAGNETIC}')")
        elif s.get("type") in ("plane_wave", "gaussian_beam"):
            first = "f.src.add_" + s["type"] + "()"
            if s.get("direction") == "-":
                parts.append(".direction('-')")
            am = _num(s.get("amplitude"), env, W, f"source[{i}].amplitude")
            if am is not None and s.get("amplitude") != _DEF["amplitude"]:
                parts.append(".amplitude(" + am + ")")
            c = _num_arr(s.get("center"), 3, env, W, f"source[{i}].center")
            if c:
                parts.append(".center(" + ", ".join(c) + ")")   # 显式设置才出
            for ang in ("theta", "phi", "psi"):
                a = _num(s.get(ang), env, W, f"source[{i}].{ang}")
                if a is not None and s.get(ang) != _DEF["angle"]:
                    parts.append(f".{ang}({a})")
            sz = _slot_arr(s.get("size"), 3, "None", env, W, f"source[{i}].size")
            if sz:
                parts.append(".size(" + ", ".join(sz) + ")")
            if py_str(s.get("propagation_axis")):
                parts.append(".propagation_axis(" + py_str(s.get("propagation_axis")) + ")")
            if py_str(s.get("from_face")):
                parts.append(".from_face(" + py_str(s.get("from_face")) + ")")
            if s.get("type") == "gaussian_beam":
                r = _num(s.get("radius"), env, W, f"source[{i}].radius")
                if r:
                    parts.append(".radius(" + r + ")")
                else:
                    warn("gaussian_beam 缺 radius")
        else:
            warn("未知源类型 " + _js_string(s.get("type")) + "，已跳过")
            continue
        out.extend(_chain(first, parts))
    return out

def _gen_monitor(list_, W: list, env: dict) -> list[str]:
    out = []
    for i, m in enumerate(list_ or []):
        if not isinstance(m, dict):    # G126：条目非对象不裸崩
            W.append(f"monitor[{i}] 不是对象（"
                     + _js_string(type(m).__name__) + "），已跳过")
            continue

        def warn(msg, i=i):
            W.append(f"monitor[{i}] " + msg)

        parts = []
        name = py_str(m.get("name"))
        if name:
            parts.append(".name(" + name + ")")
        else:
            warn("缺 name")
        c = _num_arr(m.get("center"), 3, env, W, f"monitor[{i}].center")
        if c and not _eq3(m.get("center"), _DEF["center"]):
            parts.append(".center(" + ", ".join(c) + ")")
        field = py_str(m.get("field"))
        if m.get("type") in ("time", "freq"):
            first = "f.mon.add_time()" if m["type"] == "time" else "f.mon.add_freq()"
            sz = _slot_arr(m.get("size"), 3, "None", env, W, f"monitor[{i}].size")
            size = m.get("size") or []
            if sz and list(size) != list(_DEF["mon_size"]):
                parts.append(".size(" + ", ".join(sz) + ")")
            if field:
                parts.append(".field(" + field + ")")
            else:
                warn("field 非法")
            if m["type"] == "freq":
                f = m.get("freq") or {}
                fc = _num(f.get("center"), env, W, f"monitor[{i}].freq.center")
                fbw = _num(f.get("bw"), env, W, f"monitor[{i}].freq.bw")
                fn = _num(f.get("n"), env, W, f"monitor[{i}].freq.n")
                if fc is not None and fbw is not None and fn is not None:
                    parts.append(f".freq({fc}, {fbw}, {fn})")
                else:
                    warn("freq(center, bw, n) 非法")
                a = m.get("apodize")
                if isinstance(a, dict):
                    win = _num_arr(a.get("time_window"), 2, env, W, f"monitor[{i}].apodize.time_window")
                    w = _num(a.get("width"), env, W, f"monitor[{i}].apodize.width")
                    # G131（R17 裁修）：side 是 Literal[start/stop/both]——
                    # 非法 str 原样渲会到运行期才炸、非 str 静默 None；
                    # 白名单与几何 type 白名单同族，非法即 W + 按 None 渲。
                    side_v = a.get("side")
                    if side_v is None or side_v in ("start", "stop", "both"):
                        side_s = py_str(side_v) if side_v else "None"
                    else:
                        W.append(f"monitor[{i}].apodize.side 非法（"
                                 + _js_string(str(side_v))
                                 + "，合法 start/stop/both），已按 None 渲染")
                        side_s = "None"
                    parts.append(".apodize(" + side_s + ", "
                                 + (("(" + ", ".join(win) + ")") if win else "None") + ", "
                                 + (w if w is not None else "None") + ")")
        elif m.get("type") == "index":
            first = "f.mon.add_index()"
            sz = _slot_arr(m.get("size"), 3, "None", env, W, f"monitor[{i}].size")
            size = m.get("size") or []
            if sz and list(size) != list(_DEF["index_size"]):
                parts.append(".size(" + ", ".join(sz) + ")")
            if field:
                parts.append(".field(" + field + ")")
            else:
                warn("field 非法")
        else:
            warn("未知监视器类型 " + _js_string(m.get("type")) + "，已跳过")
            continue
        d = m.get("down_sample")
        if isinstance(d, dict):
            win = _num_arr(d.get("time_window"), 2, env, W, f"monitor[{i}].down_sample.time_window")
            st = _num(d.get("stride"), env, W, f"monitor[{i}].down_sample.stride")
            parts.append(".down_sample(" + (("(" + ", ".join(win) + ")") if win else "None")
                         + ", " + (st if st is not None else str(_DEF["stride"])) + ")")
        out.extend(_chain(first, parts))
    return out

def _gen_run(cfg: dict, W: list, env: dict) -> str | None:
    # G18 单旋钮：时长在 domain.total_time（_gen_domain 渲染），run 节
    # 只剩 gpu/timeout_s——f.run() 无步数参数（跑到网格时间容量）。
    r = cfg.get("run") or {}
    kw = []
    g = r.get("gpu")
    if g is not None and py_num(g) is not None:
        kw.append("gpu=" + py_num(g))
    elif g is not None:
        # G130（R17）：gpu 为字符串/bool 时此前静默跳过——补 W。
        W.append("run.gpu 非数值（" + _js_string(type(g).__name__)
                 + "），已跳过")
    to = _num(r.get("timeout_s"), env, W, "run.timeout_s")
    if to is not None and r.get("timeout_s") != _DEF["timeout_s"]:
        kw.append("timeout_s=" + to)
    return "f.run(" + (", ".join(kw)) + ")"

def _gen_results(monitors, has_run: bool) -> list[str]:
    if not has_run or not (monitors or []):
        return []
    out = []
    used = set()
    printed = False
    for m in monitors:
        if not isinstance(m, dict):    # G126：条目非对象不裸崩（_gen_results）
            continue
        if m.get("type") == "time":
            v = _var_name("t", m.get("name"), used)
            out.append(f"{v} = f.get_time({py_str(m.get('name') or '')})"
                       + ("" if printed else f"        # {v}.time / {v}.fields['Ey']"))
            if not printed:
                out.append(f"print({v}.time[-1])        # 末时刻 (s)")
                printed = True
        elif m.get("type") == "freq":
            v = _var_name("fr", m.get("name"), used)
            out.append(f"{v} = f.get_freq({py_str(m.get('name') or '')})"
                       + ("" if printed else f"       # {v}.freqs / {v}.fields"))
            if not printed:
                out.append(f"print({v}.freqs.min(), {v}.freqs.max())   # 频轴范围 (Hz)")
                printed = True
        elif m.get("type") == "index":
            v = _var_name("ix", m.get("name"), used)
            out.append(f"{v} = f.get_index({py_str(m.get('name') or '')})"
                       + ("" if printed else f"      # {v}.eps / {v}.mu"))
    return out
