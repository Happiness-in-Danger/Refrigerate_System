from HAL.SPI import SPIBus,SPIDev
from HAL.ADC_sample import SmoothedADC
from Sensor.AD7124_PT100 import AD7124
from pyb import UART,ADC, Pin
import adc_direct

# 引脚分配（GPIO）
#    TIM1   DShot600
#         PE9, PE11, PE13, PE14
#    膨胀阀  
#         PE7, PE8  UART7  TX  RX
#         PD13, PE10, PE12  STEP   Dir   EN   
#    电流传感器



#RTD
AD7124_spi = SPIBus(1,500_000,1,1)
AD7124_cs = SPIDev(AD7124_spi,"PG15")
rts=AD7124(AD7124_spi.spi,AD7124_cs.cs)

#LCD
LCD_Uart = UART(1,115200)

REGS = (
    (0x0001, 'u16'),
    (0x0002, 'u16'),
    (0x0005, 'u16'),
    (0x0006, 'u16'),
    (0x0009, 'u16'),
    (0x0003, 'u16'),
    (0x0004, 'u16'),
    (0x000A, 'u16'),
    (0x0010, 'u32'),
)


#ADC
#Presser
adc_pc2c = SmoothedADC(adc_direct.read_pc2_c, window=16)
adc_pc3c = SmoothedADC(adc_direct.read_pc3_c, window=16)
#current
adc_current = ADC(Pin('PA4'))
#backup
adc_backup = ADC(Pin('PA5'))