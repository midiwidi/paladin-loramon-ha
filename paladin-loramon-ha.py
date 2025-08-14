import yaml
import serial
import logging
import time
from datetime import datetime
import json
import sys
import random
import os
import paho.mqtt.client as mqtt

client_id = f'python-mqtt-{random.randint(0, 1000)}'

# Global variables for reset detection and cumulative tracking
last_values = {}
reset_detected_today = False
last_reset_time = None
last_cleared_date = None  # Track what date we last cleared the reset flag
cumulative_totals = {}  # Store cumulative totals for energy sensors
last_raw_values = {}    # Store last raw values before reset

# File to persist cumulative totals
CUMULATIVE_DATA_FILE = "cumulative_totals.json"

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def is_number(s):
    try:
        float(s)
        return True
    except ValueError:
        return False

def load_cumulative_data():
    """Load cumulative totals from file if it exists."""
    global cumulative_totals
    if os.path.exists(CUMULATIVE_DATA_FILE):
        try:
            with open(CUMULATIVE_DATA_FILE, 'r') as f:
                cumulative_totals = json.load(f)
            logger.info(f"Loaded cumulative totals from {CUMULATIVE_DATA_FILE}: {cumulative_totals}")
        except Exception as e:
            logger.warning(f"Failed to load cumulative data: {e}, starting fresh")
            cumulative_totals = {}
    else:
        logger.info("No existing cumulative data file found, starting fresh")
        cumulative_totals = {}

def save_cumulative_data():
    """Save cumulative totals to file."""
    try:
        with open(CUMULATIVE_DATA_FILE, 'w') as f:
            json.dump(cumulative_totals, f, indent=2)
        logger.debug(f"Saved cumulative totals to {CUMULATIVE_DATA_FILE}")
    except Exception as e:
        logger.error(f"Failed to save cumulative data: {e}")
    
def load_config(config_path="config.yaml"):
    """Load configuration settings from a YAML file."""
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        logger.info("Loaded config from %s", config_path)
        return config
    except Exception as e:
        logger.error("Failed to load config file: %s", e)
        sys.exit(1)

def on_connect(client, userdata, flags, rc, properties):
    if rc == 0:
        logger.info("Connected to MQTT Broker!")
    else:
        logger.info("Failed to connect, return code %d\n", rc)

def connect_mqtt(config, delay=10):   
    # Set Connecting Client ID
    client = mqtt.Client(client_id=client_id, callback_api_version=mqtt.CallbackAPIVersion.VERSION2)

    mqtt_conf = config.get("mqtt", {})
    # Optionally set username and password if provided.
    if mqtt_conf.get("username") and mqtt_conf.get("password"):
        client.username_pw_set(mqtt_conf["username"], mqtt_conf["password"])

    host = mqtt_conf.get("host", "localhost")
    port = mqtt_conf.get("port", 1883)
    client.enable_logger()

    client.on_connect = on_connect
    
    while True:
        try:
            client.connect(host, port)
            logger.info("Connected to MQTT broker at %s:%s", host, port)
            return client
        except Exception as e:
            logger.warning("MQTT broker not ready (%s). Retrying in %s seconds...", e, delay)
            time.sleep(delay)


def publish_discovery(client, config):
    """
    Publish MQTT discovery messages so that Home Assistant auto-creates the device
    and its sensors. Only those sensor fields defined in the config are published.
    """
    mqtt_conf = config.get("mqtt", {})
    discovery_prefix = mqtt_conf.get("discovery_prefix", "homeassistant")
    state_prefix = mqtt_conf.get("state_prefix", "home/sensors/home_power")

    device_conf = config.get("device", {})
    device_name = device_conf.get("name", "SerialDevice")
    manufacturer = device_conf.get("manufacturer", "Unknown Manufacturer")
    model = device_conf.get("model", "Unknown Model")
    device_id = mqtt_conf.get("device_id", "default_device_id")

    sensors = config.get("sensors", {})
    
    # Automatically detect energy sensors based on device_class
    energy_sensors = [key for key, sensor_conf in sensors.items() 
                     if sensor_conf.get("device_class") == "energy"]

    # Iterate over defined sensor keys (sorted by their numeric value)
    for sensor_key in sorted(sensors, key=lambda x: int(x)):
        sensor_conf = sensors[sensor_key]
        sensor_name = sensor_conf.get("name")
        if not sensor_name:
            # If no sensor name is provided, skip this field.
            continue

        discovery_topic = f"{discovery_prefix}/sensor/{device_id}/{sensor_key}/config"
        sensor_state_topic = f"{state_prefix}/{sensor_key}/state"  # Remove device_id segment
        payload = {
            "state_topic": sensor_state_topic,
            "unique_id": f"{device_id}_{sensor_key}",
            "device": {
                "identifiers": [device_id],
                "name": device_name,
                "model": model,
                "manufacturer": manufacturer,
            },
        }
        
        # Add all attributes given in the config to the payload
        payload.update(sensor_conf)
        
        # For sensor 15 specifically, ensure it uses total_increasing
        if sensor_key == "15" and sensor_key in energy_sensors:
            payload["state_class"] = "total_increasing"
            # Remove last_reset_value_template if it exists in config
            payload.pop("last_reset_value_template", None)
            logger.info("Configured sensor 15 for cumulative (total_increasing) tracking")
        # For other energy sensors, add JSON parsing templates for last_reset support
        elif sensor_key in energy_sensors:
            payload["value_template"] = "{{ value_json.state }}"
            payload["json_attributes_topic"] = sensor_state_topic
            payload["json_attributes_template"] = "{{ value_json | tojson }}"

        client.publish(discovery_topic, payload=json.dumps(payload), retain=True)
        logger.info("Published discovery for sensor '%s' (field %s) on topic '%s'",
                     sensor_name, sensor_key, discovery_topic)

def open_serial_port(config):
    """Open the serial port using parameters from the config."""
    serial_conf = config.get("serial", {})
    port = serial_conf.get("port", "/dev/ttyUSB0")
    baud_rate = serial_conf.get("baud_rate", 9600)
    retry_delay = 1
    max_retry_delay = 3600
    while True:
        try:
            ser = serial.Serial(port, baud_rate, timeout=1)
            logger.info("Opened serial port %s at %s baud", port, baud_rate)
            return ser
        except serial.SerialException as e:
            logger.error(f"Error opening serial port: %s - retrying in {retry_delay}s ...", e)
            time.sleep(retry_delay)
            # Exponentially back-off the retry delay (double it) but limit it to max_retry_delay
            retry_delay = min(retry_delay * 2, max_retry_delay)

def process_line(line, config, mqtt_client):
    """
    Parse a newline-terminated, comma-separated string from the serial port.
    For each sensor defined in the config, publish its value (if present)
    to the corresponding MQTT state topic.
    """
    global last_values, reset_detected_today, last_reset_time, last_cleared_date, cumulative_totals, last_raw_values
    
    mqtt_conf = config.get("mqtt", {})
    state_prefix = mqtt_conf.get("state_prefix", "home/sensors/home_power")
    device_id = mqtt_conf.get("device_id", "default_device_id")
    sensors = config.get("sensors", {})

    line = line.strip()
    if not line:
        return

    # Split the line into fields
    fields = [field.strip() for field in line.split(",")]
    if len(fields) != 20:
        logger.warning(f'Serial data is missing fields. Received {len(fields)} but expected 20')
        return
    logger.debug("Received fields: %s", fields)

    # Check if we've moved to a new day (reset the daily flag) - but only once per day
    current_time = datetime.now()
    current_date = current_time.date()
    
    if last_reset_time and current_date > last_reset_time.date() and last_cleared_date != current_date:
        reset_detected_today = False
        last_cleared_date = current_date
        logger.info("New day detected, reset detection flag cleared")

    # Collect energy sensor values for reset detection
    # Automatically detect energy sensors based on device_class
    energy_sensors = [key for key, sensor_conf in sensors.items() 
                     if sensor_conf.get("device_class") == "energy"]
    current_energy_values = {}
    reset_votes = 0
    
    for sensor_key in energy_sensors:
        if sensor_key in sensors:
            try:
                index = int(sensor_key)
                if index < len(fields) and is_number(fields[index]):
                    current_value = float(fields[index])
                    current_energy_values[sensor_key] = current_value
                    
                    # Check for reset (current value much smaller than previous)
                    if sensor_key in last_values:
                        if current_value < last_values[sensor_key] * 0.5:  # 50% threshold
                            reset_votes += 1
                            logger.debug(f"Reset vote from sensor {sensor_key}: {last_values[sensor_key]} -> {current_value}")
            except (ValueError, IndexError):
                continue

    # Majority vote: if 3 or more energy sensors show reset, it's a reset
    reset_timestamp = None
    if reset_votes >= 3 and not reset_detected_today:
        reset_detected_today = True
        last_reset_time = current_time
        reset_timestamp = current_time.isoformat()
        logger.info(f"RESET DETECTED at {reset_timestamp} - {reset_votes} sensors voted for reset")
        
        # For sensor 15, add last raw value to cumulative total before reset
        if "15" in last_raw_values:
            if "15" not in cumulative_totals:
                cumulative_totals["15"] = 0
            cumulative_totals["15"] += last_raw_values["15"]
            logger.info(f"Added {last_raw_values['15']} Wh to cumulative total for sensor 15, new total: {cumulative_totals['15']} Wh")
            # Save to file immediately after update
            save_cumulative_data()

    # Update last_values and last_raw_values with current values
    last_values.update(current_energy_values)
    last_raw_values.update(current_energy_values)

    # Now process and publish all sensors
    for sensor_key in sorted(sensors, key=lambda x: int(x)):
        sensor_conf = sensors[sensor_key]
        sensor_name = sensor_conf.get("name")
        if not sensor_name:
            continue

        try:
            index = int(sensor_key)
        except ValueError:
            logger.warning("Invalid sensor key: %s", sensor_key)
            continue

        if index < len(fields):
            value = fields[index]
            sensor_state_topic = f"{state_prefix}/{sensor_key}/state"
            
            if is_number(value):
                # Apply transformations
                if index == 13:
                    value = str(-int(value))
                if index == 18:
                    value = f'{100.0 * int(value) / 255.0:.2f}'
                
                # Special handling for sensor 15 (cumulative tracking)
                if sensor_key == "15" and sensor_key in energy_sensors:
                    raw_value = float(value)
                    
                    # Initialize cumulative total if not exists
                    if "15" not in cumulative_totals:
                        cumulative_totals["15"] = 0
                    
                    # Calculate cumulative value (cumulative total + current raw value)
                    cumulative_value = cumulative_totals["15"] + raw_value
                    
                    mqtt_client.publish(sensor_state_topic, payload=str(cumulative_value))
                    logger.debug("Published cumulative energy sensor '%s' (field %s): %s Wh to topic '%s'",
                                sensor_name, sensor_key, cumulative_value, sensor_state_topic)
                # For other energy sensors, send JSON with state and optionally last_reset
                elif sensor_key in energy_sensors:
                    value_to_send = float(value) if index != 13 else int(value)
                    mqtt_client.publish(sensor_state_topic, payload=str(value_to_send))
                    logger.debug("Published energy sensor '%s' (field %s): %s to topic '%s'",
                                sensor_name, sensor_key, value_to_send, sensor_state_topic)
                else:
                    # For non-energy sensors, send plain value
                    mqtt_client.publish(sensor_state_topic, payload=value)
                    logger.debug("Published sensor '%s' (field %s): %s to topic '%s'",
                                sensor_name, sensor_key, value, sensor_state_topic)
            else:
                logger.warning(f'field {index} ("{value}") in the serial data line "{line}" is not a number')
                return
        else:
            logger.warning("Field index %d not found in data: %s", index, fields)

def main():
    # Load cumulative data first, before anything else
    load_cumulative_data()
    
    config = load_config()
    loglevel = getattr(logging, config.get("loglevel", 'INFO').upper(), logging.INFO)
    # Get the root logger and set the log level
    root_logger = logging.getLogger()
    root_logger.setLevel(loglevel)

    mqtt_client = connect_mqtt(config)
    mqtt_client.loop_start()

    while mqtt_client.is_connected() == False:
        time.sleep(0.5)
        
    publish_discovery(mqtt_client, config)

    ser = open_serial_port(config)
    nodata_timeout = config.get('serial', {}).get('data_timeout', None)
    timestamp_of_last_data = datetime.now()
    state_no_data = False
    try:
        while True:
            try:
                if ser.in_waiting:
                    try:
                        line = ser.readline().decode("utf-8")
                    except UnicodeDecodeError as e:
                        logger.error("Error decoding serial data: %s", e)
                        continue
                    
                    if state_no_data:
                        dt = (datetime.now() - timestamp_of_last_data).total_seconds()
                        logger.info(f'Receiving data again after {dt:.1f} seconds')
                    
                    timestamp_of_last_data = datetime.now()
                    state_no_data = False
                    process_line(line, config, mqtt_client)
                else:
                    if state_no_data == False:
                        if nodata_timeout:
                            dt = (datetime.now() - timestamp_of_last_data).total_seconds()
                            if dt > nodata_timeout:
                                state_no_data = True
                                logger.warning(f'Received no data for more than {dt:.1f} seconds')
                    time.sleep(0.1)
            except serial.SerialException as e:
                logger.warning('Error accessing serial port - reopening it and trying again ...')
                ser.close()
                ser = open_serial_port(config)
                timestamp_of_last_data = datetime.now()
                state_no_data = False
                continue
    except KeyboardInterrupt:
        logger.info("Received shutdown signal. Exiting...")
    finally:
        # Save cumulative data one final time before exit
        save_cumulative_data()
        ser.close()
        mqtt_client.loop_stop()
        mqtt_client.disconnect()

if __name__ == "__main__":
    main()