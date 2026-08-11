# EXV/TMC2209.py
# TMC2209 单线UART驱动 + StallGuard2 防失步
# 适用：三花 DPF(TS1) 系列 EXV，双极接法，6线中抽悬空
#
# 接线：
#   STEP     → PB7   (PWM脉冲)
#   DIR      → PB8
#   EN       → PB9   (低电平使能)
#   DIAG     → PB10  (StallGuard中断)
#   PDN_UART → STM32 UART2 TX（TX通过1kΩ电阻接RX，单线半双工）
#   MS1/MS2  → 悬空  (细分由UART控制)

from pyb import Pin, Timer, UART, ExtInt
import uasyncio as asyncio
import time

# ─── 寄存器地址 ──────────────────────────────────────────────
REG_GCONF       = 0x00
REG_GSTAT       = 0x01
REG_IHOLD_IRUN  = 0x10
REG_TPOWERDOWN  = 0x11
REG_TPWMTHRS    = 0x13
REG_TCOOLTHRS   = 0x14
REG_SGTHRS      = 0x40
REG_SG_RESULT   = 0x41
REG_CHOPCONF    = 0x6C
REG_PWMCONF     = 0x70


# ─── CRC8（TMC2209 UART协议必须）────────────────────────────
def _crc8(data: bytes) -> int:
    crc = 0
    for byte in data:
        for _ in range(8):
            if (crc >> 7) ^ (byte & 1):
                crc = ((crc << 1) ^ 0x07) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
            byte >>= 1
    return crc


class TMC2209:
    def __init__(self,
                 uart_id: int,
                 step_pin: str,
                 dir_pin: str,
                 en_pin: str,
                 addr: int = 0,
                 r_sense: float = 0.11,       # 采样电阻 Ω，常见0.11
                 default_current: int = 250,   # 运行电流 mA（DPF TS1规格260mA）
                 hold_current: int = 100,      # 保持电流 mA
                 microsteps: int = 16,          # 细分：2细分=400步/rev，与原TB6600一致
                 stall_threshold: int = 100,   # StallGuard阈值 0-255，调试后校准
                 ):
        self.addr = addr
        self.r_sense = r_sense
        self._stall_flag = False
        self._running = False
        self._tim = None

        # ── 引脚初始化（全部必须在 ExtInt 之前赋值）──────────
        self.step = Pin(step_pin, Pin.OUT, value=0)
        self.dir  = Pin(dir_pin,  Pin.OUT, value=0)
        self.en   = Pin(en_pin,   Pin.OUT, value=1)   # 高电平=禁用
        # self.diag = Pin(diag_pin, Pin.IN,  Pin.PULL_DOWN)

        # ── ExtInt 闭包（绕过 bound method 只能传1参数的问题）
        # _self = self
        # def _stall_cb(line):
        #     if _self._running:
        #         _self._stall_flag = True
        #         _self._emergency_stop()

        # self._stall_cb = _stall_cb    # 持有引用，防止 GC 回收
        # self._extint = ExtInt(
        #     self.diag,
        #     ExtInt.IRQ_RISING,
        #     Pin.PULL_DOWN,
        #     self._stall_cb
        # )

        # ── UART（单线半双工，TX/RX通过1kΩ电阻短接）──────────
        self.uart = UART(uart_id, 115200)

        # ── 等待驱动器上电稳定，再写寄存器 ───────────────────
        time.sleep_ms(100)
        self._flush_rx()
        self._init_registers(default_current, hold_current,
                             microsteps, stall_threshold)

    # ─── 紧急停止（ISR调用，不可有耗时操作）─────────────────
    # def _emergency_stop(self):
    #     if self._tim is not None:
    #         try:
    #             self._tim.deinit()
    #         except:
    #             pass
    #         self._tim = None
    #     self._running = False
    #     self.disable()

    # ─── UART工具 ─────────────────────────────────────────────
    def _flush_rx(self):
        time.sleep_ms(2)
        while self.uart.any():
            self.uart.read(self.uart.any())
    
    def _discard_echo(self, n: int):

        remaining = n
        deadline = 20  
        while remaining > 0 and deadline > 0:
            avail = self.uart.any()
            if avail > 0:
                to_read = min(avail, remaining)
                self.uart.read(to_read)
                remaining -= to_read
            else:
                time.sleep_ms(1)
                deadline -= 1


    # ─── UART 寄存器读写 ──────────────────────────────────────
    def _write_reg(self, reg: int, val: int):
       
        data = bytes([
            0x05, self.addr, reg | 0x80,
            (val >> 24) & 0xFF,
            (val >> 16) & 0xFF,
            (val >>  8) & 0xFF,
            (val      ) & 0xFF,
        ])
        frame = data + bytes([_crc8(data)])   
        self.uart.write(frame)
        self._discard_echo(8)               
        time.sleep_ms(1)

    def _read_reg(self, reg: int) -> int:

        req = bytes([0x05, self.addr, reg & 0x7F])
        req += bytes([_crc8(req)])         
        self.uart.write(req)
        self._discard_echo(4)               
        time.sleep_ms(2)                  
        raw = self.uart.read(8)
        if raw is None or len(raw) < 8:
            return -1
        if _crc8(raw[:7]) != raw[7]:
            return -1
        return (raw[3] << 24) | (raw[4] << 16) | (raw[5] << 8) | raw[6]

    # ─── 寄存器初始化 ─────────────────────────────────────────
    def _init_registers(self, run_ma, hold_ma, microsteps, sg_thrs):
        # GCONF:
        #   bit2 en_SpreadCycle = 1  → SpreadCycle模式（低速力矩稳定）
        #   bit6 pdn_disable    = 1  → 禁用PDN引脚，启用UART
        #   bit7 mstep_reg_select=1  → 细分由UART的CHOPCONF.MRES控制
        self._write_reg(REG_GCONF, (1 << 7) | (1 << 6) | (1 << 2))  # 0xC4

        # 电流设置
        irun  = self._ma_to_cs(run_ma)
        ihold = self._ma_to_cs(hold_ma)
        # IHOLDDELAY=6：保持电流延迟（约2.8s后降至IHOLD）
        self._write_reg(REG_IHOLD_IRUN, (6 << 16) | (irun << 8) | ihold)

        # 掉电延迟
        self._write_reg(REG_TPOWERDOWN, 20)

        # CHOPCONF：SpreadCycle参数，适合EXV低速大力矩场景
        #   TOFF  = 4  → 关断时间
        #   HSTRT = 4  → 磁滞启动（存储值3，实际=3+1=4）
        #   HEND  = 1  → 磁滞结束（存储值4，实际=4-3=1）
        #   TBL   = 1  → 空白时间24clk
        #   MRES       → 细分（由参数决定）
        #   INTPOL= 1  → 256步内插（步进更平滑）
        ms_map = {1: 8, 2: 7, 4: 6, 8: 5, 16: 4, 32: 3, 64: 2, 128: 1, 256: 0}
        mres = ms_map.get(microsteps, 7)   # 默认2细分(mres=7)
        chopconf = (
            4            |    # TOFF=4  (bits 3:0)
            (3    <<  4) |    # HSTRT存储值=3 (bits 6:4)
            (4    <<  7) |    # HEND存储值=4  (bits 10:7)
            (1    << 15) |    # TBL=1         (bits 16:15)
            (mres << 24) |    # MRES          (bits 27:24)
            (1    << 28)      # INTPOL=1      (bit 28)
        )
        self._write_reg(REG_CHOPCONF, chopconf)

        # TPWMTHRS=0：彻底禁用StealthChop，全速度段用SpreadCycle
        self._write_reg(REG_TPWMTHRS, 0)

        # # StallGuard配置
        # self._write_reg(REG_TCOOLTHRS, 0xFFFFF)  # 所有速度段都检测
        # self._write_reg(REG_SGTHRS, sg_thrs)     # 阈值，调试后校准

    def _ma_to_cs(self, ma: int) -> int:
        """
        电流 mA → TMC2209 CS寄存器值 (0-31)
        公式：CS = I_rms * 32 * R_sense * sqrt(2) / V_ref - 1
        V_ref(内部) = 0.325V
        注意：TMC2209设置的是RMS电流，规格书的260mA是峰值
              峰值260mA → RMS = 260/sqrt(2) ≈ 184mA
              实际建议设250mA留一点余量
        """
        cs = int(ma * 32 * self.r_sense * 1.41421 / 325.0) - 1
        return max(0, min(31, cs))

    # ─── 使能 / 禁用 ──────────────────────────────────────────
    def enable(self):
        self.en.value(0)       # 低电平使能
        time.sleep_ms(5)

    def disable(self):
        self.en.value(1)

    # ─── 状态查询 ─────────────────────────────────────────────
    def check_driver(self) -> bool:
        """
        读取 GSTAT 寄存器检测驱动器状态
        返回 True = 正常
        返回 False = UART通信失败 或 驱动器保护触发
        GSTAT bit0 = reset（上电复位，正常情况下读完自动清零）
        GSTAT bit1 = drv_err（短路/过温保护触发）
        GSTAT bit2 = uv_cp（电荷泵欠压）
        """
        val = self._read_reg(REG_GSTAT)
        if val < 0:
            return False   # UART通信失败
        if val & 0x06:
            return False   # bit1 drv_err 或 bit2 uv_cp
        return True

    # ─── PWM STEP脉冲（Timer4 Ch2 → PB7）────────────────────
    def _start_pwm(self, freq: int):
        self._tim = Timer(4, freq=freq)
        self._tim.channel(2, Timer.PWM,
                          pin=self.step,
                          pulse_width_percent=50)

    def _stop_pwm(self):
        if self._tim:
            self._tim.deinit()
            self._tim = None

    # ─── 异步运动（核心接口）─────────────────────────────────
    async def async_move(self, steps: int, freq: int, direction: int,
                         energize_ms: int = 750) -> bool:
        if steps <= 0:
            return True
 
        self.dir.value(direction)
 
        # 动作前通电等待
        self.enable()
        await asyncio.sleep_ms(energize_ms)
 
        period_ms = int(1000 * steps / freq)
        self._running = True
        self._start_pwm(freq)
 
        await asyncio.sleep_ms(period_ms)
 
        self._stop_pwm()
        self._running = False
 
        # 动作后等待稳定再断电
        await asyncio.sleep_ms(energize_ms)
        self.disable()
        return True
    # async def async_move(self, steps: int, freq: int, direction: int) -> bool:
    #     """
    #     异步运行指定步数，不阻塞其他协程
    #     direction: 1=开阀, 0=关阀
    #     返回: True=正常完成, False=失步中止
    #     """
    #     if steps <= 0:
    #         return True

    #     self._stall_flag = False
    #     self.dir.value(direction)
    #     self.enable()
    #     await asyncio.sleep_ms(10)

    #     period_ms = int(1000 * steps / freq)
    #     self._running = True
    #     self._start_pwm(freq)

    #     elapsed = 0
    #     while elapsed < period_ms:
    #         if self._stall_flag:
    #             # ISR已经调用了_emergency_stop，这里只需返回
    #             return False
    #         await asyncio.sleep_ms(10)
    #         elapsed += 10

    #     self._stop_pwm()
    #     self._running = False
    #     self.disable()
    #     return True

    # ─── 归零（关阀到底）───────────────
    async def homing(self) -> bool:
        self.dir.value(0)
 
        # 动作前通电等待
        self.enable()
        await asyncio.sleep_ms(750)
 
        self._running = True
        self._start_pwm(300)
        await asyncio.sleep_ms(2000)
        self._stop_pwm()
        self._running = False
 
        # 动作后等待稳定再断电
        await asyncio.sleep_ms(750)
        self.disable()
        return True