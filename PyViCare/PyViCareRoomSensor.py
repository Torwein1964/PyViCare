from PyViCare.PyViCareDevice import ZigbeeBatteryDevice
from PyViCare.PyViCareUtils import handleNotSupported


class RoomSensor(ZigbeeBatteryDevice):
    """A zigbee climate sensor.

    The serial comes from ZigbeeDevice, which reads it from the radio data
    point: installations that route the measured values through the room of
    their RoomControl device expose no sensor data points on the device
    itself, and reading the serial from one of those left such a sensor
    without a serial - and therefore without the link to its gateway.
    """

    @handleNotSupported
    def getTemperature(self) -> float:
        return float(self.getProperty("device.sensors.temperature")["properties"]["value"]["value"])

    @handleNotSupported
    def getHumidity(self) -> float:
        return float(self.getProperty("device.sensors.humidity")["properties"]["value"]["value"])
