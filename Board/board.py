from HAL.SPI import SPIBus,SPIDev
from Sensor.AD7124_PT100 import AD7124
from pyb import UART
import adc_direct

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
adc_direct.read_pc2_c()
adc_direct.read_pc3_c()
