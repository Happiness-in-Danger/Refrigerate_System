# ad7124_pt100.py — 最终验证版本
from machine import SPI, Pin
import time,math
import gc

class AD7124:
    REG_STATUS      = 0x00
    REG_ADC_CONTROL = 0x01
    REG_DATA        = 0x02
    REG_IO_CONTROL1 = 0x03
    REG_ID          = 0x05
    REG_CH0_MAP     = 0x09
    REG_CH1_MAP     = 0x0A
    REG_CH2_MAP     = 0x0B
    REG_CH3_MAP     = 0x0C
    REG_CH4_MAP     = 0x0D
    REG_CH5_MAP     = 0x0E
    REG_CH6_MAP     = 0x0F
    REG_CH7_MAP     = 0x10
    REG_CONFIG_0    = 0x19
    REG_FILTER_0    = 0x21
    REG_GAIN_0      = 0x31

    def __init__(self, spi, cs):
        self.spi = spi
        self.cs  = cs
        self.cs.value(1)
        self._reset()
        self._verify()

    def write_reg(self, addr, data, n):
        cs = self.cs
        cs.value(0)
        self.spi.write(bytes([addr & 0x3F]) + data.to_bytes(n, 'big'))
        cs.value(1)

    def read_reg(self, addr, n):
        buf = bytes([0x40 | (addr & 0x3F)]) + bytes(n)
        rx  = bytearray(n + 1)
        self.cs.value(0)
        self.spi.write_readinto(buf, rx)
        self.cs.value(1)
        r = 0
        for b in rx[1:]: r = (r << 8) | b
        return r

    def _reset(self):
        self.cs.value(0)
        self.spi.write(b'\xFF' * 8)
        self.cs.value(1)
        time.sleep_ms(10)

    def _verify(self):
        chip_id = self.read_reg(self.REG_ID, 1)
        if chip_id != 0x14:
            raise RuntimeError(f"AD7124未找到 ID=0x{chip_id:02X}")
#         print(f"AD7124-8 已连接 ID=0x{chip_id:02X}")

    def _waitread_regy(self, timeout=100):
        for _ in range(timeout):
            if not (self.read_reg(self.REG_STATUS, 1) & 0x80):
                return self.read_reg(self.REG_DATA, 3)
            time.sleep_ms(10)
        raise RuntimeError("转换超时")

#     # ── 片内温度传感器 ──────────────────────────────────────────
#     def config_internal_temp(self):
#         self.write_reg(self.REG_CONFIG_0,    0x0FF4, 2)  # 内部参考2.5V
#         self.write_reg(self.REG_CH0_MAP,     0x8211, 2)  # AINP=TEMP AINM=AVSS
#         #self.write_reg(self.REG_ADC_CONTROL, 0x0540, 2)  # 连续转换
#         self.write_reg(self.REG_ADC_CONTROL, 0x0544, 2)  # 单次转换
#         time.sleep_ms(100)
# 
#     def read_internal_temp(self):
#         raw = self._waitread_regy()
#         return round((raw - 0x800000) / 13584 - 272.5, 1)

    # ── PT100 四线制 ────────────────────────────────────────────
    # 接线：
    #   AIN0 ── RL1 ── RTD顶端
    #                  RTD底端 ── RL2 ── AIN1 (Sense+)
    #                             RL3 ── AIN2 (Sense-)
    #                             RL4 ── AGND
#     def config_pt100(self):
#         # 停止转换
#         self.write_reg(self.REG_ADC_CONTROL, 0x0000, 2)
#         time.sleep_ms(20)
# 
#         # CONFIG_0: 内部参考2.5V, BIPOLAR, REF_BUF, AIN_BUF, PGA=1
#         self.write_reg(self.REG_CONFIG_0, 0x0FF0, 2)
# 
#         # IO_CONTROL:            
#         io_ctrl = (0b000 << 11) | (0b110 << 8) | (0b0000 << 4) | (0b0000 << 0)
#         self.write_reg(self.REG_IO_CONTROL1, io_ctrl, 3)
# 
#         # CH0_MAP: AIN1(Sense+) / AIN2(Sense-)
#         self.write_reg(self.REG_CH0_MAP, 0x8022, 2)
# 
#         # FILTER_0: SINC3, FS=384 → 50Hz输出率，50Hz陷波
#         self.write_reg(self.REG_FILTER_0, (0b010 << 21) | 0x180, 3)
# 
#         # 连续转换，内部参考使能，中等功耗
#         self.write_reg(self.REG_ADC_CONTROL, 0x0540, 2)
#         time.sleep_ms(100)
# 
#     def read_resistance(self):
#         """R = V_sense / I_excitation，1mA激励，内部2.5V参考"""
#         raw = self._waitread_regy()
#         v = (raw - 0x800000) / 0x800000 * 2.5
#         return round(v / 0.001, 4)

    def read_temp(self):
        r = self.read_resistance()
        return resistance_to_temp(r)

    T_data=[0] * 5
    ch_config=(0x0022,0x0085,0x00E8,0x0148,0x01AE)
    io_val=(0b0000,0b0011,0b0110,0b1001,0b1100)
    def config_pt100_chs(self):
        
        T_data=[0] * 5
#         r_data=[]
        ch_config=(0x0022,0x0085,0x00E8,0x0148,0x01AE)
        io_val=(0b0000,0b0011,0b0110,0b1001,0b1100)
        
        # CONFIG_0: 内部参考REFIN, BIPOLAR, REF_BUF, AIN_BUF, PGA=16
        self.write_reg(self.REG_CONFIG_0, 0x09E4, 2)
        time.sleep_ms(5)
        self.write_reg(self.REG_FILTER_0, (0b0001 << 20) | 0x180, 3)
        time.sleep_ms(5)
        
        gc.collect()
        
        for i in range(5):
            io_ctrl = (0b000 << 11) | (0b100 << 8) | (0b0000 << 4) | (io_val[i] << 0)
            ch_map = ch_config[i] | 0x8000
            self.write_reg(self.REG_IO_CONTROL1, io_ctrl, 3)
            time.sleep_ms(5)
            self.write_reg(self.REG_CH0_MAP + i, ch_map, 2)
            time.sleep_ms(5)
            #REF_EN:0,Mode:0001:Power:10
            self.write_reg(self.REG_ADC_CONTROL, 0x0084, 2)
            time.sleep_ms(90)
            raw = self._waitread_regy()
            #(raw / (1 << 23) - 1) *  rref /(16* (1 << 23))
            r = (raw - 8388608) * (5100) / (134217728)
#             r_data.append(r)
            T_data[i] = resistance_to_temp(r)
            # 测完禁用
            self.write_reg(self.REG_CH0_MAP + i, ch_config[i] & 0x7FFF, 2)
            time.sleep_ms(10)
            
#         return T_data,r_data
        return T_data

# ── PT100 电阻→温度 IEC60751 牛顿迭代 ──────────────────────────
_R0 = 100.0
_A  =  3.9083e-3
_B  = -5.775e-7
_C  = -4.183e-12
_A_R0 = _A * _R0
_A2_R02 = (_A * _A) * (_R0 * _R0)
_4B_R0 = 4 * _B * _R0
_2B_R0 = 2 * _B * _R0

def resistance_to_temp(r):
    
    d = _A2_R02 - _4B_R0 * (_R0 - r)
    try:
        t = (-_A_R0 + math.sqrt(d)) / _2B_R0
        if t < 0:
            r2 = r * r
            r3 = r2 * r
            t = 242.02 + 2.2228*r + 0.0025859*r2 - 0.000004826*r3
        return int(t * 100) / 100
    except Exception as e:
        return 999
    
#     t= (-_A*_R0 + math.sqrt(_A*_A*_R0*_R0- 4*_B*_R0*(_R0-r)))/ (2*_B*_R0)
#     for _ in range(10):
#         if t<0:
#             t=242.02+2.2228*r+2.5859e-3*r**2-4.8260e-6*r**3
#     t = (r / _R0 - 1.0) / _A
#     for _ in range(10):
#         if t >= 0:
#             f  = _R0 * (1 + _A*t + _B*t**2) - r
#             df = _R0 * (_A + 2*_B*t)
#         else:
#             f  = _R0 * (1 + _A*t + _B*t**2 + _C*(t-100)*t**3) - r
#             df = _R0 * (_A + 2*_B*t + _C*(4*t**3 - 300*t**2))
#         t -= f / df
#         if abs(f) < 1e-6:
#             break
#     return round(t, 2)


# ── 主程序 ──────────────────────────────────────────────────────
def main():
    spi = SPI(1, baudrate=500_000, polarity=1, phase=1)
    cs = Pin('PA4', Pin.OUT, value=1)

    sensor = AD7124(spi, cs)
    # 先读片内温度确认正常
#     print("--- 片内温度 ---")
#     sensor.config_internal_temp()
#     for _ in range(3):
#         t = sensor.read_internal_temp()
#         print(f"  芯片温度: {t} °C")
#         time.sleep_ms(200)
# 
    # 切换PT100
    print("--- PT100 ---")
#     sensor.config_pt100()
#     while True:
#         r = sensor.read_resistance()
#         t = sensor.read_temp()
#         #print(f"  R={r:.3f}Ω")
#         print(f"  T={t:.2f}°C")
#         time.sleep_ms(500)
    while True:
        print(sensor.config_pt100_chs())
        time.sleep_ms(500)

if __name__ == '__main__':
    main()
