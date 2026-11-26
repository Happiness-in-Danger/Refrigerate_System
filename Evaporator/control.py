# Evaporator/control.py
# Step5a: 蒸发风机执行层（转速环 + 油门下发 + 反馈镜像 + 故障事件位）
#
# 按工作区设计口径：
# - supervisor_interlocks 保持油门值语义（*_throttle_cmd）
# - RUNNING 期间始终读取 hr13 用户油门（EVAP_FAN_USER_THROTTLE）
# - 设备模块内部实现转速环，根据 ST_ESC1_RPM 反馈调节油门
# - 故障检测：使能且有目标转速但反馈 rpm 无效/过低时置 ST_EVAP_FAN_FAULT

import time
import uasyncio as asyncio
import Board.state as state
from HAL.Driver import get_driver_link
from HAL.PID_Plus import IncrementalController


# 模块级转速环 PID（与冷凝风机共享参数）
_rpm_ctrl = IncrementalController(
    Kp=state.fan_speed_loop_params["Kp"],
    Ki=state.fan_speed_loop_params["Ki"],
    Kd=state.fan_speed_loop_params["Kd"],
    dt=state.fan_speed_loop_params["period_s"],
    setpoint=0.0,
    deadband=state.fan_speed_loop_params["deadband"],
    max_delta=state.fan_speed_loop_params["throttle_max_adjust"],
    aggr_mode="off",
    output_sign=-1.0,
)

_current_throttle = 0
_last_rpm_ms = None


def _sync_rpm_params():
    """Apply persisted/Modbus fan speed-loop settings to the live controller."""
    cp = state.fan_speed_loop_params
    _rpm_ctrl.Kp = cp["Kp"]
    _rpm_ctrl.Ki = cp["Ki"]
    _rpm_ctrl.Kd = cp["Kd"]
    _rpm_ctrl.deadband = max(0.0, cp["deadband"])
    _rpm_ctrl.max_delta = abs(cp["throttle_max_adjust"])
    _rpm_ctrl.aggr_mode = "off"
    _rpm_ctrl.output_sign = -1.0


def _rpm_update_due(now_ms):
    """Run the speed PID at its configured interval, quantized by the 500ms task."""
    global _last_rpm_ms
    period_ms = max(500, int(state.fan_speed_loop_params.get("period_s", 0.5) * 1000))
    if (_last_rpm_ms is not None and
            time.ticks_diff(now_ms, _last_rpm_ms) < period_ms):
        return False
    if _last_rpm_ms is None:
        _rpm_ctrl.dt = max(0.5, period_ms / 1000.0)
    else:
        _rpm_ctrl.dt = max(1e-6, time.ticks_diff(now_ms, _last_rpm_ms) / 1000.0)
    _last_rpm_ms = now_ms
    return True


def _throttle_to_rpm_cmd(throttle):
    """将 supervisor 下发的油门值映射到目标转速（rpm）。"""
    if throttle <= 0:
        return 0
    max_rpm = state.state_data[state.ST_FAN_SPEED_MAX]
    return int(throttle / state.ESC_THROTTLE_MAX * max_rpm)


def _reset_pid():
    global _current_throttle, _last_rpm_ms
    _current_throttle = 0
    _rpm_ctrl.reset()
    _last_rpm_ms = None


async def Evaporator():
    """蒸发风机 0.5s 周期执行协程"""
    global _current_throttle, _last_rpm_ms
    while True:
        now_ms = time.ticks_ms()
        link = get_driver_link()

        il = state.supervisor_interlocks
        enabled = il["evap_fan_enable"]

        # RUNNING 期间始终读 hr13 用户设定；其它阶段服从 supervisor
        if state.sys_mode == state.MODE_RUNNING and enabled:
            throttle_cmd = state.evap_fan_user_params.get("EVAP_FAN_USER_THROTTLE", 0)
        else:
            throttle_cmd = il["evap_fan_throttle_cmd"]

        rpm_cmd = _throttle_to_rpm_cmd(throttle_cmd)

        # Step6/MINOR-1: 转速环使能开关显式消费（0x5）
        speed_loop_enabled = bool(state.protect_params.get("EVAP_FAN_SPEED_LOOP_ENABLE", 1))

        # 发送到 ESC1
        if link is not None and enabled and rpm_cmd > 0 and state.driver_link_ok:
            _sync_rpm_params()
            if not speed_loop_enabled:
                # 转速环禁用：开环油门直通
                _rpm_ctrl.reset()
                _last_rpm_ms = None
                _current_throttle = max(
                    state.ESC_THROTTLE_MIN,
                    min(state.ESC_THROTTLE_MAX, throttle_cmd)
                )
            else:
                if _current_throttle <= 0:
                    _current_throttle = max(state.ESC_THROTTLE_MIN, throttle_cmd)

                rpm_fb = state.state_data[state.ST_ESC1_RPM]
                if rpm_fb != 999:
                    if _rpm_update_due(now_ms):
                        _rpm_ctrl.setpoint = rpm_cmd
                        delta = _rpm_ctrl.update(rpm_fb)
                        _current_throttle += delta
                else:
                    _rpm_ctrl.reset()
                    _last_rpm_ms = None

                _current_throttle = max(
                    state.ESC_THROTTLE_MIN,
                    min(state.ESC_THROTTLE_MAX, _current_throttle)
                )

            link.enable(1)
            link.set_throttle(1, int(_current_throttle))
        else:
            _reset_pid()
            if link is not None:
                link.disable(1)

        # Step6: ir21 实际下发油门镜像
        state.evap_fan_throttle_out = (int(_current_throttle)
                                       if (enabled and rpm_cmd > 0 and state.driver_link_ok)
                                       else 0)

        # 镜像反馈转速：ST_ESC1_RPM → ST_EVAP_FAN_RPM
        rpm = state.state_data[state.ST_ESC1_RPM]
        state.state_data[state.ST_EVAP_FAN_RPM] = rpm

        # 故障事件位（供 Alarm/rules.py EVAP_FAN_FAULT 规则消费）
        # Step6: 反馈转速偏离目标 > FAN_RPM_TOL 也判故障（审计建议）
        fault = False
        if enabled and rpm_cmd > 0:
            tol = state.protect_params["FAN_RPM_TOL"]
            fault = (rpm == 999 or rpm <= state.protect_params["RPM_VALID_MIN"] or
                     abs(rpm - rpm_cmd) > tol)
        state.state_data[state.ST_EVAP_FAN_FAULT] = 1 if fault else 0

        await asyncio.sleep_ms(500)
