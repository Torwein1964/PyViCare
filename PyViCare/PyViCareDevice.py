from typing import Any

from PyViCare.PyViCareService import ViCareDeviceAccessor, ViCareService, hasRoles
from PyViCare.PyViCareUtils import (
    PyViCareNotSupportedFeatureError,
    handleAPICommandErrors,
    handleNotSupported,
)


class Device:
    """This is the base class for all simple devices.
    This class connects to the Viessmann ViCare API.
    The authentication is done through OAuth2.
    Note that currently, a new token is generated for each run.
    """

    def __init__(self, accessor: ViCareDeviceAccessor, service: ViCareService, roles: list[str] | None = None) -> None:
        self.accessor = accessor
        self.service = service
        self.roles = roles if roles is not None else []

    def getProperty(self, property_name: str) -> Any:
        return self.service.getProperty(self.accessor, property_name)

    def setProperty(self, property_name: str, action: str, data: Any) -> Any:
        return self.service.setProperty(self.accessor, property_name, action, data)

    @handleNotSupported
    def getSerial(self):
        return self.getProperty("device.serial")["properties"]["value"]["value"]

    @handleNotSupported
    def getDeviceErrors(self) -> list[Any]:
        return list[Any](self.getProperty("device.messages.errors.raw")["properties"]["entries"]["value"])

    # Power consumption limitation (§14a EnWG):
    @handleNotSupported
    def getPowerConsumptionLimit(self) -> int:
        return int(self.getProperty("device.power.statusReport.consumption")["properties"]["limit"]["value"])

    @handleNotSupported
    def getPowerConsumptionLimitUnit(self) -> str:
        return str(self.getProperty("device.power.statusReport.consumption")["properties"]["limit"]["unit"])

    @handleNotSupported
    def getPowerConsumptionLimitStatus(self) -> str:
        return str(self.getProperty("device.power.statusReport.consumption")["properties"]["status"]["value"])

    @handleNotSupported
    def getPowerConsumptionLimitationSource(self) -> str:
        return str(self.getProperty("device.power.consumption.limitation")["properties"]["value"]["value"])

    # Power production limitation (feed-in):
    @handleNotSupported
    def getPowerProductionLimit(self) -> int:
        return int(self.getProperty("device.power.statusReport.production")["properties"]["limit"]["value"])

    @handleNotSupported
    def getPowerProductionLimitUnit(self) -> str:
        return str(self.getProperty("device.power.statusReport.production")["properties"]["limit"]["unit"])

    @handleNotSupported
    def getPowerProductionLimitStatus(self) -> str:
        return str(self.getProperty("device.power.statusReport.production")["properties"]["status"]["value"])
    @handleNotSupported
    def getDeviceInfoMessages(self) -> list[Any]:
        return list[Any](self.getProperty("device.messages.info.raw")["properties"]["entries"]["value"])

    @handleNotSupported
    def getDeviceServiceMessages(self) -> list[Any]:
        """Pending service notices, e.g. maintenance due."""
        return list[Any](self.getProperty("device.messages.service.raw")["properties"]["entries"]["value"])

    @handleNotSupported
    def getDeviceStatusMessages(self) -> list[Any]:
        return list[Any](self.getProperty("device.messages.status.raw")["properties"]["entries"]["value"])

    @handleNotSupported
    def getDeviceVariant(self) -> str:
        return str(self.getProperty("device.variant")["properties"]["value"]["value"])

    @handleNotSupported
    def getDeviceBrand(self) -> str:
        return str(self.getProperty("device.brand")["properties"]["value"]["value"])

    @handleNotSupported
    def getExternalLockActive(self) -> bool:
        """External blocking contact. Successor of the deprecated heating.external.lock."""
        return bool(self.getProperty("device.lock.external")["properties"]["active"]["value"])

    @handleNotSupported
    def getMalfunctionLockActive(self) -> bool:
        return bool(self.getProperty("device.lock.malfunction")["properties"]["active"]["value"])

    @handleNotSupported
    def getZigbeeCoordinatorActive(self) -> bool:
        """Whether the device's own zigbee coordinator is switched on."""
        return bool(self.getProperty("device.zigbee.active")["properties"]["active"]["value"])

    @handleNotSupported
    def getZigbeeCoordinatorStatus(self) -> str:
        return str(self.getProperty("device.zigbee.status")["properties"]["status"]["value"])

    def isLegacyDevice(self) -> bool:
        return hasRoles(["type:legacy"], self.roles)

    def isE3Device(self) -> bool:
        return hasRoles(["type:E3"], self.roles)

    def isDomesticHotWaterDevice(self):
        return self._isTypeDevice("heating.dhw")

    def isSolarThermalDevice(self):
        return self._isTypeDevice("heating.solar")

    def isVentilationDevice(self):
        return self._isTypeDevice("ventilation")

    def _isTypeDevice(self, deviceType: str):
        try:
            return self.getProperty(deviceType)["isEnabled"] and self.getProperty(deviceType)["properties"]["active"]["value"]
        except PyViCareNotSupportedFeatureError:
            return False


class ZigbeeDevice(Device):

    @handleNotSupported
    def getSerial(self) -> str:
        return str(self.getProperty("device.zigbee.parent.id")["deviceId"])

    @handleNotSupported
    def getZigbeeParentID(self) -> str:
        return str(self.getProperty("device.zigbee.parent.id")["properties"]["value"]["value"])

    @handleNotSupported
    def getZigbeeSignalStrength(self) -> int:
        return int(self.getProperty("device.zigbee.lqi")["properties"]["strength"]["value"])

    @handleNotSupported
    def getName(self) -> str:
        return str(self.getProperty("device.name")["properties"]["name"]["value"])

    @handleAPICommandErrors
    def setName(self, name: str) -> None:
        self.setProperty("device.name", "setName", {'name': name})

    @handleNotSupported
    def getIdentification(self) -> bool:
        return bool(self.getProperty("device.identification")["properties"]["triggered"]["value"])

class ZigbeeBatteryDevice(ZigbeeDevice):

    @handleNotSupported
    def getBatteryLevel(self) -> int:
        return int(self.getProperty("device.power.battery")["properties"]["level"]["value"])
