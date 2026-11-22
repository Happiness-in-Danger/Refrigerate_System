# Alarm/supervisor.py
"""
系统 supervisor 状态机 (Step3)

职责（ARCHITECTURE_PLAN.md §8.1 / §8.3 / §9）：
- sys_mode 状态机
- 启停时序（§8.3.1 启动 / §8.3.2 正常停机 / §8.3.3 故障停机）
- 保护规则延时表遍历（§9），**error_reg 唯一写者**
- 手动复位协议（§8.3.6）
- 软看门狗（传感器心跳，§7.4 末两行）

全局铁律【已拍板 §8.3】：所有「报警条件成立 → 执行保护动作」必须经过
该规则自己的 *_DELAY_S；禁止条件边沿瞬时停机。

时间基准：全程 time.ticks_ms() / time.ticks_diff()，回绕安全。
非阻塞：tick() 为纯同步单步函数，不含 sleep；supervisor_loop() 负责 await。
"""

import time
import Board.state as state
import Board.params_store as params_store
from Alarm import rules

# ═══════════════════════════════════════════════════════════
# 模块级状态
# ═══════════════════════════════════════════════════════════

# 规则计时器：{rule_name: start_ticks_ms}；不在字典内 = 未计时
_rule_timers = {}

# 已到点并已执行动作的规则名集合（防止重复执行动作）
_rule_fired = set()

# 启动时序
_startup_step = 0            # 0=未开始，1..6 = S1..S6
_startup_step_start = None   # 当前步进入时刻
_s5_hold_start = None        # S5 定速保持窗口起点（模式 0）
_s5_window_start = None      # S5 转速稳定性窗口起点（模式 1）
_s5_rpm_min = None           # 模式 1 窗口内最小转速
_s5_rpm_max = None           # 模式 1 窗口内最大转速
_s5_last_sample = None       # 模式 1 最近采样时刻

# 停机时序
_stopping_start = None       # 停机序列起点
_stopping_is_fault = False   # True=故障停机(8.3.3)，False=正常停机(8.3.2)

# Driver 链路断锁存软标志（error_reg 16 位已排满，见 state.py 注释）
driver_link_latched = False

# Step5b: L3 重试锁定计数 {fault_name: [latched_ms, ...]}
_retry_counts = {}

# Step5b: 温控循环计时
_thermo_on_start = None      # 压缩机进入 RUNNING 的起始时刻
_thermo_off_start = None     # 进入 R4 THERMO_OFF 的起始时刻

# Step5b: 启动次数记录（用于 MAX_STARTS_PER_HOUR）
_start_times = []

# 启动失败 → 映射到已有 error_reg 位，保证「禁止自动重启」（§8.3.3 E4）
# 新时序（2026-09-03）：S1 冷凝风机 → S2 蒸发风机 → S3 EXV → S4 压缩机 → S5 晋级
# S5 为晋级判据（定速保持或开机延时），失败仍归因为压缩机未能稳定运行，
# 故映射到 COMPRESSOR_FAULT，与 S4 一致。
_STARTUP_FAIL_BITS = {
    1: "COND_FAN_FAULT",    # S1 冷凝风机启动失败
    2: "EVAP_FAN_FAULT",    # S2 蒸发风机启动失败
    3: "EXV_FAULT",         # S3 EXV 到位失败
    4: "COMPRESSOR_FAULT",  # S4 压缩机软启失败
    5: "COMPRESSOR_FAULT",  # S5 晋级失败（定速保持/开机延时未满足）
}

# 严重锁存故障集合（存在任一即禁止 STARTUP）
_SEVERE_FAULTS = (
    "HIGH_PRESSURE_FAULT",
    "DISCHARGE_PSI_FAULT",
    "LOW_PRESSURE_FAULT",
    "SUCTION_PSI_FAULT",
    "OVERCURRENT_FAULT",
    "SENSOR_STALE_FAULT",
    "EXV_FAULT",
    "COMPRESSOR_FAULT",
    "EVAP_FAN_FAULT",
    "COND_FAN_FAULT",
    "FLOW_FAULT",
)

# 手动复位可清除的位
_RESETTABLE_FAULTS = (
    "HIGH_PRESSURE_FAULT",
    "DISCHARGE_PSI_FAULT",
    "LOW_PRESSURE_FAULT",
    "SUCTION_PSI_FAULT",
    "OVERCURRENT_FAULT",
    "CURRENT_SENSOR_FAULT",
    "SENSOR_STALE_FAULT",
    "LOW_SUPERHEAT_FAULT",
    "EXV_FAULT",
    "COMPRESSOR_FAULT",
    "EVAP_FAN_FAULT",
    "COND_FAN_FAULT",
    "FLOW_FAULT",
)


# ═══════════════════════════════════════════════════════════
# 内部辅助
# ═══════════════════════════════════════════════════════════
def _has_severe_latched_faults():
    """是否存在未复位的严重锁存故障（阻止 SAFE_STOP → STARTUP）"""
    for name in _SEVERE_FAULTS:
        if state.is_fault(name):
            return True
    # 电流传感器 BAD：仅 CURRENT_BAD_ACTION==1 时算严重
    if (state.protect_params["CURRENT_BAD_ACTION"] == 1 and
            state.is_fault("CURRENT_SENSOR_FAULT")):
        return True
    if driver_link_latched:
        return True
    return False


def _clear_all_timers():
    """清零全部规则计时器与已执行标记"""
    _rule_timers.clear()
    _rule_fired.clear()


def _all_outputs_off():
    """全部执行机构指令归零（联锁输出，设备层消费）"""
    il = state.supervisor_interlocks
    il["comp_enable"] = False
    il["comp_throttle_cmd"] = 0
    il["evap_fan_enable"] = False
    il["evap_fan_throttle_cmd"] = 0
    il["cond_fan_enable"] = False
    il["cond_fan_throttle_cmd"] = 0
    il["exv_enable"] = False
    il["exv_target_pct"] = 0.0


def _enter_safe_stop(reason=""):
    """进入 SAFE_STOP：清零时序状态 + 全部输出禁用（不动 error_reg 锁存位）"""
    global _startup_step, _startup_step_start, _s5_hold_start
    global _s5_window_start, _s5_rpm_min, _s5_rpm_max, _s5_last_sample
    global _stopping_start, _stopping_is_fault
    global _thermo_on_start
    state.sys_mode = state.MODE_SAFE_STOP
    _startup_step = 0
    _startup_step_start = None
    _s5_hold_start = None
    _s5_window_start = None
    _s5_rpm_min = None
    _s5_rpm_max = None
    _s5_last_sample = None
    _stopping_start = None
    _stopping_is_fault = False
    _thermo_on_start = None
    state.startup_stage = 0
    state.running_substate = state.RUNNING_THERMO_OFF
    state.limit_actions = 0
    state.comp_throttle_base = 0
    state.cond_fan_throttle_base = 0
    _clear_all_timers()
    _all_outputs_off()
    # Step6: SAFE_STOP 时立即保存参数（仅当有待保存变更）
    params_store.save_if_dirty()
    print("[Supervisor] -> SAFE_STOP %s" % reason)


# ═══════════════════════════════════════════════════════════
# Step5b: 分级阈值单调性校验
# ═══════════════════════════════════════════════════════════
def _validate_threshold_monotonicity():
    """
    启动前校验同一物理量的分级阈值满足 warn < unload < trip。
    违反则置 CONFIG_IO_FAULT，阻止 STARTUP。
    """
    pp = state.protect_params
    checks = [
        ("HP",  pp.get("HP_WARN", 0), pp.get("HP_UNLOAD", 0), pp.get("HP_MAX", 0)),
        ("LP",  pp.get("LP_MIN", 0),  pp.get("LP_UNLOAD", 0), pp.get("LP_WARN", 0)),
        ("I",   pp.get("I_WARN", 0),  pp.get("I_LIMIT", 0),   pp.get("I_MAX", 0)),
        ("TD",  pp.get("TD_WARN", 0), pp.get("TD_UNLOAD", 0), pp.get("TD_TRIP", 0)),
    ]
    for name, trip_or_warn, unload, warn_or_trip in checks:
        # 对于 HP/I/TD：warn < unload < trip
        # 对于 LP：trip < unload < warn（数值上是 LP_MIN < LP_UNLOAD < LP_WARN）
        low, mid, high = trip_or_warn, unload, warn_or_trip
        if name == "LP":
            low, mid, high = trip_or_warn, unload, warn_or_trip  # 已是有序
        if not (low < mid < high):
            print("[Supervisor] 阈值单调性校验失败: %s (%s < %s < %s 违反)" %
                  (name, low, mid, high))
            state.set_fault("CONFIG_IO_FAULT")
            return False
    return True


# ═══════════════════════════════════════════════════════════
# Step5b: L3 重试锁定
# ═══════════════════════════════════════════════════════════
def _record_latched_fault(name, now_ms):
    """某锁存故障被置位时记录一次，用于 L3 重试锁定统计"""
    global _retry_counts
    window_ms = state.protect_params["RETRY_WINDOW_S"] * 1000
    if name not in _retry_counts:
        _retry_counts[name] = []
    # 清理窗口外的旧记录（窗口大小由 RETRY_WINDOW_S 参数控制，默认 3600s=1小时）
    _retry_counts[name] = [t for t in _retry_counts[name]
                           if time.ticks_diff(now_ms, t) <= window_ms]
    _retry_counts[name].append(now_ms)
    if len(_retry_counts[name]) >= state.protect_params["RETRY_MAX"]:
        state.retry_lockout = True
        print("[Supervisor] %s 1 小时内锁存 %d 次 -> retry_lockout=True" %
              (name, len(_retry_counts[name])))


def _clear_retry_lockout(now_ms):
    """报警条件全部消失后自动清 retry_lockout"""
    if not state.retry_lockout:
        return
    # 只关注有 error_reg 位的 L2/L3 锁存规则；用 bit 直接检查，避免规则名
    #（如 DISCHARGE_TEMP_TRIP）与 ERROR_BITS 键名不一致导致 KeyError。
    has_active = False
    for rule, cond_met, should_clear in rules.evaluate_all(state.sys_mode, now_ms):
        name, bit, delay_key, action, latch, armed_modes, cond_fn, clear_fn, desc = rule
        if bit is None:
            continue
        if not (state.error_reg & (1 << bit)):
            has_active = True
            break
    if not has_active:
        state.retry_lockout = False
        _retry_counts.clear()
        print("[Supervisor] 锁存故障全部消失，retry_lockout 自动清除")


def _begin_fault_stop():
    """§8.3.3 故障停机序列入口（延时已到点后调用）"""
    global _stopping_start, _stopping_is_fault
    _stopping_start = time.ticks_ms()
    _stopping_is_fault = True
    state.sys_mode = state.MODE_STOPPING
    state.limit_actions = 0
    state.running_substate = state.RUNNING_THERMO_OFF
    state.comp_throttle_base = 0
    state.cond_fan_throttle_base = 0

    il = state.supervisor_interlocks
    # E1: 压缩机 throttle=0 且 disable
    il["comp_enable"] = False
    il["comp_throttle_cmd"] = 0

    # E3: EXV 归零/关小（所有故障停机统一先关阀）
    il["exv_target_pct"] = 0.0

    if driver_link_latched:
        # 链路断：全 ESC disable（Pico 100ms 超时兜底）
        il["evap_fan_enable"] = False
        il["evap_fan_throttle_cmd"] = 0
        il["cond_fan_enable"] = False
        il["cond_fan_throttle_cmd"] = 0
    elif state.is_fault("HIGH_PRESSURE_FAULT"):
        # 高压：冷凝风机全开排热
        il["cond_fan_enable"] = True
        il["cond_fan_throttle_cmd"] = 2047

    print("[Supervisor] 故障停机序列启动 (8.3.3)")


def _begin_normal_stop():
    """§8.3.2 正常停机序列入口：EXV 关闭 → 延时停压缩机 → 延时停风机"""
    global _stopping_start, _stopping_is_fault
    _stopping_start = time.ticks_ms()
    _stopping_is_fault = False
    state.sys_mode = state.MODE_STOPPING
    state.limit_actions = 0
    state.running_substate = state.RUNNING_THERMO_OFF
    state.comp_throttle_base = 0
    state.cond_fan_throttle_base = 0

    il = state.supervisor_interlocks
    # N1: EXV 立即开始关闭
    il["exv_target_pct"] = 0.0
    # 压缩机、风机在 _stopping_tick 中按延时槽停止

    print("[Supervisor] 正常停机序列启动 (8.3.2)")


def _fail_startup(step, reason):
    """启动步失败：锁存对应 error_reg 位（禁止自动重启）后进 SAFE_STOP"""
    bit_name = _STARTUP_FAIL_BITS.get(step)
    if bit_name:
        state.set_fault(bit_name)
        print("[Supervisor] S%d 失败(%s)，锁存 %s" % (step, reason, bit_name))
    _enter_safe_stop("S%d 失败" % step)


# ═══════════════════════════════════════════════════════════
# 启动时序 §8.3.1（S0 → S6）
# ═══════════════════════════════════════════════════════════
def _startup_tick(now_ms):
    """启动时序单步；返回 True 表示已进入 RUNNING"""
    global _startup_step, _startup_step_start, _s5_hold_start
    global _s5_window_start, _s5_rpm_min, _s5_rpm_max, _s5_last_sample

    if _startup_step == 0:
        _startup_step = 1
        _startup_step_start = now_ms
        state.startup_stage = 1
        print("[Supervisor] S0: 锁存启动请求，进入 STARTUP")
        return False

    elapsed = time.ticks_diff(now_ms, _startup_step_start)
    il = state.supervisor_interlocks
    pp = state.protect_params
    state.startup_stage = _startup_step

    # ── S1: 冷凝风机（先风后机，2026-09-03 时序修订）──
    if _startup_step == 1:
        throttle_cmd = pp["COND_FAN_START_THROTTLE"]
        il["cond_fan_enable"] = True
        il["cond_fan_throttle_cmd"] = throttle_cmd

        if state.state_data[state.ST_COND_FAN_FAULT]:
            _fail_startup(1, "COND_FAN_FAULT")
            return False
        
        # 读取 ESC2 遥测反馈（冷凝风机）
        rpm_feedback = state.state_data[state.ST_ESC2_RPM]
        if rpm_feedback != 999 and rpm_feedback > pp["RPM_VALID_MIN"]:
            print("[Supervisor] S1 完成: 冷凝风机 %d r/min" % rpm_feedback)
            _startup_step = 2
            _startup_step_start = now_ms
        elif elapsed > pp["S1_TIMEOUT_MS"]:
            _fail_startup(1, "超时/无反馈")
        return False

    # ── S2: 蒸发风机（S1 完成后立即启动）──
    if _startup_step == 2:
        throttle_cmd = pp["EVAP_FAN_START_THROTTLE"]
        il["evap_fan_enable"] = True
        il["evap_fan_throttle_cmd"] = throttle_cmd

        if state.state_data[state.ST_EVAP_FAN_FAULT]:
            _fail_startup(2, "EVAP_FAN_FAULT")
            return False
        
        # 读取 ESC1 遥测反馈（蒸发风机）
        rpm_feedback = state.state_data[state.ST_ESC1_RPM]
        if rpm_feedback != 999 and rpm_feedback > pp["RPM_VALID_MIN"]:
            print("[Supervisor] S2 完成: 蒸发风机 %d r/min" % rpm_feedback)
            _startup_step = 3
            _startup_step_start = now_ms
        elif elapsed > pp["S2_TIMEOUT_MS"]:
            _fail_startup(2, "超时/无反馈")
        return False

    # ── S3: EXV 归零后开到固定开度 ──
    if _startup_step == 3:
        target = pp["EXV_START_POS"]
        il["exv_enable"] = True
        il["exv_target_pct"] = target

        if state.state_data[state.ST_VALVE_FAULT]:
            _fail_startup(3, "EXV_FAULT")
            return False
        if abs(state.state_data[state.ST_VALVE_POS] - target) <= 2.0:
            print("[Supervisor] S3 完成: EXV 到位 %.1f%%" %
                  state.state_data[state.ST_VALVE_POS])
            _startup_step = 4
            _startup_step_start = now_ms
        elif elapsed > pp["S3_TIMEOUT_MS"]:
            _fail_startup(3, "超时")
        return False

    # ── S4: 压缩机软启（EXV 开始动作后延时，2026-09-03 新增）──
    if _startup_step == 4:
        # EXV 到压缩机延时（引用 delay_slots）
        exv_to_comp_delay_ms = int(pp["delay_slots"][pp["EXV_TO_COMP_DELAY_SLOT"]] * 1000)
        if elapsed < exv_to_comp_delay_ms:
            return False
        
        throttle_cmd = pp["COMP_START_THROTTLE"]
        il["comp_enable"] = True
        il["comp_throttle_cmd"] = throttle_cmd

        # 读取压缩机反馈转速（Compressor 模块已镜像 ST_ESC0_RPM → ST_MOTOR_SPEED）
        rpm_feedback = state.state_data[state.ST_MOTOR_SPEED]
        if rpm_feedback != 999 and rpm_feedback > pp["RPM_VALID_MIN"]:
            print("[Supervisor] S4 完成: 压缩机软启到 %d r/min (延时 %.1fs)" % 
                  (rpm_feedback, pp["delay_slots"][pp["EXV_TO_COMP_DELAY_SLOT"]]))
            _startup_step = 5
            _startup_step_start = now_ms
            _s5_hold_start = None
        elif elapsed > pp["S4_TIMEOUT_MS"]:
            _fail_startup(4, "软启超时/无反馈")
        return False

    # ── S5: 系统层晋级（二选一，§8.3.1 STARTUP_ADVANCE_MODE）──
    if _startup_step == 5:
        if pp["STARTUP_ADVANCE_MODE"] == 0:
            # 模式0 定速保持：反馈有效且稳定连续保持 COMP_HOLD_S
            rpm_feedback = state.state_data[state.ST_MOTOR_SPEED]
            in_win = (rpm_feedback != 999 and rpm_feedback > pp["RPM_VALID_MIN"])
            if in_win:
                if _s5_hold_start is None:
                    _s5_hold_start = now_ms
                elif time.ticks_diff(now_ms, _s5_hold_start) >= int(pp["COMP_HOLD_S"] * 1000):
                    print("[Supervisor] S5 完成: 定速保持 %.1fs" % pp["COMP_HOLD_S"])
                    _startup_step = 6
                    return False
            else:
                _s5_hold_start = None   # 脱离窗口 → 重新计时
        else:
            # 模式1 转速稳定性检查（Step6 完整实现）：
            # 启动后延时 COMP_START_DELAY_S，再在 COMP_RPM_STABILITY_WINDOW_S 窗口内
            # 采样转速，若窗口内变化 <= COMP_RPM_STABILITY_THRESHOLD 则晋级。
            delay_ms = int(pp["COMP_START_DELAY_S"] * 1000)
            if elapsed >= delay_ms:
                win_ms = int(pp["COMP_RPM_STABILITY_WINDOW_S"] * 1000)
                sample_ms = int(pp.get("COMP_RPM_STABILITY_SAMPLE_S", 1.0) * 1000)
                thr = pp["COMP_RPM_STABILITY_THRESHOLD"]
                rpm = state.state_data[state.ST_MOTOR_SPEED]

                if _s5_window_start is None:
                    _s5_window_start = now_ms
                    _s5_last_sample = now_ms
                    _s5_rpm_min = rpm
                    _s5_rpm_max = rpm
                else:
                    if time.ticks_diff(now_ms, _s5_last_sample) >= sample_ms:
                        _s5_last_sample = now_ms
                        if rpm != 999:
                            if _s5_rpm_min is None or rpm < _s5_rpm_min:
                                _s5_rpm_min = rpm
                            if _s5_rpm_max is None or rpm > _s5_rpm_max:
                                _s5_rpm_max = rpm
                    if time.ticks_diff(now_ms, _s5_window_start) >= win_ms:
                        stable = (rpm != 999 and _s5_rpm_min is not None and
                                  (_s5_rpm_max - _s5_rpm_min) <= thr)
                        if stable:
                            print("[Supervisor] S5 完成: 转速稳定 (窗口 %d rpm <= %d)" %
                                  (_s5_rpm_max - _s5_rpm_min, thr))
                            _startup_step = 6
                            return False
                        # 不稳定 → 重新开窗
                        _s5_window_start = now_ms
                        _s5_last_sample = now_ms
                        _s5_rpm_min = rpm
                        _s5_rpm_max = rpm

        if elapsed > pp["S5_TIMEOUT_MS"]:
            _fail_startup(5, "晋级超时")
        return False

    # ── S6: 进入 RUNNING ──
    if _startup_step == 6:
        state.sys_mode = state.MODE_RUNNING
        _startup_step = 0
        _startup_step_start = None
        _s5_hold_start = None
        _s5_window_start = None
        state.startup_stage = 6
        print("[Supervisor] S6: 进入 RUNNING，保护按 8.3.5 武装")
        return True

    return False


# ═══════════════════════════════════════════════════════════
# 停机时序推进（§8.3.2 N2/N3 与 §8.3.3 E2）
# ═══════════════════════════════════════════════════════════
def _stopping_tick(now_ms):
    global _stopping_start
    if _stopping_start is None:
        _enter_safe_stop("停机状态异常")
        return
    elapsed = time.ticks_diff(now_ms, _stopping_start)
    il = state.supervisor_interlocks
    pp = state.protect_params

    if _stopping_is_fault:
        # E2: 故障停机 —— 风机保持 FAULT_FAN_HOLD_MS 后收至停止
        if elapsed >= pp["FAULT_FAN_HOLD_MS"]:
            _enter_safe_stop("故障停机序列完成 (E4)")
        return

    comp_stop_delay_ms = int(pp["delay_slots"][pp["COMP_STOP_DELAY_SLOT"]] * 1000)
    fan_stop_delay_ms = int(pp["delay_slots"][pp["FAN_STOP_DELAY_SLOT"]] * 1000)
    # N2: 延时到点后停压缩机
    if elapsed >= comp_stop_delay_ms:
        il["comp_enable"] = False
        il["comp_throttle_cmd"] = 0
    # N3: 再延时到点后停风机，序列结束
    if elapsed >= comp_stop_delay_ms + fan_stop_delay_ms:
        il["evap_fan_enable"] = False
        il["evap_fan_throttle_cmd"] = 0
        il["cond_fan_enable"] = False
        il["cond_fan_throttle_cmd"] = 0
        _enter_safe_stop("正常停机序列完成 (N5)")


# ═══════════════════════════════════════════════════════════
# 手动复位 §8.3.6
# ═══════════════════════════════════════════════════════════
def request_fault_reset():
    """
    手动复位（唯一清故障入口，来自触摸屏 / Modbus hr48）。

    受理条件：
      - sys_mode ∈ {SAFE_STOP, DEGRADED}
      （RUNNING/STARTUP/STOPPING 期间拒绝；SAFE_STOP 后普通传感器
        恢复也不会自动绕过本流程）

    动作：清除可复位锁存位 + 清零全部计时器；
          下一 tick 由规则重新判定：条件消失→保持清除，条件仍在→重走完整延时再锁存。
    返回 True=受理，False=拒绝。
    """
    global driver_link_latched

    if state.sys_mode not in (state.MODE_SAFE_STOP, state.MODE_DEGRADED):
        print("[Supervisor] 复位拒绝: sys_mode=%d 非 SAFE_STOP/DEGRADED" % state.sys_mode)
        return False

    for name in _RESETTABLE_FAULTS:
        state.clear_fault(name)
    driver_link_latched = False
    _clear_all_timers()
    print("[Supervisor] 手动复位受理，下一周期重新判定")
    return True


# ═══════════════════════════════════════════════════════════
# 规则延时表处理
# ═══════════════════════════════════════════════════════════
def _delay_ms(delay_key):
    """支持两种延时配置：旧 *_DELAY_S（秒）和 Step5a *_DELAY_SLOT（延时槽编号）"""
    pp = state.protect_params
    if delay_key.endswith("_SLOT"):
        return int(pp["delay_slots"][pp[delay_key]] * 1000)
    return int(pp[delay_key] * 1000)


def _delay_desc(delay_key):
    """用于日志打印的延时描述"""
    pp = state.protect_params
    if delay_key.endswith("_SLOT"):
        return "槽%d=%ds" % (pp[delay_key], pp["delay_slots"][pp[delay_key]])
    return "%.1fs" % pp[delay_key]


def _process_rules(mode, now_ms):
    """遍历规则表，维护计时器 / 锁存 error_reg / 触发动作 / 生成 L1 动作码"""
    global driver_link_latched

    fault_stop_requested = False
    normal_stop_requested = False
    limit_actions = 0

    for rule, cond_met, should_clear in rules.evaluate_all(mode, now_ms):
        name, bit, delay_key, action, latch, armed_modes, cond_fn, clear_fn, desc = rule

        # CURRENT_SENSOR_FAULT 的 latch/动作由 CURRENT_BAD_ACTION 决定
        if name == "CURRENT_SENSOR_FAULT":
            if state.protect_params["CURRENT_BAD_ACTION"] == 1:
                latch, action = True, rules.ACT_SAFE_STOP
            else:
                latch, action = False, rules.ACT_NONE

        already_latched = (driver_link_latched if (bit is None and name == "DRIVER_LINK_LOSS")
                           else not (state.error_reg & (1 << bit)) if bit is not None
                           else False)

        # 已锁存且需手动复位 → 停止计时，等复位
        if latch and already_latched:
            _rule_timers.pop(name, None)
            continue

        # 条件消失 / 规则未武装 → 计时器清零；非锁存规则同时恢复 error_reg 位
        if should_clear:
            _rule_timers.pop(name, None)
            _rule_fired.discard(name)
            if not latch and bit is not None and already_latched:
                # 通过 bit 找到对应的 fault name 来清除
                for fname, fbit in state.ERROR_BITS.items():
                    if fbit == bit:
                        state.clear_fault(fname)
                        print("[Supervisor] %s 条件消失，自动清除" % name)
                        break
            continue

        if not cond_met:
            continue

        # 条件成立：启动或继续计时
        if name not in _rule_timers:
            _rule_timers[name] = now_ms
            print("[Supervisor] %s 计时启动 (延时 %s)" %
                  (name, _delay_desc(delay_key)))
            continue

        elapsed_ms = time.ticks_diff(now_ms, _rule_timers[name])
        if elapsed_ms < _delay_ms(delay_key):
            continue    # 未到点：不锁存、不动作

        # ── 延时到点 ──
        if name in _rule_fired:
            # 已执行过动作：对于 L1 限载，只要条件持续就保持动作码
            if action >= rules.ACT_INHIBIT_UP and bit is None and not latch:
                limit_actions |= action
            continue
        _rule_fired.add(name)

        if bit is not None:
            # 通过 bit 找到对应的 fault name
            fault_name = None
            for fname, fbit in state.ERROR_BITS.items():
                if fbit == bit:
                    fault_name = fname
                    break
            if fault_name is not None:
                state.set_fault(fault_name)
                print("[Supervisor] %s 延时到点 (%.1fs)，锁存 error_reg bit%d" %
                      (name, elapsed_ms / 1000.0, bit))
                if latch:
                    _record_latched_fault(fault_name, now_ms)
            else:
                print("[Supervisor] %s 延时到点但找不到对应 error_reg 位" % name)
        elif name == "DRIVER_LINK_LOSS":
            driver_link_latched = True
            print("[Supervisor] %s 延时到点，锁存软标志" % name)

        if action & rules.ACT_SAFE_STOP:
            fault_stop_requested = True
        if action & rules.ACT_NORMAL_STOP:
            normal_stop_requested = True
            print("[Supervisor] %s -> 正常停机（不锁存）" % name)
        if action & rules.ACT_EXV_REDUCE:
            state.supervisor_interlocks["exv_target_pct"] = 0.0
            print("[Supervisor] %s -> 关小 EXV（不停机）" % name)
        # L1 限载动作码：叠加到 limit_actions，由控制环消费
        l1_mask = (rules.ACT_INHIBIT_UP | rules.ACT_UNLOAD |
                   rules.ACT_COND_FAN_MAX)
        if (action & l1_mask) and bit is None and not latch:
            limit_actions |= (action & l1_mask)
            print("[Supervisor] %s -> L1 限载动作 0x%02X" % (name, action & l1_mask))

    return fault_stop_requested, limit_actions, normal_stop_requested


# ═══════════════════════════════════════════════════════════
# 主 tick（同步、非阻塞）
# ═══════════════════════════════════════════════════════════
def tick():
    """Supervisor 单步。由 supervisor_loop() 周期调用，内部无任何 sleep。"""
    global _startup_step, _startup_step_start, _thermo_on_start
    now_ms = time.ticks_ms()

    # 1) ST_SENSOR_FAULT 汇总（关闭 §5-C2/C8）
    state.state_data[state.ST_SENSOR_FAULT] = 0 if state.sensor_fault_reg == 0xFFFF else 1

    # 2) 手动复位请求（Modbus coils 0x7 / REPL 写 state.fault_reset_request）
    if state.fault_reset_request:
        request_fault_reset()
        state.fault_reset_request = False

    mode = state.sys_mode

    # 3) 规则延时表（任何模式都评估；武装由 rules 内部按 mode 判定）
    fault_stop, limit_actions, normal_stop = _process_rules(mode, now_ms)
    state.limit_actions = limit_actions

    # 4) 正常停机优先于温控/其它非故障迁移
    if normal_stop and mode in (state.MODE_STARTUP, state.MODE_RUNNING, state.MODE_ALARM):
        _begin_normal_stop()
        print("[Supervisor] %s -> STOPPING (Modbus 通讯断/正常停机)" %
              ("STARTUP" if mode == state.MODE_STARTUP else
               "ALARM" if mode == state.MODE_ALARM else "RUNNING"))
        return

    # 5) 故障停机优先于其它迁移
    if fault_stop and mode in (state.MODE_STARTUP, state.MODE_RUNNING, state.MODE_ALARM):
        _begin_fault_stop()
        return

    # 6) 状态机迁移
    if mode == state.MODE_BOOT:
        state.sys_mode = state.MODE_SELF_TEST
        print("[Supervisor] BOOT -> SELF_TEST")

    elif mode == state.MODE_SELF_TEST:
        # Step3 占位：外设显式 init 属 Step7，此处直接进默认待机
        _enter_safe_stop("(SELF_TEST 通过)")

    elif mode == state.MODE_SAFE_STOP:
        if not state.state_data[state.ST_SYSTEM_ENABLE]:
            return
        if state.retry_lockout:
            print("[Supervisor] 启动拒绝：retry_lockout 置位")
            return
        if not _validate_threshold_monotonicity():
            return
        if _has_severe_latched_faults():
            return      # 未复位的严重故障 → 禁止启动
        if state.protect_params["LINK_CHECK_ENABLE"] and not state.driver_link_ok:
            return
        _prune_start_times(now_ms)
        if len(_start_times) >= state.protect_params["MAX_STARTS_PER_HOUR"]:
            print("[Supervisor] 启动拒绝：每小时启动次数超限 (%d/%d)" %
                  (len(_start_times), state.protect_params["MAX_STARTS_PER_HOUR"]))
            return
        state.sys_mode = state.MODE_STARTUP
        _startup_step = 0
        _startup_step_start = None
        print("[Supervisor] SAFE_STOP -> STARTUP")

    elif mode == state.MODE_STARTUP:
        if not state.state_data[state.ST_SYSTEM_ENABLE]:
            _begin_normal_stop()
            print("[Supervisor] STARTUP -> STOPPING (ENABLE 1->0)")
        else:
            entered_running = _startup_tick(now_ms)
            if entered_running:
                global _thermo_on_start
                _thermo_on_start = now_ms
                _start_times.append(now_ms)
                state.running_substate = state.RUNNING_PULLDOWN
                # Step5b: 容量环/冷凝压力环从启动油门平滑接续
                state.comp_throttle_base = state.protect_params["COMP_START_THROTTLE"]
                state.cond_fan_throttle_base = state.protect_params["COND_FAN_START_THROTTLE"]
                print("[Supervisor] 进入 RUNNING/R1，启动次数 %d" % len(_start_times))

    elif mode == state.MODE_RUNNING:
        if not state.state_data[state.ST_SYSTEM_ENABLE]:
            _begin_normal_stop()
            print("[Supervisor] RUNNING -> STOPPING (ENABLE 1->0)")
        else:
            # Step5b: 将外环输出复制到 interlocks（手动模式由设备模块覆盖）
            if state.running_substate != state.RUNNING_THERMO_OFF:
                il = state.supervisor_interlocks
                il["comp_throttle_cmd"] = state.comp_throttle_base
                il["cond_fan_throttle_cmd"] = state.cond_fan_throttle_base
                il["comp_enable"] = True
                il["cond_fan_enable"] = True
                il["evap_fan_enable"] = True
            _update_running_substate(now_ms)
            if state.running_substate == state.RUNNING_THERMO_OFF:
                _apply_thermo_off_interlocks()
            if state.error_reg != 0xFFFFFFFF:
                state.sys_mode = state.MODE_ALARM
                print("[Supervisor] RUNNING -> ALARM (可恢复故障，继续运行)")

    elif mode == state.MODE_ALARM:
        if not state.state_data[state.ST_SYSTEM_ENABLE]:
            _begin_normal_stop()
            print("[Supervisor] ALARM -> STOPPING (ENABLE 1->0)")
        elif state.error_reg == 0xFFFFFFFF:
            state.sys_mode = state.MODE_RUNNING
            state.running_substate = state.RUNNING_PULLDOWN
            _thermo_on_start = now_ms
            if state.comp_throttle_base <= 0:
                state.comp_throttle_base = state.protect_params["COMP_START_THROTTLE"]
            if state.cond_fan_throttle_base <= 0:
                state.cond_fan_throttle_base = state.protect_params["COND_FAN_START_THROTTLE"]
            print("[Supervisor] ALARM -> RUNNING/R1 (报警恢复)")

    elif mode == state.MODE_STOPPING:
        _stopping_tick(now_ms)

    elif mode == state.MODE_DEGRADED:
        pass    # 等待手动复位

    # 7) L3 重试锁定：报警条件全部消失后自动清 retry_lockout
    _clear_retry_lockout(now_ms)


# ═══════════════════════════════════════════════════════════
# Step5b: RUNNING 子状态与温控循环
# ═══════════════════════════════════════════════════════════
def _update_running_substate(now_ms):
    """
    根据出液温度和 L1 限载更新 running_substate。
    R1 PULLDOWN -> R2 MODULATING -> R4 THERMO_OFF -> R1
    R3 LIMITED 只要任一 L1 动作活跃即进入。
    """
    global _thermo_on_start, _thermo_off_start

    outlet_t = state.sensor_checked[state.IDX_OUTLET_T]
    q = state.sensor_quality[state.IDX_OUTLET_T]
    age = state.sensor_age_ms(state.IDX_OUTLET_T)
    if q != state.Q_OK or age > 2000:
        # 传感器故障时不更新子状态，保持当前状态
        return
    setpoint = state.compressor_control_params["setpoint"]
    hyst = state.protect_params["THERMO_HYST"]
    sub = state.running_substate
    rpm_min = state.state_data[state.ST_MOTOR_SPEED_MIN]
    motor_rpm = state.state_data[state.ST_MOTOR_SPEED]

    # L1 限载优先
    if state.limit_actions & (state.ACT_INHIBIT_UP | state.ACT_UNLOAD):
        if sub != state.RUNNING_LIMITED:
            state.running_substate = state.RUNNING_LIMITED
            print("[Supervisor] RUNNING -> LIMITED (L1 限载)")
        return

    # 如果之前在 LIMITED，限载消失后回到 PULLDOWN/MODULATING
    if sub == state.RUNNING_LIMITED:
        sub = state.RUNNING_PULLDOWN

    # R4 THERMO_OFF：达到设定且压缩机转速 <= rpm_min
    if sub == state.RUNNING_THERMO_OFF:
        if outlet_t > (setpoint + hyst):
            # 回温超回差，检查 MIN_OFF_S
            if _thermo_off_start is not None:
                off_elapsed = time.ticks_diff(now_ms, _thermo_off_start) / 1000.0
                if off_elapsed >= state.protect_params["MIN_OFF_S"]:
                    state.running_substate = state.RUNNING_PULLDOWN
                    _thermo_off_start = None
                    _thermo_on_start = now_ms
                    print("[Supervisor] R4 -> R1 (MIN_OFF 满足，回温 %.1fK)" %
                          (outlet_t - setpoint))
                else:
                    print("[Supervisor] R4 回温但 MIN_OFF 未满 (%.1f/%.0fs)" %
                          (off_elapsed, state.protect_params["MIN_OFF_S"]))
        return

    # R1/R2 判断：是否达到设定
    if motor_rpm != 999 and motor_rpm <= rpm_min and outlet_t <= setpoint:
        # 达到设定，检查 MIN_ON_S
        if _thermo_on_start is not None:
            on_elapsed = time.ticks_diff(now_ms, _thermo_on_start) / 1000.0
            if on_elapsed >= state.protect_params["MIN_ON_S"]:
                state.running_substate = state.RUNNING_THERMO_OFF
                _thermo_on_start = None
                _thermo_off_start = now_ms
                print("[Supervisor] R%d -> R4 (达到设定 %.1f°C，MIN_ON 满足)" %
                      (sub, outlet_t))
            else:
                print("[Supervisor] 达到设定但 MIN_ON 未满 (%.1f/%.0fs)" %
                      (on_elapsed, state.protect_params["MIN_ON_S"]))
        return

    # 未达到设定：区分 PULLDOWN 和 MODULATING
    mod_thr = state.protect_params.get("MODULATING_THRESHOLD", 2.0)
    # 接近设定（偏差 <= MODULATING_THRESHOLD）进入 MODULATING
    if abs(outlet_t - setpoint) <= mod_thr:
        state.running_substate = state.RUNNING_MODULATING
        print("[Supervisor] R1 -> R2 (接近设定)")
    else:
        if sub != state.RUNNING_PULLDOWN:
            state.running_substate = state.RUNNING_PULLDOWN


def _apply_thermo_off_interlocks():
    """R4 THERMO_OFF：压缩机停、EXV 关至 10%、冷凝风机停、蒸发风机可选保持"""
    il = state.supervisor_interlocks
    il["comp_enable"] = False
    il["comp_throttle_cmd"] = 0
    il["cond_fan_enable"] = False
    il["cond_fan_throttle_cmd"] = 0
    # EXV 关至安全位 10%，不是完全归零，避免再次启动时开度突变
    il["exv_target_pct"] = 10.0
    if not state.protect_params.get("EVAP_FAN_THERMO_OFF_KEEP", 1):
        il["evap_fan_enable"] = False
        il["evap_fan_throttle_cmd"] = 0


def _prune_start_times(now_ms):
    """清理 1 小时外的启动记录"""
    global _start_times
    window_ms = 3600 * 1000
    _start_times = [t for t in _start_times
                    if time.ticks_diff(now_ms, t) <= window_ms]


# ═══════════════════════════════════════════════════════════
# 协程入口（main.py gather）
# ═══════════════════════════════════════════════════════════
TICK_MS = 100

async def supervisor_loop():
    """
    supervisor 主协程。tick() 同步无阻塞，sleep 交还事件循环，
    不使用 time.sleep，不破坏 uasyncio 调度。
    
    P1-3 修复：tick 异常后进入 SAFE_STOP，防止保护逻辑静默失效
    """
    import uasyncio as asyncio
    while True:
        try:
            tick()
        except Exception as e:
            print("[Supervisor] tick 异常:", e)
            # P1-3: tick 异常表示保护逻辑损坏，必须立即进入 SAFE_STOP
            try:
                _enter_safe_stop("tick 异常")
            except Exception as e2:
                print("[Supervisor] _enter_safe_stop 异常:", e2)
                # 最后防线：强制进入 SAFE_STOP 状态，禁用所有执行机构
                state.sys_mode = state.MODE_SAFE_STOP
                state.supervisor_interlocks["comp_enable"] = False
                state.supervisor_interlocks["evap_fan_enable"] = False
                state.supervisor_interlocks["cond_fan_enable"] = False
                state.supervisor_interlocks["exv_enable"] = False
        await asyncio.sleep_ms(TICK_MS)
