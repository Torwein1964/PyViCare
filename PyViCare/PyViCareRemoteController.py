from PyViCare.PyViCareDevice import ZigbeeBatteryDevice
from PyViCare.PyViCareUtils import handleNotSupported


class RemoteController(ZigbeeBatteryDevice):
    """A zigbee remote control unit, e.g. a Vitotrol 300-E.

    It is a room device like the climate sensor and reports the same radio and
    battery data points. Its room temperature and humidity reach the API
    through the room of the RoomControl device it is assigned to, not through
    the device itself; the getters below are only answered by installations
    that expose the sensors on the device as well.
    """

    @handleNotSupported
    def getTemperature(self) -> float:
        return float(self.getProperty("device.sensors.temperature")["properties"]["value"]["value"])

    @handleNotSupported
    def getHumidity(self) -> float:
        return float(self.getProperty("device.sensors.humidity")["properties"]["value"]["value"])
