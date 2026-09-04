# GRATMA Desktop App v2.0.1


Python desktop application (Tkinter + Matplotlib) for controlling and measuring the GRATMA device (I-V characterisation of GFET sensors) via the Vendor USB interface.

## Table of Contents
- [Installed Tools](#installed-tools)
  - [Launch the app](#launch-the-app)
- [How to Use](#how-to-use)
  - [1. Connect device](#1-connect-device)
  - [2. Configure the output (Measurements tab)](#2-configure-the-output-measurements-tab)
  - [3. Select sensors and mode](#3-select-sensors-and-mode)
  - [4. Choose measurement type and parameters](#4-choose-measurement-type-and-parameters)
  - [5. Manual control of GRATMA (GRATMA tab)](#5-manual-control-of-gratma-gratma-tab)
  - [6. Export](#6-export)
- [CSV Format](#csv-format)
- [Troubleshooting](#troubleshooting)


## Installed Tools


To run the software you need to install these libraries:

- Matplotlib
- Libusb
- CSV
- Os
- Queue
- Threading
- Time
- Datetime
- Tkinter
- Usb.core



### Launch the app

Open a terminal in the same folder as your code is and run the following command:

```bash
python app_v2.py
```


## How to use

### 1. Connect device

1.  Click **⟳** (scan) → select GRATMA from the drop-down menu → **Connect**.
2. Check: Green PCB LED means that it is connected;

### 2. Configure the output (Measurements tab)

Under **Identification / Output**:
- **Sample name**: file prefix (e.g. `BSN035_F4C7`).
- **Extra**: optional free text added to the end of the name.
- **Folder**: destination for the CSV files (button `…`). If defined, the files are saved automatically upon completion of each measurement.

### 3. Select sensors and mode

In **Sensors**: select the sensors to be measured (1–8) and choose:
- Sequential: one sensor after another.
- Parallel: all at once, a combined curve.

### 4. Choose measurement type and parameters

#### Parametric I-V
- Parameters: 
  - VD, drain voltage (mV)
  - VG start, gate voltage init (mV)
  - VG end, gate voltage end (mV)
  - VG step, gate voltage sweep (mV), is the value that determines the sequential increment in the measurement
  - Repetitions, number of repetitions
  - Reverse sweep, only forward or forward and backward

#### I vs Time (IDT)
- Parameters: 
  - VG,  gate voltage (mV)
  - VD,  drain voltage (mV)
  - Duration, total time in seconds
  - Period (s), seconds between samples

#### Two-Point Differential
- Uses the same configured **I-V parameters**, in two phases:
  1. **Phase 1** (baseline, without sample).
  2. Physically add the sample and confirm in the dialogue box.
  3. **Phase 2** (with sample).
- Result: **ΔVG = VG_min2 − VG_min1**. CSV per phase (`diff-f1` / `diff-f2`).

Click **▶ Init** to start the process and click **⬛ STOP** to finalize it.

### 5. Manual control of GRATMA (GRATMA tab)

- **High-Level**: Set VD / Set VG in mV (the firmware calculates the DAC values).
- **Raw DAC**: Set VD (1) / Set VG (0), channel, mV → Set Voltage.
- **Switches**: (0/1) + hex map.
- **INA228 readings**: manual (“↺ Read All”) or continuous with interval.

### 6. Export

If a folder is configured, the CSV files are saved automatically upon completion. The **Export CSV** button saves them again (it asks for a folder if there isn’t one).



## CSV format

Name: `<sample>_<sensor>_<measure_type>_<rep>_<extra>.csv`

Columns:
- **I-V / Differential**: `Point, Direction, VG, ID, VD, IG`
- **ID-T**: `Time_ms, Time_s, Vbus, ID, VG, IG`



## Troubleshooting

- **Device not detected**: check USB cable; install WinUSB on Interface 2 (Zadig); try `python -c ‘from gratma_usb import GratmaUSB; print(GratmaUSB.scan())’`.
- **Ping-Pong inactive (orange LED)**: update firmware to v2.0.1.
- **‘Pipe error (errno=32)’ error**: disconnect/reconnect USB and restart the app.
- **Corrupt data / parsing error**: ensure you are using **the same version of firmware and app** (the record is 23 bytes in v2.0.1; mixing with older 14-byte firmware will fail).


---

**Versión**: 2.0.1  
**Compatible con**: Firmware GRATMA v2.0.1
