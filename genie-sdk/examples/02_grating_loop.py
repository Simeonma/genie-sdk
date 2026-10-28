# 示例 02 —— 自由代码：for 循环周期光栅（v2 脚本"编译"语义展示）
# 主程序里的 for / numpy 会在执行时被展开：GUI 里看到的是展开后的
# 10 个盒子（每个都是真实节点），这正是"自由代码 → 模型"的执行语义。
import numpy as np
import genie

f = genie.connect("https://genie.example.com", api_key="YOUR_API_KEY", username="YOUR_USERNAME")

PITCH = 180e-9          # 光栅周期
WIDTH = 90e-9           # 占空一半
N_TEETH = 10            # 齿数（改成 30 再预览——模型跟着变）

f.domain.x_range = (-2e-6, 2e-6)
f.domain.y_range = (-1e-6, 1e-6)
f.domain.z_range = (-1e-6, 1e-6)
f.domain.total_time = 20e-14
f.domain.boundary_condition('x_min', 'pml')
f.domain.boundary_condition('x_max', 'pml')

f.mesh.dx = 15e-9
f.mesh.dy = 30e-9
f.mesh.dz = 30e-9

f.mat.add_const().name('sio2').eps(2.1)

xs = np.linspace(-1.6e-6, 1.6e-6, N_TEETH)   # numpy 编译期求值
for x in xs:
    f.geom.add_box().material('sio2') \
        .x_range(float(x), float(x) + WIDTH) \
        .y_range(-5e-7, 5e-7) \
        .z_range(-1e-7, 1e-7)

f.src.add_plane_wave().freq(3.3e14).bandwidth(6e13) \
    .from_face('x_min').name('pw')

f.mon.add_freq().name('tr') \
    .center(1.7e-6, 0, 0).size(2e-7, 1.2e-6, 1.2e-6) \
    .field('all').freq(3.3e14, 1.2e14, 300)

f.run(timeout_s=900)

print("光栅齿数:", N_TEETH, "周期:", PITCH)
