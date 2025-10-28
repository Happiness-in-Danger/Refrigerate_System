# main.py -- put your code here!
from pyb import Pin
import time
import json



with open('config.json', 'r') as f:
    config = json.load(f)
