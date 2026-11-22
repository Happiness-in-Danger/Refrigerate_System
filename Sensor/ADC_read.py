from pyb import ADC, Pin
from Board.state import adc_bits, ADC_BITS_DEFAULT
# 使用 PA0 → ADC1_IN0

def _max_val(bits):
    """根据位宽计算 ADC 最大值"""
    return (1 << bits) - 1

def _is_adc_out_of_range(raw, max_val=65535, low_pct=0.02, high_pct=0.98):
    return raw <= max_val * low_pct or raw >= max_val * high_pct

def read_psi(pin):
    pin
    adc = ADC(Pin(pin))
    P = adc.read()*25/3276-(25/8)
    if P<=0:
        P=0
    P+=1.013
    return round(P, 2)

def psi_trans(adc):
    """
    压力传感器换算（PC2/PC3 走 adc_direct，16-bit 固定）
    注意：本函数仅用于 adc_direct 通道，不受 adc_bits 注册表影响
    """
    if _is_adc_out_of_range(adc):
        return 999
    voltage = (adc + 000 )/ 65535 * 3.3
    P_gauge = (voltage - 0.328) / 2.64 * 25
    P_abs = P_gauge + 1.01325
    return int(P_abs * 100) / 100

def read_current(adc, bits=ADC_BITS_DEFAULT):
    """
    整机电流采样 0-50A (PA4主采样，PA5备用)
    仅用于 pyb/machine ADC 通道；adc_direct 通道不走此函数
    
    Args:
        adc: ADC 原始值
        bits: ADC 位宽（从 state.adc_bits 传入）
    
    ACS712-50A: Vcc/2 @ 0A, 40mV/A sensitivity
    """
    m = _max_val(bits)
    if adc <= m * 0.02 or adc >= m * 0.98:
        return 999
    voltage = adc / m * 3.3
    # ACS712-50A: 中点 1.65V, 灵敏度 40mV/A
    # I = (V - 1.65) / 0.04
    current = (voltage - 1.65) / 0.04
    # 负值钳位到 0（单向电流）
    if current < 0:
        current = 0
    return round(current, 2)


# adc = ADC(Pin('PC0'))
# # 读取原始值 (0–4095)
# val = adc.read()

# voltage = val * 3.3 / 4095
# p = val*25/3276-(25/8)
# if p<0:
#     p=0
# p+=1.013
# print("ADC1_IN0 =", "%.3fV"%voltage)
# print("P =", "%.3fBar"%p)
# 转换为电压
# while 1:
#     time.sleep(1)
#     voltage = val * 3.3 / 4095
#     p = val*25/3276-(50/16)+1.013
#     print("ADC1_IN0 =", "%.3fV"%voltage)
#     print("P =", "%.3fBar"%p)