from pyb import Pin, SPI
from machine import SoftSPI
class SPIBus:
    def __init__(self,ID,freq,CPOL,CAHP,soft=False, sck_pin=None, mosi_pin=None, miso_pin=None):
        self.ID = ID
        self.freq = freq
        self.CPOL = CPOL
        self.CAHP = CAHP
        self.soft = soft

        if not soft:
            self.spi = SPI(ID,SPI.MASTER, baudrate=freq, polarity=CPOL, phase=CAHP)
        else:
            if not (sck_pin and mosi_pin and miso_pin):
                raise ValueError("SoftSPI need to set sck, mosi, miso 引脚")
            self.spi = SoftSPI(baudrate=freq,polarity=CPOL,phase=CAHP,sck=Pin(sck_pin),mosi=Pin(mosi_pin),miso=Pin(miso_pin))
        
class SPIDev:
    def __init__(self, bus, cs_pin, rdy_pin=None):
        self.bus = bus
        self.spi = bus.spi    
        self.cs = Pin(cs_pin, Pin.OUT)
        self.rdy= Pin(rdy_pin,Pin.IN)
    
    def is_ready(self):
        return self.rdy.value()

    def write(self,address, data):
        self.cs.value(0)  
        self.spi.write(bytearray([address | 0x80,data]))  
        self.cs.value(1) 

    def read(self,address,length):
        self.cs.value(0)
        self.spi.write(bytearray([address & 0x7F]))  
        out_data = self.spi.read(length)  
        self.cs.value(1)
        return out_data