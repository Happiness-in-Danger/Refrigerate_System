# Sensor/validate.py
"""
传感器数据检查层（校验门）。

规则来源：ARCHITECTURE_PLAN.md §7
- 显示通道仍读 sensor_data（含 999 透传，给人看报警）。
- 控制通道读 sensor_checked + sensor_quality + sensor_age_ms()（给机器做决策）。
- validate 只判定标记，不执行任何保护动作。
"""

import time
import Board.state as state

# ─── 配置阈值 ───────────────────────────────────────────────
SENTRY_VALUE = 999

# 物理量程表（压力全程绝压 bara；电流为整机总电流 0–50A）
RANGES = {
    state.IDX_SUCTION_T:     (-40.0, 150.0),  # °C
    state.IDX_DISCHARGE_T:   (-40.0, 150.0),  # °C
    state.IDX_INLET_T:       (-40.0, 150.0),  # °C
    state.IDX_OUTLET_T:      (-40.0, 150.0),  # °C
    state.IDX_LIQUID_T:      (-40.0, 150.0),  # °C
    state.IDX_SUCTION_PSI:   (0.5, 26.0),     # bara，绝压
    state.IDX_DISCHARGE_PSI: (0.5, 26.0),     # bara，绝压
    state.IDX_SUPERHEAT:     (-10.0, 40.0),   # K
    state.IDX_E_VALUE:       (180.0, 480.0),  # kJ/kg
    state.IDX_CURRENT:       (0.0, 50.0),     # A
}

# 变化率限幅（B6 重标：实测墙上周期 ≈760-850ms，按 800ms 重新计算 = 原 200ms 值 ×4）
RATE_LIMITS = {
    state.IDX_SUCTION_T:     20.0,  # K / period
    state.IDX_DISCHARGE_T:   20.0,  # K / period
    state.IDX_INLET_T:       20.0,  # K / period
    state.IDX_OUTLET_T:      20.0,  # K / period
    state.IDX_LIQUID_T:      20.0,  # K / period
    state.IDX_SUCTION_PSI:   12.0,  # bar / period
    state.IDX_DISCHARGE_PSI: 12.0,  # bar / period
    state.IDX_SUPERHEAT:     20.0,  # K / period
    state.IDX_E_VALUE:      200.0,  # kJ/kg / period
    state.IDX_CURRENT:       80.0,  # A / period
}

# 单拍 SUSPECT；连续 N 拍 SUSPECT 升级 BAD
SUSPECT_TO_BAD_COUNT = 2

# 新鲜度：距最近一次 Q_OK 超过该毫秒数即判 BAD
# B6 重标（2026-09-15）：1000ms → 3400ms（实测 800ms ×4 + 200ms 安全余量）。
# 单一来源：Valve_CRTL.py 的过热度 PID 质量门禁引用本常量（D-8）。
FRESHNESS_MS_GLOBAL = 3400
FRESHNESS_MS = FRESHNESS_MS_GLOBAL   # 兼容旧引用

# 低过热度警戒（派生通道独立规则）
LOW_SH_THRESHOLD = -5.0  # K

# ─── 模块级历史状态 ─────────────────────────────────────────
_prev_value = [None] * 10   # 上一拍有效值（OK/SUSPECT 才会写入）
_suspect_cnt = [0] * 10     # 连续 SUSPECT 计数


def _in_range(idx, value):
    lo, hi = RANGES[idx]
    return lo <= value <= hi


def _rate_ok(idx, value):
    prev = _prev_value[idx]
    if prev is None:
        return True
    # 哨兵值在哨兵检查中处理，不拿它算变化率
    if value == SENTRY_VALUE or prev == SENTRY_VALUE:
        return True
    return abs(value - prev) <= RATE_LIMITS[idx]


def validate():
    """
    对 sensor_data[] 执行完整检查管线，结果写入：
      sensor_checked[], sensor_quality[], sensor_checked_ms[]
    本函数只标记，不执行动作。
    """
    global _prev_value, _suspect_cnt
    now = time.ticks_ms()

    # ── 1-3 级：哨兵 / 量程 / 变化率 ─────────────────────────
    for idx in range(10):
        raw = state.sensor_data[idx]
        q = state.Q_OK

        # 1. 哨兵值
        if raw == SENTRY_VALUE:
            q = state.Q_BAD
        else:
            # 2. 物理量程
            if not _in_range(idx, raw):
                q = state.Q_BAD
            # 3. 变化率限幅
            elif not _rate_ok(idx, raw):
                q = state.Q_SUSPECT

        # SUSPECT → BAD 升级
        if q == state.Q_SUSPECT:
            _suspect_cnt[idx] += 1
            if _suspect_cnt[idx] >= SUSPECT_TO_BAD_COUNT:
                q = state.Q_BAD
                # 注意：升级到 BAD 后计数器不清零，保持锁存状态
        elif q == state.Q_OK:
            # 只有 OK 才清零计数器
            _suspect_cnt[idx] = 0
        # q == Q_BAD 时：保持计数器不变，维持锁存

        # 更新 checked / quality / timestamp
        if q == state.Q_OK:
            state.sensor_checked[idx] = float(raw)
            state.sensor_quality[idx] = state.Q_OK
            state.sensor_checked_ms[idx] = now
            _prev_value[idx] = float(raw)
        elif q == state.Q_SUSPECT:
            # SUSPECT 期间：不写入 checked（避免污染），不刷新时间戳，不更新 _prev_value
            # 保留最后已知 good 值
            state.sensor_quality[idx] = state.Q_SUSPECT
        else:  # Q_BAD
            state.sensor_quality[idx] = state.Q_BAD
            # sensor_checked 保留最后已知 good 值，不刷新时间戳
            # 不拿坏值（含哨兵）污染历史，避免下一拍变化率误判

    # ── 4. 派生通道质量继承（取父通道最差 = max）─────────────
    # SH、E 的父通道为吸气温度 + 吸气压力
    sh_q = max(state.sensor_quality[state.IDX_SUCTION_T],
               state.sensor_quality[state.IDX_SUCTION_PSI])
    e_q = max(state.sensor_quality[state.IDX_SUCTION_T],
              state.sensor_quality[state.IDX_SUCTION_PSI])

    # 只有比当前更差（Q 数值更大）才升级；OK 时刷新时间戳
    if sh_q > state.sensor_quality[state.IDX_SUPERHEAT]:
        state.sensor_quality[state.IDX_SUPERHEAT] = sh_q
        if sh_q == state.Q_OK:
            state.sensor_checked_ms[state.IDX_SUPERHEAT] = now
    if e_q > state.sensor_quality[state.IDX_E_VALUE]:
        state.sensor_quality[state.IDX_E_VALUE] = e_q
        if e_q == state.Q_OK:
            state.sensor_checked_ms[state.IDX_E_VALUE] = now

    # 父通道正常但 SH 过低 → SUSPECT
    if (state.sensor_quality[state.IDX_SUPERHEAT] == state.Q_OK and
            state.sensor_data[state.IDX_SUPERHEAT] < LOW_SH_THRESHOLD):
        state.sensor_quality[state.IDX_SUPERHEAT] = state.Q_SUSPECT

    # ── 5. 新鲜度：任何通道超龄直接 BAD ──────────────────────
    # 只检查已经至少更新过一次的通道（timestamp != 0）
    for idx in range(10):
        if state.sensor_checked_ms[idx] != 0:  # 已更新过
            if state.sensor_age_ms(idx) > FRESHNESS_MS:
                state.sensor_quality[idx] = state.Q_BAD
