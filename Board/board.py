from HAL.SPI import SPIBus,SPIDev
from HAL.ADC_sample import SmoothedADC
from pyb import UART,ADC, Pin
import adc_direct

#    膨胀阀  
#         PE7, PE8  UART7  TX  RX
#         PD13, PE10, PE12  STEP   Dir   EN   
#    电流传感器



#RTD
# board 只提供总线/片选等"哑资源"；AD7124 芯片实例由 Sensor 子系统
# 惰性创建自管（见 Sensor/read_sensors.py），避免 import 期初始化失败
# 导致整个系统拒绝启动。
AD7124_spi = SPIBus(1,500_000,1,1)
AD7124_cs = SPIDev(AD7124_spi,"PG15")

# HMI UART（PC 上位机，见 docs/PC_HMI_UART_DESIGN.md 第 5 节）
# UART1: PB6(TX)/PB7(RX)；原 LCD_Uart 已移除，HMI 替代组态屏
HMI_UART = UART(1, 115200, bits=8, parity=None, stop=1, timeout=0, read_buf_len=1024)

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

#ModeBus RTU（正式方案）
BAUDRATE   = 9600
def _silence_ms(baud):
    """T3.5 帧间静默时间（ms），用来判断一帧是否收完"""
    if baud > 19200:
        return 2
    t_char_ms = 11000.0 / baud
    return max(2, int(t_char_ms * 3.5) + 1)
ModeBus_Screen = UART(8,BAUDRATE, bits=8, parity=None, stop=1,
             timeout=2, timeout_char=_silence_ms(BAUDRATE), read_buf_len=256)




#ADC
#Presser
adc_pc2c = SmoothedADC(adc_direct.read_pc2_c, window=16)
adc_pc3c = SmoothedADC(adc_direct.read_pc3_c, window=16)
#current
adc_current = ADC(Pin('PA4'))
#backup
adc_backup = ADC(Pin('PA5'))

# Step5a: 水流开关 DI（软件占位，待硬件确认引脚后替换为真实 Pin）
class _FlowDISwitch:
    def __init__(self):
        self._state = 1  # 1=通水正常，0=断开
    def value(self, v=None):
        if v is not None:
            self._state = 1 if v else 0
        return self._state

di_flow = _FlowDISwitch()
