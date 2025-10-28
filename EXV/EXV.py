from pyb import Pin, Timer
from HAL.PWM import PWM
import time
# pwm0=PWM(Pin(5), freq=1000, duty_u16=32768)
# Pwm0 = Timer(4,freq=40)
# Ch1=Pwm0.channel(1,Timer.PWM,pin=Pin("PB6"),pulse_width_percent=50)
# Ch1.pulse_width_percent(50)
# PWM(4,1,40,50,"PB6")
# Dir=Pin(0, mode=Pin.OUT,value=0)
Dir=Pin("PB8", mode=Pin.OUT,value=0) #0, close;1 open
# ena=Pin(1, mode=Pin.OUT,value=0)
Ena=Pin("PB9", mode=Pin.OUT,value=0)#1,free
# led=Pin(25, mode=Pin.OUT,value=0)
led = Pin("PA6",Pin.OUT)

def step(steps, freq, dir,ena,pin,time_nom,ch):
    steps, freq, dir,ena,pin,time_nom,ch
    period = 1.0 / freq
    Ena(ena)
    Dir(dir)
    time.sleep(0.5)
    pwm=PWM(time_nom,ch,freq,50,pin)
    time.sleep(period*steps)
    pwm.deinit()
    time.sleep(0.5)
    Ena(1-ena)

led(0)
step(500,40,0,0,"PB7",4,2)
led(1)

#线圈
# 红    橙    灰    蓝    黑    黄
# A+    B+    A-    B-   COM   COM    