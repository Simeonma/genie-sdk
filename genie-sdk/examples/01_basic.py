# 示例 01 —— 单盒波导谐振腔（方言最小面）
import genie

f = genie.connect()   # 凭据经 genie login 注入本机（一次即可）

# ── Domain 域 ──
f.domain.x_range = (-1e-6, 1e-6)
f.domain.y_range = (-1e-6, 1e-6)
f.domain.z_range = (-2e-6, 2e-6)
f.domain.total_time = 6e-14
f.domain.boundary_condition('z_min', 'pml')
f.domain.boundary_condition('z_max', 'pml')

# ── Mesh 网格 ──
f.mesh.dx = 2e-8
f.mesh.dy = 2e-8
f.mesh.dz = 2e-8

# ── Materials 材料 ──
f.mat.add_const() \
    .name('sio2') \
    .eps(2.1)

# ── Geometry 结构 ──
f.geom.add_box().material('sio2') \
    .x_range(-5e-7, 5e-7) \
    .y_range(-5e-7, 5e-7) \
    .z_range(-1e-7, 1e-7) \
    .priority(1)

# ── Source 源 ──
f.src.add_soft_source() \
    .freq(3e14) \
    .bandwidth(6e13) \
    .name('s0') \
    .center(0, 0, -3e-7)

# ── Monitor 监视器 ──
f.mon.add_time() \
    .name('pt') \
    .center(0, 0, 2e-7) \
    .field('Ex')
f.mon.add_freq() \
    .name('sp') \
    .center(0, 0, 2e-7) \
    .field('all') \
    .freq(3e14, 1.2e14, 200)

# ── Run 运行 ──
f.run(timeout_s=600)

# ── 结果（纯数据 numpy）──
t_pt = f.get_time('pt')
print(t_pt.time[-1])
fr_sp = f.get_freq('sp')
