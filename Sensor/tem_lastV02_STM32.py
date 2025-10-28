from pyb import Pin, SPI
from HAL.SPI import SPIBus,SPIDev
import time,math

spi = SPIBus(1,200000,1,1)
dev1 = SPIDev(spi,'PA4','PA3')

def calcPT100Temp(RTD_ADC_Code):
        R_REF = 430.0 # Reference Resistor
        Res0 = 100.0; # Resistance at 0 degC for 400ohm R_Ref
        a = .00390830
        b = -.000000577500
        # c = -4.18301e-12 # for -200 <= T <= 0 (degC)
        c = -0.00000000000418301
        # c = 0 # for 0 <= T <= 850 (degC)
        #print ("RTD ADC Code: %d" % RTD_ADC_Code)
        Res_RTD = (RTD_ADC_Code * R_REF) / 32768.0 # PT100 Resistance
        temp_C = -(a*Res0) + math.sqrt(a*a*Res0*Res0 - 4*(b*Res0)*(Res0 - Res_RTD))
        temp_C = temp_C / (2*(b*Res0))
        if (temp_C < 0): #use straight line approximation if less than 0
            # Can also use python lib numpy to solve cubic
            # Should never get here in this application
            temp_C = (RTD_ADC_Code/32) - 256
        return temp_C


def read_temp():
    dev1.write(0x80,0xC3)
    time.sleep(0.5)
    data=dev1.read(0x01,2)
    rtd_ADC_Code = (( data[0] << 8 ) | data[1] ) >> 1
    temp_C = calcPT100Temp(rtd_ADC_Code)
    return "%.2f"%temp_C

while 1:
    if dev1.is_ready()==0 :    
#     time.sleep(1)
        dev1.write(0x80,0xC3)
        time.sleep(0.5)
        data=dev1.read(0x01,2)
        rtd_ADC_Code = (( data[0] << 8 ) | data[1] ) >> 1
        temp_C = calcPT100Temp(rtd_ADC_Code)
        # f = spi_read(0x07, 1)
        # bit 7: RTD High Threshold / cable fault open 
        # bit 6: RTD Low Threshold / cable fault short
        # bit 5: REFIN- > 0.85 x VBias -> must be requested
        # bit 4: REFIN- < 0.85 x VBias (FORCE- open) -> must be requested
        # bit 3: RTDIN- < 0.85 x VBias (FORCE- open) -> must be requested
        # bit 2: Overvoltage / undervoltage fault
        # bits 1,0 don't care   
        print("温度：%.2f°C"%temp_C)