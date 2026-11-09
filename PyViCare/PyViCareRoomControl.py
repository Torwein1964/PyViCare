import re
from contextlib import suppress
from typing import Any

from PyViCare.PyViCareDevice import Device
from PyViCare.PyViCareUtils import (
    VICARE_DAYS,
    PyViCareCommandError,
    PyViCareNotSupportedFeatureError,
    handleAPICommandErrors,
    handleNotSupported,
)

# Room types accepted by the `rooms.add` command.
ROOM_TYPES = ["bathroom", "bedroom", "hallway", "livingroom", "kitchen",
              "office", "nursery", "toilet", "other"]

# Actor kinds accepted by the `rooms.N.addActor` command.
# cs = climate sensor, fht = floor heating thermostat, trv = radiator valve.
ACTOR_TYPES = ["cs", "fht", "trv", "airQualitySensor", "remoteControllerSensor"]


class RoomControl(Device):
    """Viessmann RoomControl virtual device.

    Rooms are exposed as objects, analogous to `HeatingDevice.circuits`:

        for room in device.rooms:
            print(room.id, room.getTemperature(), room.getHeatingSetpoint())

    How much of the room model an installation returns depends on the
    permissions granted to the API client. Features documented as
    "View Advanced Feature Data" or "Control Advanced Features" - room
    programs, schedule, quickmodes, operating state, co2 sensor - are absent
    on clients limited to "View Basic Feature Data", and `commands` blocks are
    then empty as well. Three shapes have been observed so far:

    * `temperature.levels.normal.heating` / `reduced.heating` as the setpoints
    * `temperature.levels.heating` as a single effective setpoint
    * full model including programs, schedule and `rooms.N` metadata

    Accessors therefore raise PyViCareNotSupportedFeatureError instead of
    assuming a shape. Use `isSupported()` or `contextlib.suppress` around them.

    The `rooms` and `rooms.N` parent features - carrying the room name, the
    room type and the `actors` list that maps physical devices to rooms - are
    not part of the publicly documented API. They appear in gateway bulk
    responses (see PyViCareServiceViaGateway) but can be missing entirely, in
    which case getName/getType/getActors raise and room ids are recovered by
    scanning feature names.

    Write accessors exist for the room features the API documents a command
    for. They fail with PyViCareCommandError when the installation returns no
    command block, which is the case for the whole room model on the basic
    scope. Command names observed in a real response are used as-is; the rest
    follow the naming of the same feature on a heating circuit and say so on
    the method, because no response seen so far carried them.
    """

    # ------------------------------------------------------------- room lists

    def _feature_names(self) -> list[str]:
        """Names of the features the device actually serves.

        Features flagged `isEnabled: False` are skipped. Without configured
        rooms the API still lists their slots with every feature disabled, and
        counting those as rooms produces room devices that do not exist.
        """
        features = self.service.fetch_all_features(self.accessor).get("data", [])
        return [str(f.get("feature", "")) for f in features if f.get("isEnabled")]

    def _scan_room_ids(self, pattern: str) -> list[str]:
        regex = re.compile(pattern)
        ids = {match.group(1) for name in self._feature_names()
               if (match := regex.match(name))}
        return sorted(ids, key=int)

    @handleNotSupported
    def getAvailableRoomIds(self) -> list[str]:
        """Room indices, from `rooms.enabled` when available.

        Falls back to scanning `rooms.<n>.*` feature names, because the parent
        `rooms` feature is missing on installations limited to the basic scope.
        """
        try:
            enabled = self.getProperty("rooms")["properties"]["enabled"]["value"]
            return sorted((str(x) for x in enabled), key=int)
        except (PyViCareNotSupportedFeatureError, KeyError):
            return self._scan_room_ids(r"^rooms\.(\d+)\.")

    @handleNotSupported
    def getAvailableOtherRoomIds(self) -> list[str]:
        """Indices of `rooms.others.N`, rooms without their own actors."""
        try:
            enabled = self.getProperty("rooms.others")["properties"]["enabled"]["value"]
            return sorted((str(x) for x in enabled), key=int)
        except (PyViCareNotSupportedFeatureError, KeyError):
            return self._scan_room_ids(r"^rooms\.others\.(\d+)\.")

    def getRoom(self, room_id: str) -> "Room":
        return Room(self, str(room_id))

    def getOtherRoom(self, room_id: str) -> "OtherRoom":
        return OtherRoom(self, str(room_id))

    @property
    def rooms(self) -> list["Room"]:
        return [self.getRoom(x) for x in self.getAvailableRoomIds()]

    @property
    def otherRooms(self) -> list["OtherRoom"]:
        return [self.getOtherRoom(x) for x in self.getAvailableOtherRoomIds()]

    # --------------------------------------------------- device-to-room map

    @handleNotSupported
    def getActorRoomMap(self) -> dict[str, str]:
        """Map actor device id -> room id.

        Built from the `actors` property of every `rooms.N` feature. Returns an
        empty mapping when the installation does not expose `rooms.N`, which is
        the case on clients limited to the basic scope.
        """
        mapping: dict[str, str] = {}
        for room in self.rooms:
            with suppress(PyViCareNotSupportedFeatureError):
                for actor in room.getActors():
                    device_id = actor.get("deviceId")
                    if device_id:
                        mapping[str(device_id)] = room.id
        return mapping

    @handleNotSupported
    def getRoomIdForDevice(self, device_id: str) -> str | None:
        """Room a physical device is assigned to, or None when unknown."""
        mapping: dict[str, str] = self.getActorRoomMap()
        return mapping.get(str(device_id))

    # ------------------------------------------------- installation-wide

    @handleNotSupported
    def getRoomsActive(self) -> bool:
        return bool(self.getProperty("rooms.status")["properties"]["active"]["value"])

    @handleNotSupported
    def getRoomsVariant(self) -> str:
        return str(self.getProperty("rooms.status")["properties"]["variant"]["value"])

    @handleNotSupported
    def getSupportedRoomsFeatures(self) -> list[str]:
        return list(self.getProperty("rooms.features")["properties"]["supported"]["value"])

    @handleNotSupported
    def getActivatedRoomsFeatures(self) -> list[str]:
        """Active supply-channel features, e.g. supplyChannel0FTDC."""
        return list(self.getProperty("rooms.features")["properties"]["activated"]["value"])

    @handleNotSupported
    def getRemotePresenceProbabilityActive(self) -> bool:
        return bool(self.getProperty("rooms.features.remotePresenceProbability")["properties"]["active"]["value"])

    @handleNotSupported
    def getRemotePresenceProbability(self) -> float:
        """Probability that everybody is away, from the geofencing of the app.

        `rooms.remotePresenceProbability` is the value belonging to the
        activation flag above. Only the feature name is documented, so the
        probability is read from `probability` and falls back to `value`.
        """
        properties = self.getProperty("rooms.remotePresenceProbability")["properties"]
        if "probability" in properties:
            return float(properties["probability"]["value"])
        return float(properties["value"]["value"])

    # ------------------------------------------- flat accessors (compatible)
    # Kept so existing callers keep working; each delegates to a Room.

    @handleNotSupported
    def getRoomTemperature(self, room_id: str) -> float:
        return float(self.getRoom(room_id).getTemperature())

    @handleNotSupported
    def getRoomHumidity(self, room_id: str) -> float:
        return float(self.getRoom(room_id).getHumidity())

    @handleNotSupported
    def getRoomCO2(self, room_id: str) -> int:
        return int(self.getRoom(room_id).getDesiredCO2Concentration())

    @handleNotSupported
    def getRoomCondensationRisk(self, room_id: str) -> bool:
        return bool(self.getRoom(room_id).getCondensationRisk())

    @handleNotSupported
    def getRoomSetpointHeating(self, room_id: str) -> float:
        return float(self.getRoom(room_id).getHeatingSetpoint())

    @handleNotSupported
    def getRoomSetpointCooling(self, room_id: str) -> float:
        return float(self.getRoom(room_id).getCoolingSetpoint())

    @handleNotSupported
    def getRoomSetpointComfortHeating(self, room_id: str) -> float:
        return float(self.getRoom(room_id).getComfortHeatingTemperature())

    @handleNotSupported
    def getRoomSetpointComfortCooling(self, room_id: str) -> float:
        return float(self.getRoom(room_id).getComfortCoolingTemperature())

    @handleNotSupported
    def getRoomSetpointNormalHeating(self, room_id: str) -> float:
        return float(self.getRoom(room_id).getNormalHeatingTemperature())

    @handleNotSupported
    def getRoomSetpointReducedHeating(self, room_id: str) -> float:
        return float(self.getRoom(room_id).getReducedHeatingTemperature())

    @handleNotSupported
    def getRoomSetpointNormalCooling(self, room_id: str) -> float:
        return float(self.getRoom(room_id).getNormalCoolingTemperature())

    @handleNotSupported
    def getRoomSetpointReducedCooling(self, room_id: str) -> float:
        return float(self.getRoom(room_id).getReducedCoolingTemperature())

    @handleNotSupported
    def getRoomSetpointNormalPerceived(self, room_id: str) -> float:
        return float(self.getRoom(room_id).getNormalPerceivedTemperature())

    @handleNotSupported
    def getRoomSetpointComfortPerceived(self, room_id: str) -> float:
        return float(self.getRoom(room_id).getComfortPerceivedTemperature())

    @handleNotSupported
    def getRoomChildLockActive(self, room_id: str) -> bool:
        return bool(self.getRoom(room_id).getChildLockActive())

    @handleNotSupported
    def getRoomChildLockStatus(self, room_id: str) -> str:
        return str(self.getRoom(room_id).getChildLockStatus())

    @handleNotSupported
    def getRoomWindowOpen(self, room_id: str) -> bool:
        return bool(self.getRoom(room_id).getWindowOpen())

    @handleNotSupported
    def getRoomOpenWindowDetectionEnabled(self, room_id: str) -> bool:
        return bool(self.getRoom(room_id).getOpenWindowDetectionEnabled())

    @handleNotSupported
    def getRoomHydraulicBalancingEnabled(self, room_id: str) -> bool:
        return bool(self.getRoom(room_id).getHydraulicBalancingEnabled())

    @handleNotSupported
    def getRoomTrvAlgorithmEnabled(self, room_id: str) -> bool:
        return bool(self.getRoom(room_id).getTrvAlgorithmEnabled())

    @handleNotSupported
    def getRoomHeatOnTimeEnabled(self, room_id: str) -> bool:
        return bool(self.getRoom(room_id).getHeatOnTimeEnabled())

    @handleNotSupported
    def getRoomOperatingLevel(self, room_id: str) -> str:
        return str(self.getRoom(room_id).getOperatingLevel())


class RoomBase:
    """Shared plumbing for `rooms.N` and `rooms.others.N`."""

    def __init__(self, device: RoomControl, room_id: str) -> None:
        self.device = device
        self.service = device.service
        self.room_id = room_id

    @property
    def id(self) -> str:
        return self.room_id

    @property
    def feature_prefix(self) -> str:
        """Feature name prefix of this room, without trailing dot."""
        raise NotImplementedError

    def getProperty(self, property_name: str) -> Any:
        return self.device.getProperty(property_name)

    def setProperty(self, property_name: str, action: str, data: Any) -> Any:
        return self.device.setProperty(property_name, action, data)

    def _executeCommand(self, feature_suffix: str, command: str, data: Any) -> Any:
        """Run a command of this room's `feature_suffix`.

        The API declares what is writable in the `commands` block of the
        feature itself, so that block is checked before anything is sent: a
        request to a command the installation does not offer only burns a call
        of the daily quota and comes back as a generic failure. The error says
        which commands the feature does offer instead.
        """
        property_name = f"{self.feature_prefix}.{feature_suffix}"
        try:
            feature = self.getProperty(property_name)
        except PyViCareNotSupportedFeatureError as error:
            raise PyViCareCommandError(
                f'{property_name} is not available on this installation'
            ) from error

        commands = feature.get("commands") or {}
        if command not in commands:
            offered = ", ".join(sorted(commands)) if commands else "none"
            raise PyViCareCommandError(
                f'{property_name} offers no command {command}'
                f' (offered: {offered})'
            )
        return self.setProperty(property_name, command, data)

    def hasCommand(self, feature_suffix: str, command: str) -> bool:
        """Whether the API offers `command` on this room's `feature_suffix`.

        Room features arrive without any command for clients that lack the
        "Control Advanced Features" permission, and callers that build a
        control out of a setter need to know that upfront instead of running
        into a failing request:

            if room.hasCommand("childLock", "setActive"):
                room.setChildLockActive(True)
        """
        property_name = f"{self.feature_prefix}.{feature_suffix}"
        try:
            feature = self.getProperty(property_name)
        except PyViCareNotSupportedFeatureError:
            return False
        return command in (feature.get("commands") or {})

    def _sensorConnected(self, getter: Any) -> bool:
        try:
            return bool(getter() == "connected")
        except PyViCareNotSupportedFeatureError:
            return False


class Room(RoomBase):
    """A single `rooms.N` entry of a RoomControl device."""

    @property
    def feature_prefix(self) -> str:
        return f"rooms.{self.room_id}"

    # ------------------------------------------------------------- metadata

    @handleNotSupported
    def getName(self) -> str:
        room_id = self.room_id
        return str(self.getProperty(f"rooms.{room_id}")["properties"]["name"]["value"])

    @handleNotSupported
    def getType(self) -> str:
        room_id = self.room_id
        return str(self.getProperty(f"rooms.{room_id}")["properties"]["type"]["value"])

    @handleNotSupported
    def getActors(self) -> list[dict[str, Any]]:
        """Devices assigned to this room.

        Example: [{'deviceId': 'zigbee-0123456789abcdef', 'heatingCircuit': 0}]
        """
        room_id = self.room_id
        return list(self.getProperty(f"rooms.{room_id}")["properties"]["actors"]["value"])

    @handleNotSupported
    def getActorDeviceIds(self) -> list[str]:
        return [str(a["deviceId"]) for a in self.getActors() if "deviceId" in a]

    # -------------------------------------------------------------- sensors

    @handleNotSupported
    def getTemperature(self) -> float:
        room_id = self.room_id
        return float(self.getProperty(f"rooms.{room_id}.sensors.temperature")["properties"]["value"]["value"])

    @handleNotSupported
    def getTemperatureSensorStatus(self) -> str:
        room_id = self.room_id
        return str(self.getProperty(f"rooms.{room_id}.sensors.temperature")["properties"]["status"]["value"])

    def isTemperatureSensorConnected(self) -> bool:
        return self._sensorConnected(self.getTemperatureSensorStatus)

    @handleNotSupported
    def getHumidity(self) -> float:
        room_id = self.room_id
        return float(self.getProperty(f"rooms.{room_id}.sensors.humidity")["properties"]["value"]["value"])

    @handleNotSupported
    def getHumiditySensorStatus(self) -> str:
        room_id = self.room_id
        return str(self.getProperty(f"rooms.{room_id}.sensors.humidity")["properties"]["status"]["value"])

    def isHumiditySensorConnected(self) -> bool:
        return self._sensorConnected(self.getHumiditySensorStatus)

    def getCO2(self) -> int:
        """Upstream's name for `getDesiredCO2Concentration()`.

        Both read `rooms.N.co2` -> `concentration`; kept so code written
        against upstream PyViCare keeps working. No decorator needed here -
        the delegate already carries @handleNotSupported.
        """
        return int(self.getDesiredCO2Concentration())

    @handleNotSupported
    def getCO2SensorStatus(self) -> str:
        """Status of the room's co2 sensor (`rooms.N.sensors.co2`)."""
        room_id = self.room_id
        return str(self.getProperty(f"rooms.{room_id}.sensors.co2")["properties"]["status"]["value"])

    @handleNotSupported
    def getCO2SensorLevel(self) -> str:
        """Measured co2 level of the room, mapped by the API into a level.

        `rooms.N.sensors.co2` carries the weighted average of all co2 sensors
        in the room as a level (a legend is part of the feature), not as a ppm
        value. Rooms without an air quality sensor report only `status`, so
        this raises there.
        """
        room_id = self.room_id
        return str(self.getProperty(f"rooms.{room_id}.sensors.co2")["properties"]["value"]["value"])

    @handleNotSupported
    def getDesiredCO2Concentration(self) -> int:
        """Desired co2 concentration (`rooms.N.co2`), not a measured value."""
        room_id = self.room_id
        return int(self.getProperty(f"rooms.{room_id}.co2")["properties"]["concentration"]["value"])

    @handleNotSupported
    def getCondensationRisk(self) -> bool:
        room_id = self.room_id
        return bool(self.getProperty(f"rooms.{room_id}.condensationRisk")["properties"]["value"]["value"])

    @handleNotSupported
    def getWindowOpen(self) -> bool:
        room_id = self.room_id
        return bool(self.getProperty(f"rooms.{room_id}.sensors.openWindow")["properties"]["value"]["value"])

    @handleNotSupported
    def getWindowOpenLegacy(self) -> bool:
        """Open window detection from the deprecated `sensors.window.openState`.

        Deprecated by Viessmann (removal date 2024-09-15) in favour of
        `sensors.openWindow`, which getWindowOpen reads. Firmware that still
        serves the old feature usually serves both; prefer getWindowOpen.
        """
        room_id = self.room_id
        return bool(self.getProperty(f"rooms.{room_id}.sensors.window.openState")["properties"]["value"]["value"])

    # ----------------------------------------------------- effective setpoint

    @handleNotSupported
    def getHeatingSetpoint(self) -> float:
        """Effective heating setpoint (`rooms.N.temperature.levels.heating`)."""
        room_id = self.room_id
        return float(self.getProperty(f"rooms.{room_id}.temperature.levels.heating")["properties"]["temperature"]["value"])

    @handleNotSupported
    def getCoolingSetpoint(self) -> float:
        """Effective cooling setpoint (`rooms.N.temperature.levels.cooling`)."""
        room_id = self.room_id
        return float(self.getProperty(f"rooms.{room_id}.temperature.levels.cooling")["properties"]["temperature"]["value"])

    # ----------------------------------------------------- calculated levels

    @handleNotSupported
    def getComfortHeatingTemperature(self) -> float:
        room_id = self.room_id
        return float(self.getProperty(f"rooms.{room_id}.temperature.levels.comfort.heating")["properties"]["temperature"]["value"])

    @handleNotSupported
    def getComfortCoolingTemperature(self) -> float:
        room_id = self.room_id
        return float(self.getProperty(f"rooms.{room_id}.temperature.levels.comfort.cooling")["properties"]["temperature"]["value"])

    @handleNotSupported
    def getComfortPerceivedTemperature(self) -> float:
        room_id = self.room_id
        return float(self.getProperty(f"rooms.{room_id}.temperature.levels.comfort.perceived")["properties"]["temperature"]["value"])

    @handleAPICommandErrors
    def setComfortPerceivedTemperature(self, temperature: float) -> Any:
        """Set the desired comfort level temperature of the room.

        Command name unconfirmed: no observed response carried the command
        block of `temperature.levels.comfort.perceived`, so it follows the
        heating circuit convention (`setTemperature` / `temperature`).
        """
        return self._executeCommand("temperature.levels.comfort.perceived",
                                    "setTemperature", {'temperature': float(temperature)})

    @handleNotSupported
    def getNormalHeatingTemperature(self) -> float:
        room_id = self.room_id
        return float(self.getProperty(f"rooms.{room_id}.temperature.levels.normal.heating")["properties"]["temperature"]["value"])

    @handleNotSupported
    def getNormalCoolingTemperature(self) -> float:
        room_id = self.room_id
        return float(self.getProperty(f"rooms.{room_id}.temperature.levels.normal.cooling")["properties"]["temperature"]["value"])

    @handleNotSupported
    def getNormalPerceivedTemperature(self) -> float:
        room_id = self.room_id
        return float(self.getProperty(f"rooms.{room_id}.temperature.levels.normal.perceived")["properties"]["temperature"]["value"])

    @handleAPICommandErrors
    def setNormalPerceivedTemperature(self, temperature: float) -> Any:
        """Set the desired normal level temperature of the room.

        Command name unconfirmed, see setComfortPerceivedTemperature.
        """
        return self._executeCommand("temperature.levels.normal.perceived",
                                    "setTemperature", {'temperature': float(temperature)})

    @handleNotSupported
    def getReducedHeatingTemperature(self) -> float:
        room_id = self.room_id
        return float(self.getProperty(f"rooms.{room_id}.temperature.levels.reduced.heating")["properties"]["temperature"]["value"])

    @handleNotSupported
    def getReducedCoolingTemperature(self) -> float:
        room_id = self.room_id
        return float(self.getProperty(f"rooms.{room_id}.temperature.levels.reduced.cooling")["properties"]["temperature"]["value"])

    @handleAPICommandErrors
    def setReferenceTemperature(self, temperature: float) -> Any:
        """Calibrate the room algorithms with a reference temperature.

        `rooms.N.temperature.reference` is command-only: the feature carries no
        properties, which is why there is no matching getter. Command name
        unconfirmed, see setComfortPerceivedTemperature.
        """
        return self._executeCommand("temperature.reference",
                                    "setTemperature", {'temperature': float(temperature)})

    # ------------------------------------------------------------- programs

    @handleNotSupported
    def getComfortProgramActive(self) -> bool:
        room_id = self.room_id
        return bool(self.getProperty(f"rooms.{room_id}.operating.programs.comfort")["properties"]["active"]["value"])

    @handleNotSupported
    def getComfortProgramTemperature(self) -> float:
        room_id = self.room_id
        return float(self.getProperty(f"rooms.{room_id}.operating.programs.comfort")["properties"]["temperature"]["value"])

    @handleAPICommandErrors
    def setComfortProgramTemperature(self, temperature: float) -> Any:
        return self._executeCommand("operating.programs.comfort",
                                    "setTemperature", {'targetTemperature': float(temperature)})

    @handleNotSupported
    def getComfortHeatingProgramActive(self) -> bool:
        room_id = self.room_id
        return bool(self.getProperty(f"rooms.{room_id}.operating.programs.comfortHeating")["properties"]["active"]["value"])

    @handleNotSupported
    def getComfortHeatingProgramTemperature(self) -> float:
        room_id = self.room_id
        return float(self.getProperty(f"rooms.{room_id}.operating.programs.comfortHeating")["properties"]["temperature"]["value"])

    @handleAPICommandErrors
    def setComfortHeatingProgramTemperature(self, temperature: float) -> Any:
        return self._executeCommand("operating.programs.comfortHeating",
                                    "setTemperature", {'targetTemperature': float(temperature)})

    @handleNotSupported
    def getComfortCoolingProgramActive(self) -> bool:
        room_id = self.room_id
        return bool(self.getProperty(f"rooms.{room_id}.operating.programs.comfortCooling")["properties"]["active"]["value"])

    @handleNotSupported
    def getComfortCoolingProgramTemperature(self) -> float:
        room_id = self.room_id
        return float(self.getProperty(f"rooms.{room_id}.operating.programs.comfortCooling")["properties"]["temperature"]["value"])

    @handleAPICommandErrors
    def setComfortCoolingProgramTemperature(self, temperature: float) -> Any:
        return self._executeCommand("operating.programs.comfortCooling",
                                    "setTemperature", {'targetTemperature': float(temperature)})

    @handleNotSupported
    def getNormalHeatingProgramActive(self) -> bool:
        room_id = self.room_id
        return bool(self.getProperty(f"rooms.{room_id}.operating.programs.normalHeating")["properties"]["active"]["value"])

    @handleNotSupported
    def getNormalHeatingProgramTemperature(self) -> float:
        room_id = self.room_id
        return float(self.getProperty(f"rooms.{room_id}.operating.programs.normalHeating")["properties"]["temperature"]["value"])

    @handleAPICommandErrors
    def setNormalHeatingProgramTemperature(self, temperature: float) -> Any:
        return self._executeCommand("operating.programs.normalHeating",
                                    "setTemperature", {'targetTemperature': float(temperature)})

    @handleNotSupported
    def getNormalCoolingProgramActive(self) -> bool:
        room_id = self.room_id
        return bool(self.getProperty(f"rooms.{room_id}.operating.programs.normalCooling")["properties"]["active"]["value"])

    @handleNotSupported
    def getNormalCoolingProgramTemperature(self) -> float:
        room_id = self.room_id
        return float(self.getProperty(f"rooms.{room_id}.operating.programs.normalCooling")["properties"]["temperature"]["value"])

    @handleAPICommandErrors
    def setNormalCoolingProgramTemperature(self, temperature: float) -> Any:
        return self._executeCommand("operating.programs.normalCooling",
                                    "setTemperature", {'targetTemperature': float(temperature)})

    @handleNotSupported
    def getReducedHeatingProgramActive(self) -> bool:
        room_id = self.room_id
        return bool(self.getProperty(f"rooms.{room_id}.operating.programs.reducedHeating")["properties"]["active"]["value"])

    @handleNotSupported
    def getReducedHeatingProgramTemperature(self) -> float:
        room_id = self.room_id
        return float(self.getProperty(f"rooms.{room_id}.operating.programs.reducedHeating")["properties"]["temperature"]["value"])

    @handleAPICommandErrors
    def setReducedHeatingProgramTemperature(self, temperature: float) -> Any:
        return self._executeCommand("operating.programs.reducedHeating",
                                    "setTemperature", {'targetTemperature': float(temperature)})

    @handleNotSupported
    def getReducedCoolingProgramActive(self) -> bool:
        room_id = self.room_id
        return bool(self.getProperty(f"rooms.{room_id}.operating.programs.reducedCooling")["properties"]["active"]["value"])

    @handleNotSupported
    def getReducedCoolingProgramTemperature(self) -> float:
        room_id = self.room_id
        return float(self.getProperty(f"rooms.{room_id}.operating.programs.reducedCooling")["properties"]["temperature"]["value"])

    @handleAPICommandErrors
    def setReducedCoolingProgramTemperature(self, temperature: float) -> Any:
        return self._executeCommand("operating.programs.reducedCooling",
                                    "setTemperature", {'targetTemperature': float(temperature)})

    # -------------------------------------------------------- operating state

    @handleNotSupported
    def getOperatingLevel(self) -> str:
        room_id = self.room_id
        return str(self.getProperty(f"rooms.{room_id}.operating.state")["properties"]["level"]["value"])

    @handleNotSupported
    def getOperatingDemand(self) -> str:
        room_id = self.room_id
        return str(self.getProperty(f"rooms.{room_id}.operating.state")["properties"]["demand"]["value"])

    @handleNotSupported
    def getOperatingReason(self) -> str:
        room_id = self.room_id
        return str(self.getProperty(f"rooms.{room_id}.operating.state")["properties"]["reason"]["value"])

    @handleNotSupported
    def getOperatingModifier(self) -> str:
        room_id = self.room_id
        return str(self.getProperty(f"rooms.{room_id}.operating.state")["properties"]["modifier"]["value"])

    # ------------------------------------------------------------ quickmodes

    @handleNotSupported
    def getManualQuickmodeActive(self) -> bool:
        room_id = self.room_id
        return bool(self.getProperty(f"rooms.{room_id}.quickmodes.manual")["properties"]["active"]["value"])

    @handleNotSupported
    def getManualQuickmodeDefaultDuration(self) -> int:
        room_id = self.room_id
        return int(self.getProperty(f"rooms.{room_id}.quickmodes.manual")["properties"]["defaultDuration"]["value"])

    @handleAPICommandErrors
    def activateManualQuickmode(self, temperature: float | None = None,
                                duration: int | None = None) -> Any:
        """Start the manual quick mode of the room.

        Both parameters of the `activate` command are required by the API but
        optional here, because the feature itself carries defaults for them:
        `duration` falls back to the room's defaultDuration, `temperature` to
        the setpoint the room is currently heating to. That makes "hold what
        we have for a while" a call without arguments, which is what a switch
        in a user interface needs.
        """
        if duration is None:
            duration = int(self.getManualQuickmodeDefaultDuration())
        if temperature is None:
            temperature = self._currentSetpoint()
        return self._executeCommand("quickmodes.manual", "activate",
                                    {'temperature': float(temperature), 'duration': int(duration)})

    def _currentSetpoint(self) -> float:
        """Temperature the room is heating to right now.

        Tried in the order the installations deliver them: the effective
        setpoint, then the desired comfort level, then the normal level.
        """
        for getter in (self.getHeatingSetpoint,
                       self.getComfortPerceivedTemperature,
                       self.getNormalPerceivedTemperature):
            try:
                return float(getter())
            except PyViCareNotSupportedFeatureError:
                continue
        raise PyViCareCommandError(
            f'{self.feature_prefix} reports no setpoint to fall back to,'
            ' pass a temperature'
        )

    @handleAPICommandErrors
    def deactivateManualQuickmode(self) -> Any:
        return self._executeCommand("quickmodes.manual",
                                    "deactivate", {})

    @handleAPICommandErrors
    def setManualQuickmodeTemperature(self, temperature: float) -> Any:
        return self._executeCommand("quickmodes.manual",
                                    "setTemperature", {'targetTemperature': float(temperature)})

    @handleNotSupported
    def getManualTillNextScheduleQuickmodeActive(self) -> bool:
        room_id = self.room_id
        return bool(self.getProperty(f"rooms.{room_id}.quickmodes.manualTillNextSchedule")["properties"]["active"]["value"])

    @handleAPICommandErrors
    def activateManualTillNextScheduleQuickmode(self) -> Any:
        """Hold the current temperature until the next schedule entry."""
        return self._executeCommand("quickmodes.manualTillNextSchedule",
                                    "activate", {})

    @handleAPICommandErrors
    def deactivateManualTillNextScheduleQuickmode(self) -> Any:
        return self._executeCommand("quickmodes.manualTillNextSchedule",
                                    "deactivate", {})

    # -------------------------------------------------------------- schedule

    @handleNotSupported
    def getSchedule(self) -> dict[str, Any]:
        room_id = self.room_id
        properties = self.getProperty(f"rooms.{room_id}.schedule")["properties"]
        schedule: dict[str, Any] = {"active": properties["active"]["value"]}
        for day in VICARE_DAYS:
            schedule[day] = properties["entries"]["value"][day]
        return schedule

    @handleAPICommandErrors
    def setSchedule(self, schedule: dict[str, Any]) -> Any:
        """Replace the heating schedule of the room."""
        return self._executeCommand("schedule",
                                    "setSchedule", {'newSchedule': schedule})

    @handleAPICommandErrors
    def resetSchedule(self) -> Any:
        """Restore the factory schedule of the room."""
        return self._executeCommand("schedule",
                                    "resetSchedule", {})

    # --------------------------------------------------------- configuration

    @handleNotSupported
    def getChildLockActive(self) -> bool:
        room_id = self.room_id
        return bool(self.getProperty(f"rooms.{room_id}.childLock")["properties"]["active"]["value"])

    @handleNotSupported
    def getChildLockStatus(self) -> str:
        room_id = self.room_id
        return str(self.getProperty(f"rooms.{room_id}.childLock")["properties"]["status"]["value"])

    @handleAPICommandErrors
    def setChildLockActive(self, active: bool) -> Any:
        """Lock or unlock the buttons of the room's eTRVs."""
        return self._executeCommand("childLock",
                                    "setActive", {'active': bool(active)})

    @handleAPICommandErrors
    def activateChildLock(self) -> Any:
        return self._executeCommand("childLock",
                                    "activate", {})

    @handleAPICommandErrors
    def deactivateChildLock(self) -> Any:
        return self._executeCommand("childLock",
                                    "deactivate", {})

    @handleNotSupported
    def getOpenWindowDetectionEnabled(self) -> bool:
        room_id = self.room_id
        return bool(self.getProperty(f"rooms.{room_id}.configuration.openWindow")["properties"]["active"]["value"])

    @handleAPICommandErrors
    def setOpenWindowDetectionEnabled(self, active: bool) -> Any:
        """Switch the 'Open window' function of the room on or off.

        Command name unconfirmed: the feature is documented as providing
        commands, but no observed response carried them. `setActive` matches
        the childLock feature, which has the same `active` shape.
        """
        return self._executeCommand("configuration.openWindow",
                                    "setActive", {'active': bool(active)})

    @handleNotSupported
    def getOpenWindowDetectionEnabledLegacy(self) -> bool:
        """'Open window' activation from the deprecated `configuration.window.openState`.

        Deprecated by Viessmann (removal date 2024-09-15) in favour of
        `configuration.openWindow`, read by getOpenWindowDetectionEnabled.
        """
        room_id = self.room_id
        return bool(self.getProperty(f"rooms.{room_id}.configuration.window.openState")["properties"]["active"]["value"])

    @handleNotSupported
    def getHydraulicBalancingEnabled(self) -> bool:
        room_id = self.room_id
        return bool(self.getProperty(f"rooms.{room_id}.configuration.hydraulicBalancing")["properties"]["value"]["value"])

    @handleNotSupported
    def getTrvAlgorithmEnabled(self) -> bool:
        room_id = self.room_id
        return bool(self.getProperty(f"rooms.{room_id}.configuration.trvAlgorithmActive")["properties"]["value"]["value"])

    @handleAPICommandErrors
    def setTrvAlgorithmEnabled(self, active: bool) -> Any:
        """Switch the TRV algorithm of the room on or off.

        Command name unconfirmed, see setOpenWindowDetectionEnabled. The
        feature reports its state in `value`, so `setValue` is sent.
        """
        return self._executeCommand("configuration.trvAlgorithmActive",
                                    "setValue", {'value': bool(active)})

    @handleNotSupported
    def getHeatOnTimeEnabled(self) -> bool:
        room_id = self.room_id
        return bool(self.getProperty(f"rooms.{room_id}.configuration.heatOnTime")["properties"]["active"]["value"])

    @handleAPICommandErrors
    def setHeatOnTimeEnabled(self, active: bool) -> Any:
        """Switch the heat on time algorithm of the room on or off.

        Command name unconfirmed, see setOpenWindowDetectionEnabled.
        """
        return self._executeCommand("configuration.heatOnTime",
                                    "setActive", {'active': bool(active)})

    @handleNotSupported
    def getAlgorithmConfiguration(self) -> dict[str, Any]:
        """Regulation parameters, e.g. hydraulicBalance, heatupSpeed, etrvSync."""
        room_id = self.room_id
        return dict(self.getProperty(f"rooms.{room_id}.algorithm")["properties"]["configuration"]["value"])

    @handleAPICommandErrors
    def setAlgorithmConfiguration(self, configuration: dict[str, Any]) -> Any:
        """Replace the regulation parameters of the room.

        Command name unconfirmed: `rooms.N.algorithm` is documented as
        providing a command to set its configuration, but no observed response
        carried the command block.
        """
        return self._executeCommand("algorithm",
                                    "setConfiguration", {'configuration': dict(configuration)})


class OtherRoom(RoomBase):
    """A `rooms.others.N` entry: a room known to the controller without own actors."""

    @property
    def feature_prefix(self) -> str:
        return f"rooms.others.{self.room_id}"

    @handleNotSupported
    def getTemperature(self) -> float:
        room_id = self.room_id
        return float(self.getProperty(f"rooms.others.{room_id}.sensors.temperature")["properties"]["value"]["value"])

    @handleNotSupported
    def getTemperatureSensorStatus(self) -> str:
        room_id = self.room_id
        return str(self.getProperty(f"rooms.others.{room_id}.sensors.temperature")["properties"]["status"]["value"])

    def isTemperatureSensorConnected(self) -> bool:
        return self._sensorConnected(self.getTemperatureSensorStatus)

    @handleNotSupported
    def getHumidity(self) -> float:
        room_id = self.room_id
        return float(self.getProperty(f"rooms.others.{room_id}.sensors.humidity")["properties"]["value"]["value"])

    @handleNotSupported
    def getHumiditySensorStatus(self) -> str:
        room_id = self.room_id
        return str(self.getProperty(f"rooms.others.{room_id}.sensors.humidity")["properties"]["status"]["value"])

    def isHumiditySensorConnected(self) -> bool:
        return self._sensorConnected(self.getHumiditySensorStatus)

    @handleNotSupported
    def getCondensationRisk(self) -> bool:
        room_id = self.room_id
        return bool(self.getProperty(f"rooms.others.{room_id}.condensationRisk")["properties"]["value"]["value"])

    # ------------------------------------------------------ effective levels

    @handleNotSupported
    def getHeatingSetpoint(self) -> float:
        room_id = self.room_id
        return float(self.getProperty(f"rooms.others.{room_id}.temperature.levels.heating")["properties"]["temperature"]["value"])

    @handleNotSupported
    def getCoolingSetpoint(self) -> float:
        room_id = self.room_id
        return float(self.getProperty(f"rooms.others.{room_id}.temperature.levels.cooling")["properties"]["temperature"]["value"])

    # ----------------------------------------------------- calculated levels

    @handleNotSupported
    def getComfortHeatingTemperature(self) -> float:
        room_id = self.room_id
        return float(self.getProperty(f"rooms.others.{room_id}.temperature.levels.comfort.heating")["properties"]["temperature"]["value"])

    @handleNotSupported
    def getComfortCoolingTemperature(self) -> float:
        room_id = self.room_id
        return float(self.getProperty(f"rooms.others.{room_id}.temperature.levels.comfort.cooling")["properties"]["temperature"]["value"])

    @handleNotSupported
    def getComfortPerceivedTemperature(self) -> float:
        room_id = self.room_id
        return float(self.getProperty(f"rooms.others.{room_id}.temperature.levels.comfort.perceived")["properties"]["temperature"]["value"])

    @handleAPICommandErrors
    def setComfortPerceivedTemperature(self, temperature: float) -> Any:
        """Set the desired comfort level temperature of the other room.

        Command name unconfirmed, see Room.setComfortPerceivedTemperature.
        """
        return self._executeCommand("temperature.levels.comfort.perceived",
                                    "setTemperature", {'temperature': float(temperature)})

    @handleNotSupported
    def getNormalHeatingTemperature(self) -> float:
        room_id = self.room_id
        return float(self.getProperty(f"rooms.others.{room_id}.temperature.levels.normal.heating")["properties"]["temperature"]["value"])

    @handleNotSupported
    def getNormalCoolingTemperature(self) -> float:
        room_id = self.room_id
        return float(self.getProperty(f"rooms.others.{room_id}.temperature.levels.normal.cooling")["properties"]["temperature"]["value"])

    @handleNotSupported
    def getNormalPerceivedTemperature(self) -> float:
        room_id = self.room_id
        return float(self.getProperty(f"rooms.others.{room_id}.temperature.levels.normal.perceived")["properties"]["temperature"]["value"])

    @handleAPICommandErrors
    def setNormalPerceivedTemperature(self, temperature: float) -> Any:
        """Set the desired normal level temperature of the other room.

        Command name unconfirmed, see Room.setComfortPerceivedTemperature.
        """
        return self._executeCommand("temperature.levels.normal.perceived",
                                    "setTemperature", {'temperature': float(temperature)})

    @handleNotSupported
    def getReducedHeatingTemperature(self) -> float:
        room_id = self.room_id
        return float(self.getProperty(f"rooms.others.{room_id}.temperature.levels.reduced.heating")["properties"]["temperature"]["value"])

    @handleNotSupported
    def getReducedCoolingTemperature(self) -> float:
        room_id = self.room_id
        return float(self.getProperty(f"rooms.others.{room_id}.temperature.levels.reduced.cooling")["properties"]["temperature"]["value"])

    # ------------------------------------------------------------- programs

    @handleNotSupported
    def getComfortProgramActive(self) -> bool:
        room_id = self.room_id
        return bool(self.getProperty(f"rooms.others.{room_id}.operating.programs.comfort")["properties"]["active"]["value"])

    @handleNotSupported
    def getComfortProgramTemperature(self) -> float:
        room_id = self.room_id
        return float(self.getProperty(f"rooms.others.{room_id}.operating.programs.comfort")["properties"]["temperature"]["value"])

    @handleAPICommandErrors
    def setComfortProgramTemperature(self, temperature: float) -> Any:
        return self._executeCommand("operating.programs.comfort",
                                    "setTemperature", {'targetTemperature': float(temperature)})

    # --------------------------------------------------------- configuration

    @handleNotSupported
    def getAlgorithmConfiguration(self) -> dict[str, Any]:
        room_id = self.room_id
        return dict(self.getProperty(f"rooms.others.{room_id}.algorithm")["properties"]["configuration"]["value"])

    @handleAPICommandErrors
    def setAlgorithmConfiguration(self, configuration: dict[str, Any]) -> Any:
        """Replace the regulation parameters of the other room.

        Command name unconfirmed, see Room.setAlgorithmConfiguration.
        """
        return self._executeCommand("algorithm",
                                    "setConfiguration", {'configuration': dict(configuration)})
