from pyb import Pin, Timer
import utime
def PWM(timer,channel,freq,duty,pin):
    timer,channel,freq,duty,pin
    Pwm = Timer(timer,freq=freq)
    Ch=Pwm.channel(channel,Timer.PWM,pin=Pin(pin),pulse_width_percent=duty)
    return Pwm