"""
GRATMA_multipuerto.py — medida I-V del GRATMA en VARIOS puertos serie a la vez.

Cambios respecto a GRATMA_random_mejorado.py:

  1. Se pueden configurar N equipos (puerto + wafer + chip). Cada equipo se mide
     en su propio hilo, de forma que todos los puertos avanzan en paralelo.

  2. Configuración por argumentos, repitiendo --device:

       python GRATMA_multipuerto.py --device COM8:USAGRAPH1:F5C9 \
                                    --device COM9:USAGRAPH1:F5C10

     También se admite el formato antiguo de un solo equipo:

       python GRATMA_multipuerto.py --port COM8 --wafer USAGRAPH1 --chip F5C9

     Si no se pasa nada, el programa pregunta cuántos equipos hay y pide los
     datos de cada uno por terminal.

  3. La estabilización inicial se hace UNA sola vez: se abren todos los puertos,
     se envía "um 1" a cada equipo y se espera STABILIZE_S antes de lanzar los
     hilos, de modo que todos empiezan a barrer a la vez.

  4. Cada línea de la consola va etiquetada con el puerto, [COM8], [COM9], ...
     para poder seguir varias medidas simultáneas.

  5. Los nombres de archivo no cambian:

       Wafer_Chip_ArrayN_random_Secuencia_Electrolito.txt

     Como el nombre incluye wafer y chip, dos equipos distintos no colisionan.
     El programa aborta si se repite el par wafer+chip o el mismo puerto.

  6. La cabecera de cada TXT incluye además los puertos que estaban midiendo
     en paralelo, para poder rastrear las medidas simultáneas.
"""

import argparse
import os
import random
import re
import threading
import time
from datetime import datetime

import serial


FOLDER_PATH = (
    r"C:\GRATMA\medidas\APS5"   # Se cambia con respecto al PC que lo use.
)

# ==================== Información de sensores ====================
NSENSOR = [1, 2, 3, 4, 5, 6, 7, 8]

# ==================== Parámetros de medida ====================
VD = 50          # Drain voltage (mV)
VGINIT = 0       # Vg inicial (mV)
VGEND = 1200     # Vg final (mV)
VGSWEEP = 15     # Paso de Vg (mV)
FBWD = 1         # 0: solo forward | 1: forward + backward
NUM_REP = 5      # Secuencias sobre todos los sensores

# ==================== Tiempos y modo ====================
STABILIZE_S = 180
BETWEEN_SENSORS_S = 10
GND_UNSELECTED = True

# ==================== Identificación de los archivos ====================
MEASUREMENT_MODE = "random"
ELECTROLYTE = "PB-S0_01"
BAUDRATE = 115200
SERIAL_TIMEOUT_S = 1
MEASUREMENT_TIMEOUT_S = 300


# -----------------------------------------------------------------
# Consola compartida entre hilos
# -----------------------------------------------------------------
PRINT_LOCK = threading.Lock()


def log(message="", tag=None):
    """Imprime de forma segura desde varios hilos, etiquetando por puerto."""
    text = str(message)
    with PRINT_LOCK:
        if tag is None:
            print(text)
        else:
            for line in text.split("\n"):
                print(f"[{tag}] {line}")


# -----------------------------------------------------------------
# Configuración desde terminal
# -----------------------------------------------------------------
def parse_arguments():
    """Lee argumentos opcionales introducidos al ejecutar el programa."""
    parser = argparse.ArgumentParser(
        description="Medida I-V aleatoria con varios GRATMA en paralelo.",
    )
    parser.add_argument(
        "--device",
        action="append",
        default=[],
        metavar="PUERTO:WAFER:CHIP",
        help=(
            "Equipo a medir. Se puede repetir para añadir más puertos. "
            "Ejemplo: --device COM8:USAGRAPH1:F5C9"
        ),
    )
    parser.add_argument(
        "--port",
        help="Puerto serie (modo de un solo equipo).",
    )
    parser.add_argument(
        "--wafer",
        help="Nombre del wafer (modo de un solo equipo).",
    )
    parser.add_argument(
        "--chip",
        help="Código o nombre completo del chip (modo de un solo equipo).",
    )
    parser.add_argument(
        "--folder",
        help="Carpeta de salida. Si se omite se usa la definida en el código.",
    )
    return parser.parse_args()


def prompt_required(message):
    """Solicita un valor obligatorio sin mostrar ejemplos ni valores por defecto."""
    while True:
        try:
            value = input(f"{message}: ").strip()
        except EOFError as exc:
            raise RuntimeError(
                f"No se ha podido leer el valor obligatorio: {message}."
            ) from exc

        if value:
            return value

        print("  Este campo no puede quedar vacío.")


def normalize_port(port):
    """Deja los COM en mayúsculas y respeta rutas tipo /dev/ttyUSB0."""
    port = port.strip()
    if re.fullmatch(r"com\d+", port, flags=re.IGNORECASE):
        return port.upper()
    return port


def sanitize_filename_component(value):
    """Evita caracteres no válidos en nombres de archivo de Windows."""
    value = value.strip()
    return re.sub(r'[<>:"/\\|?*]+', "_", value)


def normalize_chip_name(wafer, chip_input):
    """Devuelve solo el chip y elimina el wafer si se escribió como prefijo."""
    wafer = sanitize_filename_component(wafer)
    chip_input = sanitize_filename_component(chip_input)

    wafer_prefix = f"{wafer}_"
    if chip_input.upper().startswith(wafer_prefix.upper()):
        return chip_input[len(wafer_prefix):]
    return chip_input


def build_device(port, wafer, chip_input):
    """Crea el diccionario de un equipo con los nombres ya normalizados."""
    wafer = sanitize_filename_component(wafer)
    return {
        "port": normalize_port(port),
        "wafer": wafer,
        "chip": normalize_chip_name(wafer, chip_input),
        "serial": None,
        "saved_files": 0,
        "error": None,
    }


def parse_device_argument(text):
    """Interpreta 'PUERTO:WAFER:CHIP' (también admite , o ; como separador)."""
    parts = [part.strip() for part in re.split(r"[:;,]", text) if part.strip()]
    if len(parts) != 3:
        raise ValueError(
            f"Formato no válido en --device '{text}'. "
            "Se espera PUERTO:WAFER:CHIP, por ejemplo COM8:USAGRAPH1:F5C9."
        )
    return build_device(*parts)


def prompt_devices():
    """Pregunta por terminal cuántos equipos hay y los datos de cada uno."""
    while True:
        raw_number = prompt_required("Número de equipos a medir en paralelo")
        if raw_number.isdigit() and int(raw_number) >= 1:
            number_of_devices = int(raw_number)
            break
        print("  Introduce un número entero mayor o igual que 1.")

    devices = []
    for index in range(1, number_of_devices + 1):
        print(f"\n--- Equipo {index}/{number_of_devices} ---")
        port = prompt_required("Puerto COM")
        wafer = prompt_required("Nombre del wafer")
        chip = prompt_required("Código del chip")
        devices.append(build_device(port, wafer, chip))

    return devices


def validate_devices(devices):
    """Comprueba que no se repiten puertos ni combinaciones wafer+chip."""
    if not devices:
        raise RuntimeError("No se ha configurado ningún equipo.")

    seen_ports = set()
    seen_chips = set()

    for device in devices:
        port = device["port"]
        chip_key = (device["wafer"].upper(), device["chip"].upper())

        if port in seen_ports:
            raise RuntimeError(f"El puerto {port} está repetido.")
        if chip_key in seen_chips:
            raise RuntimeError(
                f"El par wafer+chip {device['wafer']}_{device['chip']} está "
                "repetido: los archivos de salida se sobrescribirían."
            )

        seen_ports.add(port)
        seen_chips.add(chip_key)


def get_runtime_configuration(args):
    """Obtiene los equipos y la carpeta de salida desde argumentos o terminal."""
    print("\n" + "=" * 62)
    print("CONFIGURACIÓN DE LA MEDIDA GRATMA (MULTIPUERTO)")
    print("=" * 62)

    devices = [parse_device_argument(text) for text in args.device]

    # Modo antiguo de un solo equipo: --port / --wafer / --chip.
    if args.port or args.wafer or args.chip:
        port = args.port if args.port else prompt_required("Puerto COM")
        wafer = args.wafer if args.wafer else prompt_required("Nombre del wafer")
        chip = args.chip if args.chip else prompt_required("Código del chip")
        devices.append(build_device(port, wafer, chip))

    if not devices:
        devices = prompt_devices()

    validate_devices(devices)

    folder_path = os.path.expandvars(
        os.path.expanduser(args.folder if args.folder else FOLDER_PATH)
    )

    return {"devices": devices, "folder_path": folder_path}


# -----------------------------------------------------------------
# Cabeceras de los TXT
# -----------------------------------------------------------------
def build_measurement_metadata(
    *,
    port,
    wafer,
    chip,
    sensor,
    sequence,
    random_order,
    parallel_ports,
):
    """Construye los parámetros que se escribirán al comienzo de cada TXT."""
    return {
        "fecha_hora_inicio": datetime.now().isoformat(timespec="seconds"),
        "puerto": port,
        "baudrate": BAUDRATE,
        "equipos_en_paralelo": len(parallel_ports),
        "puertos_en_paralelo": ",".join(parallel_ports),
        "wafer": wafer,
        "chip": chip,
        "sensor": sensor,
        "array": f"Array{sensor}",
        "secuencia": sequence,
        "numero_secuencias_total": NUM_REP,
        "orden_aleatorio_secuencia": ",".join(map(str, random_order)),
        "modo_medida": MEASUREMENT_MODE,
        "electrolito": ELECTROLYTE,
        "VD_mV": VD,
        "VGINIT_mV": VGINIT,
        "VGEND_mV": VGEND,
        "VGSWEEP_mV": VGSWEEP,
        "FBWD": FBWD,
        "modo_barrido": "forward_backward" if FBWD == 1 else "forward",
        "estabilizacion_inicial_s": STABILIZE_S,
        "espera_entre_sensores_s": BETWEEN_SENSORS_S,
        "sensores_no_seleccionados_a_tierra": GND_UNSELECTED,
    }


def metadata_to_lines(metadata):
    """Convierte un diccionario de parámetros en comentarios legibles."""
    lines = ["# PARAMETROS_INICIALES_GRATMA"]
    for key, value in metadata.items():
        lines.append(f"# {key}={value}")
    return lines


def write_metadata(file_object, metadata):
    """Escribe la cabecera de parámetros en un archivo ya abierto."""
    if not metadata:
        return
    file_object.write("\n".join(metadata_to_lines(metadata)))
    file_object.write("\n\n")


# -----------------------------------------------------------------
# Nombres de archivo
# -----------------------------------------------------------------
def build_measurement_filename(
    wafer,
    chip,
    sensor,
    sequence,
    electrolyte=ELECTROLYTE,
):
    """Construye el nombre definitivo del TXT de una medida."""
    wafer = sanitize_filename_component(wafer)
    chip = sanitize_filename_component(chip)
    electrolyte = sanitize_filename_component(electrolyte)

    return (
        f"{wafer}_{chip}_Array{sensor}_{MEASUREMENT_MODE}_"
        f"{sequence}_{electrolyte}.txt"
    )


def build_temporary_txt_filename(final_filename):
    """Crea un TXT temporal para conservar la salida serie completa."""
    filename_without_extension = os.path.splitext(final_filename)[0]
    return f"All_info_{filename_without_extension}.txt"


# -----------------------------------------------------------------
# Funciones de medida
# -----------------------------------------------------------------
def sensor_bitmask(sensor):
    """Sensor 1..8 -> máscara de bit que espera el comando 'iv'."""
    return 1 << (sensor - 1)


def send_cmd(ser, cmd, wait=0.4, tag=None, verbose=True):
    """Envía un comando y muestra la respuesta del dispositivo."""
    if not cmd.endswith("\n"):
        cmd += "\n"

    try:
        ser.reset_input_buffer()
    except Exception:
        pass

    ser.write(cmd.encode())
    if verbose:
        log(f"    [CMD] {cmd.strip()}", tag)

    time.sleep(wait)
    replies = []
    start_time = time.time()

    while time.time() - start_time < wait + 0.8:
        line = ser.readline().decode(errors="ignore").strip()
        if line:
            replies.append(line)
            if verbose:
                log(f"      · {line}", tag)
        else:
            break

    return replies


def countdown_sleep(seconds, label="", tag=None):
    """Espera mostrando una cuenta atrás para indicar que sigue ejecutándose."""
    seconds = int(round(seconds))
    step = 30 if seconds > 60 else 5
    remaining = seconds

    while remaining > 0:
        if remaining % step == 0 or remaining <= 5:
            log(f"      ... {label}{remaining}s restantes", tag)
        time.sleep(1)
        remaining -= 1


def random_sequence_order(sensors, rng=random):
    """Genera un orden aleatorio alternando sensores 1-4 y sensores 5-8."""
    top = [sensor for sensor in sensors if sensor <= 4]
    bottom = [sensor for sensor in sensors if sensor >= 5]
    rng.shuffle(top)
    rng.shuffle(bottom)

    order = []
    top_index = 0
    bottom_index = 0
    take_top = True

    while top_index < len(top) or bottom_index < len(bottom):
        if take_top and top_index < len(top):
            order.append(top[top_index])
            top_index += 1
        elif not take_top and bottom_index < len(bottom):
            order.append(bottom[bottom_index])
            bottom_index += 1
        elif top_index < len(top):
            order.append(top[top_index])
            top_index += 1
        elif bottom_index < len(bottom):
            order.append(bottom[bottom_index])
            bottom_index += 1
        take_top = not take_top

    return order


def read_serial_to_file(
    ser,
    vd,
    vginit,
    vgend,
    vgsweep,
    sensor,
    fbwd,
    rep,
    output_file,
    timeout,
    folder_path,
    metadata=None,
    tag=None,
    verbose=True,
):
    """Envía el comando IV y guarda la respuesta completa en un TXT."""
    value = sensor_bitmask(sensor)
    iv_command = (
        f"iv {vd} {vginit} {vgend} {vgsweep} "
        f"{value} {fbwd} {rep}\n"
    )

    try:
        ser.reset_input_buffer()
    except Exception:
        pass

    ser.write(iv_command.encode())
    if verbose:
        log(f"    [CMD] {iv_command.strip()}   (bitmask sensor={value})", tag)

    current_file = os.path.join(folder_path, output_file)

    def parse_point(line_text):
        columns = line_text.split(";")
        if len(columns) != 4:
            return None
        try:
            return float(columns[0]), float(columns[1])
        except ValueError:
            return None

    number_of_points = 0
    max_vg = None
    dirac_vg = None
    min_abs_id = None

    with open(current_file, "w", encoding="utf-8") as file_object:
        # La cabecera se añade también al All_info para que todos los TXT
        # conserven la configuración exacta de la medida.
        write_metadata(file_object, metadata)

        last_data_time = time.time()
        while True:
            line = ser.readline().decode(errors="ignore").strip()

            if line:
                file_object.write(line + "\n")
                last_data_time = time.time()
                point = parse_point(line)

                if point is not None:
                    number_of_points += 1
                    vg, drain_current = point

                    if max_vg is None or vg > max_vg:
                        max_vg = vg
                    if min_abs_id is None or abs(drain_current) < min_abs_id:
                        min_abs_id = abs(drain_current)
                        dirac_vg = vg

                    if verbose and number_of_points % 25 == 0:
                        log(
                            f"      ... {number_of_points} puntos "
                            f"(Vfg≈{vg:.4g})",
                            tag,
                        )
                elif verbose:
                    log(f"      · {line}", tag)
            elif time.time() - last_data_time > timeout:
                if verbose:
                    log("      [WARN] timeout esperando datos — corto la lectura", tag)
                break

            if line == "(GRATMA) Measurement sweep completed":
                break

    if verbose:
        extra = ""
        if max_vg is not None:
            extra = f" | Vfg_max={max_vg:.4g}"
            if dirac_vg is not None:
                extra += f" | min|Id| en Vfg={dirac_vg:.4g}"
        log(
            f"    -> {number_of_points} puntos guardados "
            f"en {output_file}{extra}",
            tag,
        )

    return number_of_points


def save_clean_measurement(output_path, metadata, data_buffer):
    """Guarda un TXT limpio con parámetros, columnas y datos numéricos."""
    with open(output_path, "w", encoding="utf-8") as output_file:
        write_metadata(output_file, metadata)
        output_file.write("\n".join(data_buffer) + "\n")


def split_txt_by_reps(
    wafer,
    chip,
    sensor,
    sequence,
    input_filename,
    folder_path,
    metadata=None,
    tag=None,
):
    """Extrae Vfg;Id;Ig;Is y crea el TXT definitivo de la medida."""
    os.makedirs(folder_path, exist_ok=True)

    input_path = os.path.join(folder_path, input_filename)

    with open(input_path, "r", encoding="utf-8", errors="ignore") as input_file:
        lines = input_file.readlines()

    repetition = None
    collecting = False
    data_buffer = []
    saved_filename = None

    number = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
    numeric_line_pattern = re.compile(
        rf"^{number};{number};{number};{number}$"
    )

    def save_current_block(current_repetition, current_buffer):
        nonlocal saved_filename

        if current_repetition is None or not current_buffer:
            return

        if saved_filename is not None:
            log(
                "    [WARN] Se ha encontrado más de un bloque de datos; "
                "solo se conserva el primero.",
                tag,
            )
            return

        output_filename = build_measurement_filename(
            wafer=wafer,
            chip=chip,
            sensor=sensor,
            sequence=sequence,
        )
        output_path = os.path.join(folder_path, output_filename)
        save_clean_measurement(output_path, metadata, current_buffer)
        saved_filename = output_filename
        log(f"    Guardado: {output_filename}", tag)

    for line in lines:
        stripped_line = line.strip()

        sensor_match = re.match(
            r"\(GRATMA\) Sensor (\d+) \(rep=(\d+)\)",
            stripped_line,
        )
        if sensor_match:
            sensor_found = int(sensor_match.group(1))
            repetition = int(sensor_match.group(2))
            collecting = sensor_found == sensor
            data_buffer = []
            continue

        if stripped_line == "Vfg;Id;Ig;Is" and repetition is not None:
            data_buffer = [stripped_line]
            collecting = True
            continue

        if collecting:
            if numeric_line_pattern.fullmatch(stripped_line):
                data_buffer.append(stripped_line)
            else:
                save_current_block(repetition, data_buffer)
                collecting = False
                data_buffer = []

    # Guarda el último bloque si el archivo termina justo después de los datos.
    if collecting and data_buffer:
        save_current_block(repetition, data_buffer)

    return saved_filename


# -----------------------------------------------------------------
# Hilo de medida de un equipo
# -----------------------------------------------------------------
def measure_device(device, folder_path, parallel_ports):
    """Ejecuta todas las secuencias de un equipo. Se lanza en un hilo propio."""
    tag = device["port"]
    wafer = device["wafer"]
    chip = device["chip"]
    serial_connection = device["serial"]

    # Generador propio por hilo: cada equipo tiene su propio orden aleatorio.
    rng = random.Random()
    sensors = list(NSENSOR)
    first_measurement = True

    try:
        for sequence in range(1, NUM_REP + 1):
            order = random_sequence_order(sensors, rng)
            log("#" * 50, tag)
            log(
                f"# Secuencia {sequence}/{NUM_REP} — orden aleatorio: {order}",
                tag,
            )
            log("#" * 50, tag)

            for sensor in order:
                if not first_measurement:
                    log(
                        f"[ESPERA] {BETWEEN_SENSORS_S}s antes de pasar a "
                        f"S{sensor} ...",
                        tag,
                    )
                    countdown_sleep(BETWEEN_SENSORS_S, tag=tag)
                first_measurement = False

                grounded = [value for value in range(1, 9) if value != sensor]
                grounded_text = ", ".join(f"S{value}" for value in grounded)
                log(
                    f">>> [seq {sequence}/{NUM_REP}] Midiendo S{sensor} "
                    f"(a tierra: {grounded_text})",
                    tag,
                )

                metadata = build_measurement_metadata(
                    port=device["port"],
                    wafer=wafer,
                    chip=chip,
                    sensor=sensor,
                    sequence=sequence,
                    random_order=order,
                    parallel_ports=parallel_ports,
                )

                final_filename = build_measurement_filename(
                    wafer=wafer,
                    chip=chip,
                    sensor=sensor,
                    sequence=sequence,
                )
                temporary_txt_filename = build_temporary_txt_filename(
                    final_filename
                )

                read_serial_to_file(
                    ser=serial_connection,
                    vd=VD,
                    vginit=VGINIT,
                    vgend=VGEND,
                    vgsweep=VGSWEEP,
                    sensor=sensor,
                    fbwd=FBWD,
                    rep=1,
                    output_file=temporary_txt_filename,
                    timeout=MEASUREMENT_TIMEOUT_S,
                    folder_path=folder_path,
                    metadata=metadata,
                    tag=tag,
                )

                try:
                    saved_filename = split_txt_by_reps(
                        wafer=wafer,
                        chip=chip,
                        sensor=sensor,
                        sequence=sequence,
                        input_filename=temporary_txt_filename,
                        folder_path=folder_path,
                        metadata=metadata,
                        tag=tag,
                    )

                    if saved_filename is not None:
                        device["saved_files"] += 1
                        temporary_txt_path = os.path.join(
                            folder_path,
                            temporary_txt_filename,
                        )
                        os.remove(temporary_txt_path)
                except Exception as error:
                    log(f"    [WARN] split_txt_by_reps falló: {error}", tag)
                    log(
                        "    El TXT temporal se conserva para poder "
                        f"recuperar los datos: {temporary_txt_filename}",
                        tag,
                    )

    except Exception as error:
        # Un fallo en un equipo no debe detener a los demás.
        device["error"] = error
        log(f"[ERROR] Medida interrumpida en este equipo: {error}", tag)

    log("Equipo terminado.", tag)


# -----------------------------------------------------------------
# Programa principal
# -----------------------------------------------------------------
def open_devices(devices):
    """Abre todos los puertos y devuelve solo los que han respondido."""
    opened = []

    for device in devices:
        port = device["port"]
        try:
            device["serial"] = serial.Serial(
                port,
                BAUDRATE,
                timeout=SERIAL_TIMEOUT_S,
            )
            opened.append(device)
            log(f"[SETUP] Puerto {port} abierto correctamente.")
        except serial.SerialException as error:
            log(f"[ERROR] No se ha podido abrir el puerto {port}: {error}")
            log("        Comprueba el puerto y que no esté siendo usado.")

    return opened


def main(devices, folder_path):
    """Prepara todos los equipos y lanza una medida en paralelo por puerto."""
    print("\n" + "=" * 62)
    print("GRATMA I-V ALEATORIO — MEDIDA EN PARALELO")
    print("=" * 62)
    for device in devices:
        print(
            f"  {device['port']:>12}  |  wafer {device['wafer']}  |  "
            f"chip {device['chip']}"
        )
    print("-" * 62)
    print(f"Sensores: {NSENSOR} | Secuencias: {NUM_REP}")
    print(
        f"VD={VD} | VGINIT={VGINIT} | VGEND={VGEND} | "
        f"VGSWEEP={VGSWEEP} | FBWD={FBWD}"
    )
    print(
        f"Estabilización inicial: {STABILIZE_S}s "
        f"({STABILIZE_S / 60:.1f} min)"
    )
    print(f"Espera entre sensores: {BETWEEN_SENSORS_S}s")
    print(
        "Tierra a los no medidos (um 1): "
        f"{'SÍ' if GND_UNSELECTED else 'NO'}"
    )
    print(f"Carpeta de salida: {folder_path}")
    print("=" * 62)

    os.makedirs(folder_path, exist_ok=True)

    active_devices = open_devices(devices)
    if not active_devices:
        print("\n[ERROR] No hay ningún puerto disponible. Se aborta la medida.")
        return

    parallel_ports = [device["port"] for device in active_devices]
    threads = []

    try:
        time.sleep(2)   # Margen tras abrir los puertos.

        if GND_UNSELECTED:
            print(
                "\n[SETUP] Activando tierra en los sensores no medidos "
                "(um 1) en todos los equipos ..."
            )
            for device in active_devices:
                send_cmd(device["serial"], "um 1", tag=device["port"])

        # Una sola estabilización para todos: los equipos esperan a la vez.
        print(
            f"\n[ESPERA] Estabilizando {STABILIZE_S}s "
            f"({STABILIZE_S / 60:.1f} min) antes de empezar ..."
        )
        countdown_sleep(STABILIZE_S)

        print(
            f"\n[INICIO] Lanzando {len(active_devices)} medidas en paralelo: "
            f"{', '.join(parallel_ports)}"
        )

        for device in active_devices:
            thread = threading.Thread(
                target=measure_device,
                args=(device, folder_path, parallel_ports),
                name=f"GRATMA-{device['port']}",
                daemon=True,
            )
            thread.start()
            threads.append(thread)

        for thread in threads:
            thread.join()

    except KeyboardInterrupt:
        print("\n[AVISO] Interrupción por teclado: esperando a que los hilos "
              "terminen la medida en curso ...")
        for thread in threads:
            thread.join()

    finally:
        for device in active_devices:
            if device["serial"] is not None:
                try:
                    device["serial"].close()
                except Exception:
                    pass

    print("\n" + "=" * 62)
    print("RESUMEN")
    print("=" * 62)
    for device in active_devices:
        status = "OK" if device["error"] is None else f"ERROR: {device['error']}"
        print(
            f"  {device['port']:>12}  |  {device['wafer']}_{device['chip']}  |  "
            f"{device['saved_files']} archivos  |  {status}"
        )

    print("\n\033[1mFinish\033[0m")


if __name__ == "__main__":
    command_line_arguments = parse_arguments()
    runtime_configuration = get_runtime_configuration(command_line_arguments)
    main(**runtime_configuration)
