# Compressor/control.py
# Step5a/5b: 压缩机执行层（容量环 + 转速环 + 油门下发 + 反馈镜像 + 故障检测）
#
# 控制架构（DESIGN.md v3.3）：
#   容量环（10s）：出液温度 -> comp_throttle_base（油门绝对值）
#   转速环（0.5s）：rpm_cmd <- throttle_base -> throttle_adjust -> ESC0
# - supervisor 在 RUNNING 期间把 comp_throttle_base 复制到 interlocks
# - 手动模式 0x2=1 时，容量环输出无效，由 4x220 直接覆盖

import time
import uasyncio as asyncio
import Board.state as state
from HAL.Driver import get_driver_link
from HAL.PID_Plus import IncrementalController


# 模块级 PID 实例
_cap_ctrl = IncrementalController(
    Kp=state.compressor_control_params["Kp"],
    Ki=state.compressor_control_params["Ki"],
    Kd=state.compressor_control_params["Kd"],
    dt=10.0,
    setpoint=state.compressor_control_params["setpoint"],
    deadband=state.compressor_control_params["deadband"],
    max_delta=state.compressor_control_params["max_delta"],
    error_threshold=state.compressor_control_params.get("error_threshold", 5.0),
    aggr_mode=state.compressor_control_params.get("aggr_mode", "togoal"),
    tau=state.compressor_control_params.get("tau", 3.0),
    T_goal=state.compressor_control_params.get("T_goal", 10.0),
)

_rpm_ctrl = IncrementalController(
    Kp=state.comp_speed_loop_params["Kp"],
    Ki=state.comp_speed_loop_params["Ki"],
    Kd=state.comp_speed_loop_params["Kd"],
    dt=state.comp_speed_loop_params["period_s"],
    setpoint=0.0,
    deadband=state.comp_speed_loop_params["deadband"],
    max_delta=state.comp_speed_loop_params["throttle_max_adjust"],
)

# 当前输出
_current_throttle = 0
_last_cap_ms = 0


def _sync_cap_params():
    """同步 Modbus 修改后的容量环参数"""
    cp = state.compressor_control_params
    _cap_ctrl.Kp = cp["Kp"]
    _cap_ctrl.Ki = cp["Ki"]
    _cap_ctrl.Kd = cp["Kd"]
    _cap_ctrl.setpoint = cp["setpoint"]
    _cap_ctrl.deadband = cp["deadband"]
    _cap_ctrl.max_delta = cp["max_delta"]
    _cap_ctrl.error_threshold = cp.get("error_threshold", 5.0)


def _throttle_to_rpm_cmd(throttle):
    """将 supervisor 下发的油门值映射到目标转速（rpm）。"""
    if throttle <= 0:
        return 0
    max_rpm = state.state_data[state.ST_MOTOR_SPEED_MAX]
    return int(throttle / state.ESC_THROTTLE_MAX * max_rpm)


def _reset_pid():
    """停机时清零转速环状态，防止历史积分影响下次启动"""
    global _current_throttle
    _current_throttle = 0
    _rpm_ctrl.integral = 0.0
    _rpm_ctrl.prev_meas = None
    _rpm_ctrl.dx_filtered = 0.0
    _rpm_ctrl.prev_dx_filtered = 0.0
    _rpm_ctrl.ddx_filtered = 0.0


def _reset_cap_pid():
    """容量环积分清零"""
    _cap_ctrl.integral = 0.0
    _cap_ctrl.prev_meas = None
    _cap_ctrl.dx_filtered = 0.0
    _cap_ctrl.prev_dx_filtered = 0.0
    _cap_ctrl.ddx_filtered = 0.0


def _update_capacity_loop(now_ms):
    """容量环（默认 10s，周期由 hr24/period_s 可配）：出液温度 -> 基础油门"""
    global _last_cap_ms
    period_ms = int(state.compressor_control_params.get("period_s", 10.0) * 1000)
    if period_ms <= 0:
        period_ms = 10000
    if _last_cap_ms and time.ticks_diff(now_ms, _last_cap_ms) < period_ms:
        return
    _last_cap_ms = now_ms
    _sync_cap_params()

    # 手动模式或 R4 THERMO_OFF 时不更新容量环输出
    if (state.manual_params.get("MANUAL_MODE_ENABLE", 0) or
            state.running_substate == state.RUNNING_THERMO_OFF or
            state.sys_mode != state.MODE_RUNNING):
        return

    idx = state.IDX_OUTLET_T
    q = state.sensor_quality[idx]
    age = state.sensor_age_ms(idx)
    if q != state.Q_OK or age > 2000:
        # 质量非 OK 或超龄：冻结积分，保持当前输出
        return

    temp = state.sensor_checked[idx]
    # 偏差 = temp - setpoint（温度偏高 -> 增加油门；偏低 -> 减小油门）
    error = temp - _cap_ctrl.setpoint
    delta = _cap_ctrl.update(temp, setpoint=_cap_ctrl.setpoint)

    cp = state.compressor_control_params
    base = state.comp_throttle_base + delta
    base = max(cp["throttle_min"], min(cp["throttle_max"], base))

    # L1 限载：禁升速
    if state.limit_actions & state.ACT_INHIBIT_UP:
        base = min(base, state.comp_throttle_base)

    # L1 限载：降容
    if state.limit_actions & state.ACT_UNLOAD:
        oil_thr = state.protect_params["COMP_OIL_RETURN_THROTTLE"]
        rate = state.protect_params["UNLOAD_RATE_THROTTLE_S"]
        max_allowed = state.comp_throttle_base - rate * 10.0
        base = max(oil_thr, min(base, max_allowed))

    state.comp_throttle_base = int(base)
    print("[Compressor] 容量环: T=%.1f°C e=%+.1f delta=%+.1f base=%d" %
          (temp, error, delta, state.comp_throttle_base))


async def Compressor():
    """压缩机 0.5s 周期执行协程"""
    global _current_throttle
    while True:
        now_ms = time.ticks_ms()
        link = get_driver_link()

        il = state.supervisor_interlocks
        enabled = il["comp_enable"]
        throttle_cmd = il["comp_throttle_cmd"]

        # Step5b: 容量环（10s 周期）
        _update_capacity_loop(now_ms)

        # 手动模式覆盖：仅 RUNNING 期间且 0x2=1 时，使用 4x220 手动油门
        if (enabled and state.sys_mode == state.MODE_RUNNING and
                state.manual_params.get("MANUAL_MODE_ENABLE", 0)):
            throttle_cmd = state.manual_params.get("MANUAL_COMP_THROTTLE", 0)
            # 手动模式下容量环输出保持，但不写入 interlocks

        # 目标转速
        rpm_cmd = _throttle_to_rpm_cmd(throttle_cmd)

        # Step6/MINOR-1: 转速环使能开关显式消费（0x3）
        # 禁用时：跳过转速 PID，开环直接下发容量环油门（仅限幅）。
        speed_loop_enabled = bool(state.protect_params.get("COMP_SPEED_LOOP_ENABLE", 1))

        # 发送到 ESC0
        if link is not None and enabled and rpm_cmd > 0 and state.driver_link_ok:
            if not speed_loop_enabled:
                # 转速环禁用：开环油门直通
                _current_throttle = max(
                    state.ESC_THROTTLE_MIN,
                    min(state.ESC_THROTTLE_MAX, throttle_cmd)
                )
            else:
                # 启动瞬间用 supervisor 油门作为开环前馈
                if _current_throttle <= 0:
                    _current_throttle = max(state.ESC_THROTTLE_MIN, throttle_cmd)

                # 转速环：读反馈，计算油门增量
                rpm_fb = state.state_data[state.ST_ESC0_RPM]
                if rpm_fb != 999:
                    _rpm_ctrl.setpoint = rpm_cmd
                    delta = _rpm_ctrl.update(rpm_fb)
                    _current_throttle += delta

                # 限幅
                _current_throttle = max(
                    state.ESC_THROTTLE_MIN,
                    min(state.ESC_THROTTLE_MAX, _current_throttle)
                )

            link.enable(0)
            link.set_throttle(0, int(_current_throttle))
        else:
            _reset_pid()
            if link is not None:
                link.disable(0)

        # Step6: ir19 实际下发油门镜像
        state.comp_throttle_out = (int(_current_throttle)
                                   if (enabled and rpm_cmd > 0 and state.driver_link_ok)
                                   else 0)

        # 镜像反馈转速：ST_ESC0_RPM -> ST_MOTOR_SPEED
        rpm = state.state_data[state.ST_ESC0_RPM]
        state.state_data[state.ST_MOTOR_SPEED] = rpm

        # 故障事件位（供 Alarm/rules.py COMPRESSOR_FAULT 规则消费）
        fault = False
        if enabled and rpm_cmd > 0:
            fault = (rpm == 999 or rpm <= state.protect_params["RPM_VALID_MIN"])
        state.state_data[state.ST_COMPRESSOR_FAULT] = 1 if fault else 0

        await asyncio.sleep_ms(500)
