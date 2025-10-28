from pyb import ADC, Pin
import time

def read_psi(pin):
    pin
    adc = ADC(Pin(pin))
    P = adc.read()*25/3276-(25/8)
    if P<=0:
        P=0
    P+=1.013
    return "%.2f"%P

adc = ADC(Pin('PC0'))
# read orginal valve (0–4095)
val = adc.read()

voltage = val * 3.3 / 4095
p = val*25/3276-(25/8)
if p<0:
    p=0
p+=1.013
print("ADC1_IN0 =", "%.3fV"%voltage)
print("P =", "%.3fBar"%p)
# while 1:
#     time.sleep(1)
#     voltage = val * 3.3 / 4095
#     p = val*25/3276-(50/16)+1.013
#     print("ADC1_IN0 =", "%.3fV"%voltage)
#     print("P =", "%.3fBar"%p)

