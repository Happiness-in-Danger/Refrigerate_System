# Condenser/control.py
# Step5a/5b: 冷凝风机执行层（冷凝压力环 + 转速环 + 油门下发 + 反馈镜像 + 故障检测）
#
# 控制架构（DESIGN.md v3.3）：
#   冷凝压力环（2s）：排气压力 -> cond_fan_throttle_base（油门绝对值）
#   转速环（0.5s）：rpm_cmd <- throttle_base -> throttle_adjust -> ESC2
# - supervisor 在 RUNNING 期间把 cond_fan_throttle_base 复制到 interlocks
# - 手动模式 0x2=1 时，冷凝压力环输出无效，由 4x221 直接覆盖
# - L1 高压限载 ACT_COND_FAN_MAX 强制全速

import time
import uasyncio as asyncio
import Board.state as state
from HAL.Driver import get_driver_link
from HAL.PID_Plus import IncrementalController


# 模块级 PID 实例
_pres_ctrl = IncrementalController(
    Kp=state.cond_control_params["Kp"],
    Ki=state.cond_control_params["Ki"],
    Kd=state.cond_control_params["Kd"],
    dt=2.0,
    setpoint=state.cond_control_params["setpoint"],
    deadband=state.cond_control_params["deadband"],
    max_delta=state.cond_control_params["max_delta"],
    error_threshold=state.cond_control_params.get("error_threshold", 5.0),
    aggr_mode=state.cond_control_params.get("aggr_mode", "togoal"),
    tau=state.cond_control_params.get("tau", 3.0),
    T_goal=state.cond_control_params.get("T_goal", 5.0),
)

_rpm_ctrl = IncrementalController(
    Kp=state.fan_speed_loop_params["Kp"],
    Ki=state.fan_speed_loop_params["Ki"],
    Kd=state.fan_speed_loop_params["Kd"],
    dt=state.fan_speed_loop_params["period_s"],
    setpoint=0.0,
    deadband=state.fan_speed_loop_params["deadband"],
    max_delta=state.fan_speed_loop_params["throttle_max_adjust"],
)

_current_throttle = 0
_last_pres_ms = 0


def _sync_pres_params():
    """同步 Modbus 修改后的冷凝压力环参数"""
    cp = state.cond_control_params
    _pres_ctrl.Kp = cp["Kp"]
    _pres_ctrl.Ki = cp["Ki"]
    _pres_ctrl.Kd = cp["Kd"]
    _pres_ctrl.setpoint = cp["setpoint"]
    _pres_ctrl.deadband = cp["deadband"]
    _pres_ctrl.max_delta = cp["max_delta"]
    _pres_ctrl.error_threshold = cp.get("error_threshold", 5.0)


def _throttle_to_rpm_cmd(throttle):
    """将 supervisor 下发的油门值映射到目标转速（rpm）。"""
    if throttle <= 0:
        return 0
    max_rpm = state.state_data[state.ST_FAN_SPEED_MAX]
    return int(throttle / state.ESC_THROTTLE_MAX * max_rpm)


def _reset_pid():
    global _current_throttle
    _current_throttle = 0
    _rpm_ctrl.integral = 0.0
    _rpm_ctrl.prev_meas = None
    _rpm_ctrl.dx_filtered = 0.0
    _rpm_ctrl.prev_dx_filtered = 0.0
    _rpm_ctrl.ddx_filtered = 0.0


def _reset_pres_pid():
    """冷凝压力环积分清零"""
    _pres_ctrl.integral = 0.0
    _pres_ctrl.prev_meas = None
    _pres_ctrl.dx_filtered = 0.0
    _pres_ctrl.prev_dx_filtered = 0.0
    _pres_ctrl.ddx_filtered = 0.0


def _update_pressure_loop(now_ms):
    """冷凝压力环（默认 2s，周期由 cond_control_params['period_s'] 可配）：排气压力 -> 基础油门"""
    global _last_pres_ms
    period_ms = int(state.cond_control_params.get("period_s", 2.0) * 1000)
    if period_ms <= 0:
        period_ms = 2000
    if _last_pres_ms and time.ticks_diff(now_ms, _last_pres_ms) < period_ms:
        return
    _last_pres_ms = now_ms
    _sync_pres_params()

    # 手动模式或 R4 THERMO_OFF 时不更新压力环输出
    if (state.manual_params.get("MANUAL_MODE_ENABLE", 0) or
            state.running_substate == state.RUNNING_THERMO_OFF or
            state.sys_mode != state.MODE_RUNNING):
        return

    idx = state.IDX_DISCHARGE_PSI
    q = state.sensor_quality[idx]
    age = state.sensor_age_ms(idx)
    if q != state.Q_OK or age > 2000:
        return

    pres = state.sensor_checked[idx]
    delta = _pres_ctrl.update(pres)

    cp = state.cond_control_params
    base = state.cond_fan_throttle_base + delta
    base = max(cp["throttle_min"], min(cp["throttle_max"], base))

    # L1 高压限载：冷凝风机强制全速
    if state.limit_actions & state.ACT_COND_FAN_MAX:
        base = cp["throttle_max"]

    state.cond_fan_throttle_base = int(base)
    print("[Condenser] 压力环: P=%.1f bara delta=%+.1f base=%d" %
          (pres, delta, state.cond_fan_throttle_base))


async def Condenser():
    """冷凝风机 0.5s 周期执行协程"""
    global _current_throttle
    while True:
        now_ms = time.ticks_ms()
        link = get_driver_link()

        il = state.supervisor_interlocks
        enabled = il["cond_fan_enable"]
        throttle_cmd = il["cond_fan_throttle_cmd"]

        # Step5b: 冷凝压力环（2s 周期）
        _update_pressure_loop(now_ms)

        # 手动模式覆盖：仅 RUNNING 期间且 0x2=1 时，使用 4x221 手动油门
        if (enabled and state.sys_mode == state.MODE_RUNNING and
                state.manual_params.get("MANUAL_MODE_ENABLE", 0)):
            throttle_cmd = state.manual_params.get("MANUAL_COND_FAN_THROTTLE", 0)

        # L1 高压限载：强制全速（覆盖手动/自动）
        if state.limit_actions & state.ACT_COND_FAN_MAX:
            throttle_cmd = state.ESC_THROTTLE_MAX

        rpm_cmd = _throttle_to_rpm_cmd(throttle_cmd)

        # Step6/MINOR-1: 转速环使能开关显式消费（0x4）
        speed_loop_enabled = bool(state.protect_params.get("COND_FAN_SPEED_LOOP_ENABLE", 1))

        # 发送到 ESC2
        if link is not None and enabled and rpm_cmd > 0 and state.driver_link_ok:
            if not speed_loop_enabled:
                # 转速环禁用：开环油门直通
                _current_throttle = max(
                    state.ESC_THROTTLE_MIN,
                    min(state.ESC_THROTTLE_MAX, throttle_cmd)
                )
            else:
                if _current_throttle <= 0:
                    _current_throttle = max(state.ESC_THROTTLE_MIN, throttle_cmd)

                rpm_fb = state.state_data[state.ST_ESC2_RPM]
                if rpm_fb != 999:
                    _rpm_ctrl.setpoint = rpm_cmd
                    delta = _rpm_ctrl.update(rpm_fb)
                    _current_throttle += delta

                _current_throttle = max(
                    state.ESC_THROTTLE_MIN,
                    min(state.ESC_THROTTLE_MAX, _current_throttle)
                )

            link.enable(2)
            link.set_throttle(2, int(_current_throttle))
        else:
            _reset_pid()
            if link is not None:
                link.disable(2)

        # Step6: ir23 实际下发油门镜像
        state.cond_fan_throttle_out = (int(_current_throttle)
                                       if (enabled and rpm_cmd > 0 and state.driver_link_ok)
                                       else 0)

        # 镜像反馈转速：ST_ESC2_RPM -> ST_COND_FAN_RPM
        rpm = state.state_data[state.ST_ESC2_RPM]
        state.state_data[state.ST_COND_FAN_RPM] = rpm

        # 故障事件位（供 Alarm/rules.py COND_FAN_FAULT 规则消费）
        # Step6: 反馈转速偏离目标 > FAN_RPM_TOL 也判故障（审计建议）
        fault = False
        if enabled and rpm_cmd > 0:
            tol = state.protect_params["FAN_RPM_TOL"]
            fault = (rpm == 999 or rpm <= state.protect_params["RPM_VALID_MIN"] or
                     abs(rpm - rpm_cmd) > tol)
        state.state_data[state.ST_COND_FAN_FAULT] = 1 if fault else 0

        await asyncio.sleep_ms(500)
