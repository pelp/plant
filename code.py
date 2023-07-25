import wifi
import asyncio
import board
import time
from webserver import Webserver, endpoint
from digitalio import DigitalInOut, Pull, Direction
from adafruit_vl53l4cd import VL53L4CD
from log import error, warning, info


# ---------------------------------------------------------------- | Constants |


moisture_sensor_pins = [
    DigitalInOut(board.GP13),
    DigitalInOut(board.GP14),
    DigitalInOut(board.GP15)
]
for pin in moisture_sensor_pins:
    pin.direction = Direction.INPUT
    pin.pull = Pull.DOWN


pump_a_pin = DigitalInOut(board.GP10)
pump_a_pin.direction = Direction.OUTPUT
pump_b_pin = DigitalInOut(board.GP11)
pump_b_pin.direction = Direction.OUTPUT
pump_c_pin = DigitalInOut(board.GP12)
pump_c_pin.direction = Direction.OUTPUT

label2pin = {'a': pump_a_pin,
             'b': pump_b_pin,
             'c': pump_c_pin}


i2ABC = ['A', 'B', 'C']
i2abc = ['a', 'b', 'c']

target_moisture = [60, 60, 60]
mlpp = 1
scmml = 0.00669518060338389 # s/cm/ml
pipe_volume = 40 # ml
tof = None
actual_moisture = [0, 0, 0]


# ---------------------------------------------------------- | General helpers |


ssid = "Netnet"
secret = "cirkelsag88"
def connect():
    networks = wifi.radio.start_scanning_networks()
    if ssid not in [network.ssid for network in networks]:
        error(f"{ssid} not found, please try again later.")
        return
    wifi.radio.stop_scanning_networks()
    wifi.radio.connect(ssid, secret)
    if not wifi.radio.connected:
        error(f"Failed to connect to {ssid}")
        return
    info(f"Connected to {ssid}")


def start_pump(pump):
    info(f"Starting pump {pump.upper()}.")
    label2pin[pump].value = True
    
    
def stop_pump(pump):
    info(f"Stopping pump {pump.upper()}.")
    label2pin[pump].value = False


async def dispense_water(pump, ml, tof):
    try:
        cm = sample_tof(tof)
        water_time = scmml * cm * (ml + pipe_volume)
        info(f"Dispensing {ml} ml")
        start_pump(pump)
        await asyncio.sleep(water_time)
        stop_pump(pump)
    except:
        stop_pump(pump)
        raise    

async def moisture_readings():                                                        
  results = []

  for i in range(0, 3):
    # count time for sensor to "tick" 25 times
    sensor = moisture_sensor_pins[i]

    last_value = sensor.value
    start = time.monotonic_ns()
    first = None
    last = None
    ticks = 0
    while ticks < 10 and time.monotonic_ns() - start <= 1_000_000_000:
      value = sensor.value
      if last_value != value:
        if first == None:
          first = time.monotonic_ns()
        last = time.monotonic_ns()
        ticks += 1
        last_value = value
      await asyncio.sleep(0)

    if not first or not last:
      results.append(0.0)
      continue

    # calculate the average tick between transitions in ms
    average = (last - first) / ticks
    # scale the result to a 0...100 range where 0 is very dry
    # and 100 is standing in water
    #
    # dry = 10ms per transition, wet = 80ms per transition
    min_ns = 20_000_000
    max_ns = 80_000_000
    average = max(min_ns, min(max_ns, average)) # clamp range
    scaled = ((average - min_ns) / (max_ns - min_ns)) * 100
    results.append(round(scaled, 2))
  
  return results


def sample_tof(tof, count=1):
    tof.start_ranging()
    distance = 0
    for c in range(count):
        while not tof.data_ready: pass
        tof.clear_interrupt()
        distance += tof.distance
    tof.stop_ranging()
    return distance / count

        
# ------------------------------------------------------------- | Main program |


async def watering_loop():
    global actual_moisture
    while True:
        info("Reading sensors...")
        actual_moisture = await moisture_readings()
        info("Sensor report:", actual_moisture)
        for i, (reading, target) in enumerate(zip(actual_moisture,
                                                  target_moisture)):
            if reading < target:
                info(f"Moisture too low in plant {i2ABC[i]}")
                diff = target - reading
                ml = diff * mlpp
                await dispense_water(i2abc[i], ml, tof)
        await asyncio.sleep(15 * 60) # 15 minutes between waterings



async def main():
    global tof
    i2c = board.STEMMA_I2C()
    info("Initializing i2c device...")
    tof = VL53L4CD(i2c, 0x29)
    tof.inter_measurement = 0
    tof.timing_budget = 200 # Maximum timing budget
    info("Initialization done.")
    if not wifi.radio.connected:
        connect()
    latency = wifi.radio.ping(wifi.radio.ipv4_gateway)
    if latency is None:
        warning("Could not ping gateway!")
    info(f"IP: {wifi.radio.ipv4_address}")
    await asyncio.gather(Webserver(1337), watering_loop())


@endpoint("/water")
async def water(pump=None, volume=None):
    if pump is None or volume is None:
        return (404, "Not found")
    pump = pump[-1].lower()
    await dispense_water(pump, float(volume), tof)

@endpoint("/moisture", method="GET")
async def moisture():
    global actual_moisture
    return str(actual_moisture)

asyncio.run(main())

