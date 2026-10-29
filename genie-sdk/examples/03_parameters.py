import genie
from genie.lib.grating import grating

f = genie.connect()   # 凭据经 genie login 注入本机（一次即可）

# ── 参数表 + 表达式槽（原文上行，服务端求值；GUI 3D 显示用解析副本）──
# G17：配置一律走 API 函数链式构造——load_config 是内部承重面，不是用户入口。
f.parameters({"d": "50[nm]", "pitch": "d*4"})
f.domain.x_range = (-2e-6, 2e-6)
f.domain.y_range = (-1e-6, 1e-6)
f.domain.z_range = (-1e-6, 1e-6)
f.domain.total_time = 6e-14
f.domain.boundary_condition('z_min', 'pml')
f.domain.boundary_condition('z_max', 'pml')
f.mesh.dx = 'd/4'          # 表达式槽引用参数表

# ── Python 变量 = 编译期展开（脚本执行即定）；参数表 = 服务端求值 ──
# 两个世界各得其所：结构规律用 Python 算，物理量用参数表扫。
N = 8
f.mat.add_const().name('sio2').eps(2.1)
grating(f, material='sio2', x_range=(0, N * 200e-9),
        y_range=(-5e-7, 5e-7), z_range=(-1e-7, 1e-7),
        n=N, pitch=200e-9)
f.src.add_plane_wave().freq(3e14).bandwidth(6e13).from_face('z_min')
f.mon.add_freq().name('sp').center(0, 0, 5e-7) \
    .freq(3e14, 1.2e14, 200)
f.run()
