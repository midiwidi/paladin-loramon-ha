# paladin-loramon-ha - <br>A Paladin LoRa Monitor to Home Assistant Bridge

## Overview

`paladin-loramon-ha` reads and processes data from a Paladin LoRa Monitor connected via a serial port and publishes the parsed values to Home Assistant using MQTT. This allows real-time monitoring and visualization of power, energy and temperature data from a Paladin Solar Diverter in Home Assistant. No configuration is necessary in Home Assistant as the device and all entities are created automatically via MQTT.

## Installation

Clone the repository and install dependencies using `pipenv`:

```sh
git clone https://github.com/midiwidi/paladin-loramon-ha.git
cd paladin-loramon-ha
pipenv install
```

## Configuration

Edit the `config.yaml` file to specify connection settings for MQTT, the serial port, and sensor mappings.

### General Settings

- `loglevel`: Controls how verbose the logs are, letting you filter messages by importance (e.g., `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`).

### MQTT Settings

- `host`: The hostname or IP address of the MQTT broker (should be set by the user).
- `port`: The MQTT port (default: `1883`, usually left as is).
- `username` / `password`: MQTT credentials if required, otherwise leave these commented out.
- `discovery_prefix`: The MQTT topic prefix for Home Assistant discovery (default: `homeassistant`, should not be changed unless necessary).
- `state_prefix`: The MQTT topic where sensor states will be published (should be set by the user).
- `device_id`: The identifier used for Home Assistant device discovery (default: `paladin`, usually left as is).

### Serial Port Settings

- `port`: The serial port device (e.g., `/dev/ttyUSB0` on Linux, `COM3` on Windows, must be set by the user).
- `baud_rate`: The baud rate for serial communication (default: `57600`, usually left as is).

### Device Information

- `name`: Display name of the device in Home Assistant.
- `manufacturer`: Manufacturer of the device.
- `model`: Model identifier.

### Sensors

Define individual sensors mapped to the data fields received from the LoRa Monitor.

- Each key corresponds to the index of the value in the serial input.
- `"0":` Sensor at index `0`, with attributes like `name`, `unit_of_measurement`, and `device_class`.
- To disable a sensor from being published to Home Assistant, comment it out or remove the entry.

#### Example:

```yaml
sensors:
  "0":
    name: "Mains Power"
    unit_of_measurement: "W"
    device_class: "power"
  # "1":
  #   name: "Ignored Sensor"
  #   unit_of_measurement: "W"
  #   device_class: "power"
  "2":
    name: "Solar Power"
    unit_of_measurement: "W"
    device_class: "power"
```

## Running the Script

To start `paladin-loramon-ha` manually:

```sh
pipenv run python paladin-loramon-ha.py
```

## Running as a Systemd Service on Linux

### Example Systemd Service File

Create `/etc/systemd/system/paladin-loramon-ha.service`:

```ini
[Unit]
Description=Paladin LoRa Monitor to Home Assistant Bridge
After=network.target

[Service]
ExecStart=/usr/bin/pipenv run python /path/to/paladin-loramon-ha.py
WorkingDirectory=/path/to/paladin-loramon-ha
Restart=always
User=your_user
Group=your_group

[Install]
WantedBy=multi-user.target
```

### Enable and Start the Service

```sh
sudo systemctl daemon-reload
sudo systemctl enable paladin-loramon-ha
sudo systemctl start paladin-loramon-ha
```

### Viewing Logs

To check logs, use:

```sh
journalctl -u paladin-loramon-ha -f
```