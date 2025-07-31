from datetime import timedelta
import logging

from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.components.valve import DOMAIN as VALVE_DOMAIN
from homeassistant.components.fan import DOMAIN as FAN_DOMAIN

from custom_components.dual_smart_thermostat.hvac_controller.generic_controller import (
    GenericHvacController,
)
from custom_components.dual_smart_thermostat.hvac_controller.hvac_controller import (
    HvacGoal,
)
from custom_components.dual_smart_thermostat.hvac_device.generic_hvac_device import (
    GenericHVACDevice,
)
from custom_components.dual_smart_thermostat.managers.environment_manager import (
    EnvironmentManager,
)
from custom_components.dual_smart_thermostat.managers.feature_manager import (
    FeatureManager,
)
from custom_components.dual_smart_thermostat.managers.hvac_power_manager import (
    HvacPowerManager,
)
from custom_components.dual_smart_thermostat.managers.opening_manager import (
    OpeningManager,
)

_LOGGER = logging.getLogger(__name__)


class CustomPositionValveWithFanDevice(GenericHVACDevice):
    """
    Custom valve device with fan control that manages both valve position and fan speed:
    - Valve: Off at 55%, Cooling 50-20% (20% fully open), Heating 60-100% (100% fully open)
    - Fan: Standard 0% (off) to 100% (full speed) control
    Both devices work simultaneously based on temperature conditions.
    """

    hvac_modes = [HVACMode.HEAT, HVACMode.COOL, HVACMode.OFF]

    def __init__(
        self,
        hass: HomeAssistant,
        valve_entity_id: str,
        fan_entity_id: str,
        min_cycle_duration: timedelta,
        initial_hvac_mode: HVACMode,
        environment: EnvironmentManager,
        openings: OpeningManager,
        features: FeatureManager,
        hvac_power: HvacPowerManager,
        hvac_goal: HvacGoal,
    ) -> None:
        # Use valve entity as primary entity for the base class
        super().__init__(
            hass,
            valve_entity_id,
            min_cycle_duration,
            initial_hvac_mode,
            environment,
            openings,
            features,
            hvac_power,
            hvac_goal,
        )

        self.valve_entity_id = valve_entity_id
        self.fan_entity_id = fan_entity_id

        self.hvac_controller = GenericHvacController(
            hass,
            valve_entity_id,  # Primary entity for controller
            min_cycle_duration,
            environment,
            openings,
            self.async_turn_on,
            self.async_turn_off
        )

        # Custom valve position constants
        self.OFF_POSITION = 55
        self.COOLING_MIN_POSITION = 50
        self.COOLING_MAX_POSITION = 20  # 20% is fully open for cooling
        self.HEATING_MIN_POSITION = 60
        self.HEATING_MAX_POSITION = 100  # 100% is fully open for heating

    def get_device_ids(self) -> list[str]:
        """Return both valve and fan entity IDs."""
        return [self.valve_entity_id, self.fan_entity_id]

    @property
    def target_env_attr(self) -> str:
        return (
            "_target_temp_low" if self.features.is_range_mode else self._target_env_attr
        )

    @property
    def hvac_action(self) -> HVACAction:
        _LOGGER.debug(
            "CustomPositionValveWithFanDevice hvac_action. is_active: %s, hvac_mode: %s",
            self.is_active,
            self.hvac_mode,
        )
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        if self.is_active:
            if self.hvac_mode == HVACMode.HEAT:
                return HVACAction.HEATING
            elif self.hvac_mode == HVACMode.COOL:
                return HVACAction.COOLING
        return HVACAction.IDLE

    def _calculate_valve_position(self) -> int:
        """Calculate the valve position based on HVAC mode and temperature difference."""
        if self.hvac_mode == HVACMode.OFF:
            return self.OFF_POSITION

        # Get current and target temperatures
        current_temp = self.environment.cur_temp
        target_temp = getattr(self.environment, self.target_env_attr)

        if current_temp is None or target_temp is None:
            _LOGGER.warning("Temperature values not available, using default position")
            return self.OFF_POSITION

        temp_difference = abs(current_temp - target_temp)

        # Use power tolerance to determine the range for position calculation
        self.hvac_power.update_hvac_power(self.strategy, self.target_env_attr, self.hvac_action)
        # power_tolerance = self.hvac_power._get_hvac_power_tolerance(True)
        # power_tolerance = self.hvac_power._get_hvac_power_tolerance(True)  # True for temperature

        # Normalize the temperature difference (0.0 to 1.0)
        normalized_diff = self.hvac_power.hvac_power_percent/100

        if self.hvac_mode == HVACMode.COOL:
            # Cooling: 50% (min demand) to 20% (max demand/fully open)
            position_range = self.COOLING_MIN_POSITION - self.COOLING_MAX_POSITION
            position = self.COOLING_MIN_POSITION - (normalized_diff * position_range)
            return max(self.COOLING_MAX_POSITION, min(int(position), self.COOLING_MIN_POSITION))

        elif self.hvac_mode == HVACMode.HEAT:
            # Heating: 60% (min demand) to 100% (max demand/fully open)
            position_range = self.HEATING_MAX_POSITION - self.HEATING_MIN_POSITION
            position = self.HEATING_MIN_POSITION + (normalized_diff * position_range)
            return max(self.HEATING_MIN_POSITION, min(int(position), self.HEATING_MAX_POSITION))

        return self.OFF_POSITION

    def _calculate_fan_speed(self) -> int:
        """Calculate fan speed percentage based on temperature difference and power levels."""
        if self.hvac_mode == HVACMode.OFF:
            return 0

        # Use the power management system to get the current power percentage
        # This will be calculated based on temperature difference
        return self.hvac_power.hvac_power_percent

    async def async_turn_on(self):
        """Turn on both valve and fan by setting appropriate positions/speeds."""
        _LOGGER.info(
            "%s. Setting valve position and fan speed for entities %s, %s",
            self.__class__.__name__,
            self.valve_entity_id,
            self.fan_entity_id,
        )

        if self.valve_entity_id is None or self.fan_entity_id is None:
            _LOGGER.warning("Valve or fan entity ID is None")
            return

        # Set valve position
        valve_position = self._calculate_valve_position()
        await self._async_set_valve_position(valve_position)

        # Set fan speed
        fan_speed = self._calculate_fan_speed()
        await self._async_set_fan_speed(fan_speed)

    async def async_turn_off(self):
        """Turn off both valve and fan."""
        _LOGGER.info(
            "%s. Turning off valve and fan for entities %s, %s",
            self.__class__.__name__,
            self.valve_entity_id,
            self.fan_entity_id,
        )

        if self.valve_entity_id is not None:
            await self._async_set_valve_position(self.OFF_POSITION)

        if self.fan_entity_id is not None:
            await self._async_set_fan_speed(0)

        self.hvac_power.update_hvac_power(
            self.strategy, self.target_env_attr, HVACAction.OFF
        )

    async def _async_set_valve_position(self, position: int) -> None:
        """Set the valve to a specific position percentage."""
        _LOGGER.info(
            "%s. Setting valve position to %s%% for entity %s",
            self.__class__.__name__,
            position,
            self.valve_entity_id,
        )

        if self.valve_entity_id is not None:
            try:
                await self.hass.services.async_call(
                    VALVE_DOMAIN,
                    "set_valve_position",
                    {
                        ATTR_ENTITY_ID: self.valve_entity_id,
                        "position": position,
                    },
                    context=self._context,
                    blocking=True,
                )
                _LOGGER.debug("Successfully set valve position to %s%%", position)
            except Exception as e:
                _LOGGER.error(
                    "Error setting valve position for entity %s to %s%%. Error: %s",
                    self.valve_entity_id,
                    position,
                    e,
                )

    async def _async_set_fan_speed(self, speed_percent: int) -> None:
        """Set the fan to a specific speed percentage."""
        _LOGGER.info(
            "%s. Setting fan speed to %s%% for entity %s",
            self.__class__.__name__,
            speed_percent,
            self.fan_entity_id,
        )

        if self.fan_entity_id is not None:
            try:
                if speed_percent == 0:
                    # Turn off the fan
                    await self.hass.services.async_call(
                        FAN_DOMAIN,
                        "turn_off",
                        {ATTR_ENTITY_ID: self.fan_entity_id},
                        context=self._context,
                        blocking=True,
                    )
                else:
                    # Set fan speed
                    await self.hass.services.async_call(
                        FAN_DOMAIN,
                        "set_percentage",
                        {
                            ATTR_ENTITY_ID: self.fan_entity_id,
                            "percentage": speed_percent,
                        },
                        context=self._context,
                        blocking=True,
                    )
                _LOGGER.debug("Successfully set fan speed to %s%%", speed_percent)
            except Exception as e:
                _LOGGER.error(
                    "Error setting fan speed for entity %s to %s%%. Error: %s",
                    self.fan_entity_id,
                    speed_percent,
                    e,
                )

    async def async_control_hvac(self, time=None, force=False):
        """Control both valve and fan based on temperature conditions."""
        _LOGGER.debug("CustomPositionValveWithFanDevice async_control_hvac called")

        # Let the parent class handle the basic control logic
        await super().async_control_hvac(time, force)

        # If the device should be active, set appropriate valve position and fan speed
        if self.is_active and self.hvac_mode != HVACMode.OFF:
            valve_position = self._calculate_valve_position()
            fan_speed = self._calculate_fan_speed()

            await self._async_set_valve_position(valve_position)
            await self._async_set_fan_speed(fan_speed)
