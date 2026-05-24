from pyb import ADC, Pin
# 使用 PA0 → ADC1_IN0

def read_psi(pin):
    pin
    adc = ADC(Pin(pin))
    P = adc.read()*25/3276-(25/8)
    if P<=0:
        P=0
    P+=1.013
    return round(P, 2)

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

