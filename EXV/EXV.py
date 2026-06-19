from pyb import Pin, Timer
from HAL.PWM import PWM
import time
import uasyncio as asyncio
# pwm0=PWM(Pin(5), freq=1000, duty_u16=32768)
# Pwm0 = Timer(4,freq=40)
# Ch1=Pwm0.channel(1,Timer.PWM,pin=Pin("PB6"),pulse_width_percent=50)
# Ch1.pulse_width_percent(50)
# PWM(4,1,40,50,"PB6")
# Dir=Pin(0, mode=Pin.OUT,value=0)
Dir=Pin("PB8", mode=Pin.OUT,value=0) #0, close;1 open
# ena=Pin(1, mode=Pin.OUT,value=0)
Ena=Pin("PB9", mode=Pin.OUT,value=0)#1,free
# led=Pin(25, mode=Pin.OUT,value=0)
led = Pin("PA6",Pin.OUT)

def step(steps, freq, dir,ena,pin,time_nom,ch):
    steps, freq, dir,ena,pin,time_nom,ch
    period = 1.0 / freq
    Ena(ena)
    time.sleep(0.5)
    Dir(dir)
    pwm=PWM(time_nom,ch,freq,50,pin)
    time.sleep(period*steps)
    pwm.deinit()
    time.sleep(0.5)
    Ena(1-ena)

led(0)
step(500,40,0,0,"PB7",4,2)
led(1)

#正式程序
_pwm=PWM(4,2,40,50,"PB7")
_pwm.deinit()

def get_pwm_status():
    return _pwm.active()  # 返回True（运行中）/False（已关闭）

async def async_run(steps, freq, dir,ena,pin,time_nom,ch):
	steps, freq, dir,ena,pin,time_nom,ch
	period = 1.0 / freq
	Ena(ena)
	Dir(dir)
	await uasyncio.sleep(0.5)
	_pwm
	await uasyncio.sleep(period*steps)
	_pwm.deinit()
	Ena(1-ena)


#备用方案
async def async_run1(steps, freq, dir,ena,pin,time_nom,ch):
	steps, freq, dir,ena,pin,time_nom,ch
	period = 1.0 / freq
	Ena(ena)
	Dir(dir)
	await uasyncio.sleep(0.5)
	pwm=PWM(time_nom,ch,freq,50,pin)
	await uasyncio.sleep(period*steps)
	pwm.deinit()
	Ena(1-ena)

# EXV/EXV.py
# EXV 控制层，封装开/关/定位/归零接口
# 上层（main.py）只调用这个文件的接口，不直接操作 TMC2209

from EXV.TMC2209 import TMC2209
import uasyncio as asyncio

# ─── 方向常量 ────────────────────────────────────────────────
DIR_OPEN  = 1
DIR_CLOSE = 0

# ─── 阀体参数（三花 DPF TS1）────────────────────────────────
FULL_STROKE   = 500              # 全行程步数
STEPS_PER_PCT = FULL_STROKE / 100  # 5步 = 1%

# ─── 驱动器实例 ──────────────────────────────────────────────
_motor = TMC2209(
    uart_id         = 2,
    step_pin        = "PB7",
    dir_pin         = "PB8",
    en_pin          = "PB9",
    addr            = 0,
    r_sense         = 0.11,
    default_current = 250,
    hold_current    = 100,
    microsteps      = 2,
)

# ─── 位置追踪（步数）────────────────────────────────────────
# 0 = 全关，FULL_STROKE = 全开
# 上电后必须先调用 homing() 才有效
_position   = 0
_move_count = 0
# REHOME_INTERVAL = 20    # 每20次动作归零一次


# ════════════════════════════════════════════════════════════
# 位置查询
# ════════════════════════════════════════════════════════════

def get_position_steps() -> int:
    """当前位置（步数，0~500）"""
    return _position

def get_position_pct() -> float:
    """当前位置（开度百分比，0.0~100.0）"""
    return round(_position / STEPS_PER_PCT, 1)


# ════════════════════════════════════════════════════════════
# 健康检测
# ════════════════════════════════════════════════════════════

def check_health() -> bool:
    """
    检测驱动器和通信状态
    返回 False = UART通信失败 或 驱动器短路/过温/欠压保护触发
    动作前调用，故障时上层应写入 Error_point 并停机
    """
    if not _motor.check_driver():
        print("[EXV] 驱动器故障（通信失败或保护触发）")
        return False
    return True


# ════════════════════════════════════════════════════════════
# 归零（上电必须先调用）
# ════════════════════════════════════════════════════════════

async def homing() -> bool:
    """
    300Hz运行2s走到机械端点
    上电、定期修正后都应调用
    """
    global _position, _move_count
    if not check_health():
        return False
    print("[EXV] homing...")
    ok = await _motor.homing()
    if ok:
        _position = 0
        _move_count = 0
        print("[EXV] homing OK, position = 0 (全关)")
    return ok


# ════════════════════════════════════════════════════════════
# 基础运动（步数级别，带软件限位）
# ════════════════════════════════════════════════════════════

async def open_steps(steps: int, freq: int = 40,
                     energize_ms: int = 750) -> bool:
    """开阀 N 步，自动限位不超过全开"""
    global _position, _move_count
    if not check_health():
        return False
    steps = min(steps, FULL_STROKE - _position)
    if steps <= 0:
        return True
    ok = await _motor.async_move(steps, freq, DIR_OPEN, energize_ms)
    if ok:
        _position = min(FULL_STROKE, _position + steps)
        _move_count += 1
    return ok

async def close_steps(steps: int, freq: int = 40,
                      energize_ms: int = 750) -> bool:
    """关阀 N 步，自动限位不超过全关"""
    global _position, _move_count
    if not check_health():
        return False
    steps = min(steps, _position)
    if steps <= 0:
        return True
    ok = await _motor.async_move(steps, freq, DIR_CLOSE, energize_ms)
    if ok:
        _position = max(0, _position - steps)
        _move_count += 1
    return ok


# ════════════════════════════════════════════════════════════
# 高级接口（开度级别）
# ════════════════════════════════════════════════════════════

async def move_to_pct(target_pct: float, freq: int = 40,
                      energize_ms: int = 750) -> bool:
    """绝对定位到指定开度（0.0~100.0%）"""
    target_pct   = max(0.0, min(100.0, target_pct))
    target_steps = round(target_pct * STEPS_PER_PCT)
    delta        = target_steps - _position

    if delta > 0:
        return await open_steps(delta, freq, energize_ms)
    elif delta < 0:
        return await close_steps(abs(delta), freq, energize_ms)
    return True

async def move_delta_pct(delta_pct: float, freq: int = 40,
                         energize_ms: int = 750) -> bool:
    """
    按开度增量移动（PID输出直接传入）
    delta_pct > 0 → 开阀
    delta_pct < 0 → 关阀
    """
    steps = round(abs(delta_pct) * STEPS_PER_PCT)
    if steps == 0:
        return True
    if delta_pct > 0:
        return await open_steps(steps, freq, energize_ms)
    else:
        return await close_steps(steps, freq, energize_ms)

async def full_open(freq: int = 40,
                    energize_ms: int = 750) -> bool:
    """全开"""
    return await move_to_pct(100.0, freq, energize_ms)

async def full_close() -> bool:
    """全关（走归零流程，同时修正累积误差）"""
    return await homing()


# ════════════════════════════════════════════════════════════
# 定期归零（防丢步）
# ════════════════════════════════════════════════════════════

# async def check_and_rehome() -> bool:
#     """
#     在系统空闲时调用
#     每 REHOME_INTERVAL 次动作后自动归零修正累积误差
#     """
#     if _move_count >= REHOME_INTERVAL:
#         print("[EXV] 定期归零修正...")
#         return await homing()
#     return True


#线圈
# 红    橙    灰    蓝    黑    黄
# A+    B+    A-    B-   COM   COM    