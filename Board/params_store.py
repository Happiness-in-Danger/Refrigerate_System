# Board/params_store.py
"""
Step6 参数持久化（DESIGN.md §9.4）。

设计要点：
- `Board/state.py` 中的参数字典是运行期内存唯一来源（params 单源）。
- 单一配置文件 `config.json`：含 version / timestamp / checksum / params。
- 掉电保持：Modbus 写参数后 30s 防抖落盘；进入 SAFE_STOP 时立即落盘。
- 原子写：先写 `config.json.tmp`，成功后 rename 覆盖。
- 损坏回退：checksum 不符 / 文件损坏 / 版本过高 → 使用 state.py 默认值。
- 版本迁移：`_migrate()` 预留。
- MicroPython 兼容：json.dumps/load 不使用 encoding=/indent=/ensure_ascii=。
"""

import time
import json
import os
import Board.state as state

CONFIG_PATH = "config.json"
CONFIG_TMP_PATH = "config.json.tmp"
CONFIG_VERSION_CURRENT = 1
SAVE_DEBOUNCE_MS = 30000

# HMI 用户范围段（见 docs/PC_HMI_UART_DESIGN.md 6.4）
# ranges: {symbol: {"min": value, "max": value}, ...}
_ranges = {}
_ranges_dirty = False

# 持久化的参数组：组名 -> state 中的字典属性名
# 手动指令（manual_params / evap_fan_user_params）与复位脉冲不持久化。
_PERSIST_GROUPS = (
    ("sh_pid", "control_params"),
    ("cap_pid", "compressor_control_params"),
    ("cond_pid", "cond_control_params"),
    ("comp_speed_loop", "comp_speed_loop_params"),
    ("fan_speed_loop", "fan_speed_loop_params"),
    ("protect", "protect_params"),
    ("esc_telemetry", "esc_telemetry_params"),
)

_DEFAULT_DELAY_SLOTS = [0, 5, 10, 30, 45, 60, 300, 900]

_dirty = False
_dirty_ms = 0
_write_count = 0


def _checksum(params):
    """对 params 的 JSON 序列化做 16-bit 累加和（确定性、MicroPython 可用）"""
    c = 0
    for ch in json.dumps(params).encode():
        c = (c + ch) & 0xFFFF
    return c


def collect_params():
    """收集当前内存参数（供落盘 / 测试）"""
    out = {}
    for group, attr in _PERSIST_GROUPS:
        out[group] = dict(getattr(state, attr))
    return out


def collect_ranges():
    """收集当前用户范围（供落盘 / 测试）"""
    return dict(_ranges)


def apply_params(params):
    """把持久化参数应用到内存（缺省键保留默认值，不报错）"""
    for group, attr in _PERSIST_GROUPS:
        saved = params.get(group)
        if not isinstance(saved, dict):
            continue
        target = getattr(state, attr)
        for k, v in saved.items():
            if k in target:
                target[k] = v
    slots = state.protect_params.get("delay_slots")
    if not isinstance(slots, list) or len(slots) != 8:
        state.protect_params["delay_slots"] = list(_DEFAULT_DELAY_SLOTS)
    # ESC 遥测配置从持久化回写后，同步回驱动使用的列表
    if hasattr(state, "sync_esc_telemetry_cfg"):
        state.sync_esc_telemetry_cfg()


def apply_ranges(ranges, expected_version=None, expected_hash=None):
    """把持久化范围应用到内存；版本/哈希不匹配则丢弃。"""
    global _ranges
    if not isinstance(ranges, dict):
        return False
    if expected_version is not None and expected_hash is not None:
        try:
            import Board.hmi_points as hmi_points
            if expected_version != hmi_points.HMI_VERSION or expected_hash != hmi_points.LIST_HASH:
                print("[Params] HMI 范围段版本/哈希不匹配，丢弃")
                return False
        except Exception as e:
            print("[Params] 无法校验 HMI 范围段:", e)
            return False
    _ranges = {k: dict(v) for k, v in ranges.items() if isinstance(v, dict)}
    return True


def get_range(symbol):
    """返回 (min, max)；未设定返回 (None, None)"""
    r = _ranges.get(symbol)
    if r is None:
        return None, None
    return r.get("min"), r.get("max")


def set_range(symbol, min_val, max_val):
    """设定用户范围（不检查当前值）。"""
    global _ranges
    _ranges[symbol] = {"min": min_val, "max": max_val}
    mark_ranges_dirty()


def clear_range(symbol):
    """清除用户范围。"""
    global _ranges
    if symbol in _ranges:
        del _ranges[symbol]
        mark_ranges_dirty()


def get_all_ranges():
    """返回范围字典副本。"""
    return {k: dict(v) for k, v in _ranges.items()}


def _migrate(cfg):
    """版本迁移：返回 params 字典。当前仅 v1。"""
    ver = cfg.get("version", 0)
    if ver < 1:
        # v0 -> v1：无结构性变更（占位，供未来迁移）
        pass
    return cfg.get("params", {})


def load_config(path=None):
    """
    开机加载配置。
    返回 True=成功应用；False=使用 state.py 默认值。
    """
    if path is None:
        path = CONFIG_PATH
    try:
        with open(path, "r") as f:
            cfg = json.load(f)
    except Exception as e:
        print("[Params] 配置加载失败，使用默认值:", e)
        return False
    try:
        if not isinstance(cfg, dict) or "params" not in cfg:
            print("[Params] 配置格式不兼容（旧版/损坏），使用默认值")
            return False
        if cfg.get("version", 0) > CONFIG_VERSION_CURRENT:
            print("[Params] 配置版本高于固件，忽略")
            return False
        params = cfg["params"]
        if _checksum(params) != cfg.get("checksum"):
            print("[Params] checksum 校验失败，使用默认值")
            return False
        apply_params(_migrate(cfg))

        # HMI 用户范围段（独立段，损坏不匹配不影响参数）
        hmi_section = cfg.get("hmi")
        if isinstance(hmi_section, dict):
            ranges = hmi_section.get("ranges", {})
            if _checksum(ranges) == hmi_section.get("ranges_checksum"):
                apply_ranges(
                    ranges,
                    hmi_section.get("hmi_version"),
                    hmi_section.get("hmi_hash"),
                )
            else:
                print("[Params] HMI 范围段 checksum 失败，丢弃")

        print("[Params] 配置已加载")
        return True
    except Exception as e:
        print("[Params] 配置应用失败，使用默认值:", e)
        return False


def save_now(path=None):
    """立即落盘（原子写 tmp + rename）"""
    global _dirty, _write_count, _ranges_dirty
    if path is None:
        path = CONFIG_PATH
    tmp_path = path + ".tmp"
    params = collect_params()
    ranges = collect_ranges()
    hmi_section = {
        "hmi_version": None,
        "hmi_hash": None,
        "ranges_checksum": _checksum(ranges),
        "ranges": ranges,
    }
    try:
        import Board.hmi_points as hmi_points
        hmi_section["hmi_version"] = hmi_points.HMI_VERSION
        hmi_section["hmi_hash"] = hmi_points.LIST_HASH
    except Exception:
        pass
    cfg = {
        "version": CONFIG_VERSION_CURRENT,
        "timestamp": time.ticks_ms(),
        "checksum": _checksum(params),
        "params": params,
        "hmi": hmi_section,
    }
    try:
        with open(tmp_path, "w") as f:
            f.write(json.dumps(cfg))
        try:
            os.remove(path)
        except Exception:
            pass
        os.rename(tmp_path, path)
        _dirty = False
        _ranges_dirty = False
        _write_count += 1
        print("[Params] 已保存到", path)
        return True
    except Exception as e:
        print("[Params] 保存失败:", e)
        return False


def save_if_dirty():
    """仅在有待保存变更时落盘（SAFE_STOP 入口调用，避免无谓写 flash）"""
    if _dirty or _ranges_dirty:
        return save_now()
    return False


def mark_dirty():
    """标记参数已变更，启动 30s 防抖计时"""
    global _dirty, _dirty_ms
    _dirty = True
    _dirty_ms = time.ticks_ms()


def save_timer_pending():
    return _dirty


def write_count():
    return _write_count


def tick(now_ms=None):
    """防抖计时器：到点则落盘。由 loop() 或调用方周期调用。"""
    if not _dirty and not _ranges_dirty:
        return
    if now_ms is None:
        now_ms = time.ticks_ms()
    if time.ticks_diff(now_ms, _dirty_ms) >= SAVE_DEBOUNCE_MS:
        save_now()


def mark_ranges_dirty():
    """标记用户范围已变更，启动防抖计时。"""
    global _ranges_dirty, _dirty_ms
    _ranges_dirty = True
    _dirty_ms = time.ticks_ms()


async def loop(period_ms=1000):
    import uasyncio as asyncio
    while True:
        tick()
        await asyncio.sleep_ms(period_ms)
