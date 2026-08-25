# #多线程法
# import _thread
# from HAL.PID_Plus import IncrementalController
# # from Extensions.read_r134a import read_record
# # from Sensor.read_sensors import reads
# # from EXV.EXV import step
# import time
# class ValveController:
#     def __init__(self,cycle=0.5,init_position=0.0):

#         self.valve_position = init_position
#         self.pending_delta = 0.0
#         self.valve_statue = False
#         self.cycle_pid = cycle
#         self.running = True
#         self.lock = _thread.allocate_lock()
#         _thread.start_new_thread(self.worker, ())

#     def run_valva(self,valve):
#         if valve>=0:
#             valve*=5
#             print(valve)
#             # step(round(valve),40,1,0,"PB7",4,2)
#         else:
#             valve*=5
#             print(valve)
#             # step(round(abs(valve)),40,0,0,"PB7",4,2)

#     def get_delta(self,pid_value):
#         with self.lock:
#             self.pending_delta += pid_value

#     def worker(self):
#         """后台线程，不停检查是否需要执行 step()"""
#         while self.running:
#             with self.lock:
#                 pid_value = self.pending_delta
#                 if abs(pid_value) < 0.01:
#                     pid_value = 0
#                 else:
#                     self.pending_delta = 0

#             if pid_value != 0:
#                 # 执行真实动作（阻塞）
#                 self.run_valva(pid_value)
#                 # 每次动作完成后更新开度
#                 with self.lock:
#                     self.valve_position += pid_value

#             else:
#                 time.sleep(0.01)

#     def stop(self):
#         self.running = False

# EXV/exv_ctrl.py
# PID计算 + 阀门队列调度
# main.py 只调用 set_controller() 和 run()

import uasyncio as asyncio
import EXV.EXV as exv
import Board.state as state

# ─── 模块级状态 ──────────────────────────────────────────────
_exv_ctrl     = None   # PID控制器，由main.py注入
pending_delta = 0.0    # 累积待执行增量
valve_busy    = False  # 阀门忙碌标志
_synced_params = None  # 最近一次应用到控制器的屏幕参数


# ════════════════════════════════════════════════════════════
# 初始化接口
# ════════════════════════════════════════════════════════════

def set_controller(ctrl):
    """main.py 初始化PID后调用，注入控制器实例"""
    global _exv_ctrl, _synced_params
    _exv_ctrl = ctrl
    _synced_params = _controller_params()


def _controller_params():
    cp = state.control_params
    params = tuple(cp[key] for key in (
        'Kp', 'Ki', 'Kd', 'setpoint', 'deadband', 'max_delta',
        'error_threshold', 'aggr_mode', 'tau', 'T_goal'))
    if params[-1] <= 0:
        params = params[:-1] + (1e-6,)
    return params


def _sync_controller_params():
    """Apply screen changes without resetting unchanged online-tuned gains."""
    global _synced_params
    params = _controller_params()
    if params == _synced_params:
        return

    (_exv_ctrl.Kp, _exv_ctrl.Ki, _exv_ctrl.Kd,
     _exv_ctrl.setpoint, _exv_ctrl.deadband, _exv_ctrl.max_delta,
     _exv_ctrl.error_threshold, _exv_ctrl.aggr_mode,
     _exv_ctrl.tau, _exv_ctrl.T_goal) = params
    _synced_params = params


# ════════════════════════════════════════════════════════════
# PID采样协程（1s周期）
# ════════════════════════════════════════════════════════════

async def pid_loop():
    global pending_delta
    while True:
        if not state.state_data[state.ST_SYSTEM_ENABLE]:
            # Do not retain commands generated before the system was stopped.
            pending_delta = 0.0
            await asyncio.sleep_ms(1000)
            continue

        _sync_controller_params()
        sh = state.sensor_data[7]
        if sh == 999:
            print("[PID] 传感器故障，跳过本次计算")
        else:
            delta = _exv_ctrl.update(sh)
            pending_delta += delta
            print("[PID] SH=%.2f delta=%+.2f pending=%+.2f busy=%s" % (
                  sh, delta, pending_delta, valve_busy))
        await asyncio.sleep_ms(1000)


# ════════════════════════════════════════════════════════════
# 阀门执行协程（100ms轮询）
# ════════════════════════════════════════════════════════════

async def valve_loop():
    global pending_delta, valve_busy
    while True:
        if not state.state_data[state.ST_SYSTEM_ENABLE]:
            pending_delta = 0.0
            await asyncio.sleep_ms(100)
            continue

        # 定期归零检查（每20次动作触发）
        # await exv.check_and_rehome()

        # 有积压 且 阀门空闲 → 取出执行
        if abs(pending_delta) >= 1.0 and not valve_busy:
            # 取出当前所有积压，清零
            # 执行期间新delta继续累积到清零后的pending
            delta         = pending_delta
            pending_delta = 0.0
            valve_busy    = True

            print("[Valve] 开始执行 delta=%+.2f | pos=%d步 pct=%.1f%%" % (
                  delta, exv.get_position_steps(), exv.get_position_pct()))

            ok = await exv.move_delta_pct(delta)

            # 执行完成，统计绝对位置
            valve_busy = False
            pos = exv.get_position_steps()
            pct = exv.get_position_pct()

            # 同步到共享状态
            state.state_data[state.ST_VALVE_POS] = pct

            print("[Valve] 完成 | pos=%d/500步 pct=%.1f%% | 队列剩余=%+.2f" % (
                  pos, pct, pending_delta))

            if not ok:
                print("[Valve] 驱动器故障")
                state.set_fault('EXV_FAULT')
        else:
            await asyncio.sleep_ms(100)


# ════════════════════════════════════════════════════════════
# 对外入口
# ════════════════════════════════════════════════════════════

async def run():
    """
    上电归零 + 启动 pid_loop 和 valve_loop
    main.py 在 asyncio.gather 里调用此函数
    """
    if not state.state_data[state.ST_SYSTEM_ENABLE]:
        await asyncio.sleep_ms(100)
        return

    ok = await exv.homing()
    if not ok:
        print("[EXV] 归零失败")
        state.set_fault('EXV_FAULT')
        return

    await asyncio.gather(
        pid_loop(),
        valve_loop(),
    )
