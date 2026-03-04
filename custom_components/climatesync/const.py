"""Constants for ClimateSync integration."""

DOMAIN = "climatesync"

CONF_SOURCE_ENTITIES = "source_entities"
CONF_DESTINATION_ENTITY = "destination_entity"
CONF_IDLE_TEMPERATURE = "idle_temperature"
CONF_ROUNDING_MODE = "rounding_mode"
CONF_RESYNC_INTERVAL = "resync_interval_seconds"
CONF_MIN_CHANGE_THRESHOLD = "min_change_threshold"
CONF_MIN_SEND_INTERVAL = "min_send_interval_seconds"

DEFAULT_IDLE_TEMPERATURE = 5.0
DEFAULT_ROUNDING_MODE = "1_decimal"
DEFAULT_RESYNC_INTERVAL = 60
DEFAULT_MIN_CHANGE_THRESHOLD = 0.2
DEFAULT_MIN_SEND_INTERVAL = 10

ROUNDING_MODE_HALF = "half_step"
ROUNDING_MODE_1DEC = "1_decimal"
ROUNDING_MODE_2DEC = "2_decimals"

ROUNDING_MODES = [ROUNDING_MODE_HALF, ROUNDING_MODE_1DEC, ROUNDING_MODE_2DEC]

# Status states for sensor.climatesync_status
STATUS_OK = "ok"
STATUS_RATE_LIMITED = "rate_limited"
STATUS_DESTINATION_UNAVAILABLE = "destination_unavailable"
STATUS_MISSING_SOURCE_DATA = "missing_source_data"
STATUS_APPLY_FAILED = "apply_failed"
STATUS_MISMATCH = "mismatch"
