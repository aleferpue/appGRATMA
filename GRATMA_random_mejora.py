"""
GRATMA_random_mejorado.py — medida I-V del GRATMA por terminal serie,
midiendo los sensores en orden aleatorio.

Mejoras añadidas:
  1. El puerto COM, el wafer y el chip se introducen desde la terminal.
  2. También pueden indicarse como argumentos:

       python GRATMA_random_mejorado.py --port COM8 --wafer USAGRAPH1 --chip F5C9

     Si falta alguno, el programa lo pregunta de forma interactiva.
  3. Cada TXT final se guarda con el formato:

       Wafer_Chip_ArrayN_random_Secuencia_Electrolito.txt

     Ejemplo:

       USAGRAPH1_F5C9_Array3_random_2_PB-S0_01.txt

  4. Todos los TXT generados incluyen una cabecera con los parámetros usados
     en la medida, seguida de las columnas:

       Vfg;Id;Ig;Is

El resto del funcionamiento se mantiene: orden aleatorio, tierra en sensores
no seleccionados, estabilización inicial, espera entre sensores y separación
de las repeticiones en archivos individuales.
"""

import argparse
from datetime import datetime
import os
import random
import re
import time

import serial


FOLDER_PATH = (
    r"C:\Users\rodri\OneDrive\Escritorio\GRATMA\Medidas_GRATMA"   # Se cambia con respecto al PC que lo use.
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
# Configuración desde terminal
# -----------------------------------------------------------------
def parse_arguments():
    """Lee argumentos opcionales introducidos al ejecutar el programa."""
    parser = argparse.ArgumentParser(
        description="Medida I-V aleatoria con GRATMA."
    )
    parser.add_argument(
        "--port",
        help="Puerto serie.",
    )
    parser.add_argument(
        "--wafer",
        help="Nombre del wafer.",
    )
    parser.add_argument(
        "--chip",
        help="Código o nombre completo del chip.",
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


def choose_com_port(argument_port=None):
    """Obtiene el puerto desde --port o lo solicita directamente."""
    if argument_port:
        return argument_port.strip().upper()

    return prompt_required("Puerto COM").upper()


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


def get_runtime_configuration(args):
    """Obtiene los datos variables de la medida desde argumentos o terminal."""
    print("\n" + "=" * 62)
    print("CONFIGURACIÓN DE LA MEDIDA GRATMA")
    print("=" * 62)

    port = choose_com_port(args.port)

    wafer = (
        sanitize_filename_component(args.wafer)
        if args.wafer
        else sanitize_filename_component(prompt_required("Nombre del wafer"))
    )

    chip_input = (
        args.chip.strip()
        if args.chip
        else prompt_required("Código del chip")
    )
    chip = normalize_chip_name(wafer, chip_input)

    folder_path = os.path.expandvars(
        os.path.expanduser(args.folder if args.folder else FOLDER_PATH)
    )

    return {
        "port": port,
        "wafer": wafer,
        "chip": chip,
        "folder_path": folder_path,
    }


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
):
    """Construye los parámetros que se escribirán al comienzo de cada TXT."""
    return {
        "fecha_hora_inicio": datetime.now().isoformat(timespec="seconds"),
        "puerto": port,
        "baudrate": BAUDRATE,
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
# Funciones de medida
# -----------------------------------------------------------------
def sensor_bitmask(sensor):
    """Sensor 1..8 -> máscara de bit que espera el comando 'iv'."""
    return 1 << (sensor - 1)


def send_cmd(ser, cmd, wait=0.4, verbose=True):
    """Envía un comando y muestra la respuesta del dispositivo."""
    if not cmd.endswith("\n"):
        cmd += "\n"

    try:
        ser.reset_input_buffer()
    except Exception:
        pass

    ser.write(cmd.encode())
    if verbose:
        print(f"    [CMD] {cmd.strip()}")

    time.sleep(wait)
    replies = []
    start_time = time.time()

    while time.time() - start_time < wait + 0.8:
        line = ser.readline().decode(errors="ignore").strip()
        if line:
            replies.append(line)
            if verbose:
                print(f"      · {line}")
        else:
            break

    return replies


def countdown_sleep(seconds, label=""):
    """Espera mostrando una cuenta atrás para indicar que sigue ejecutándose."""
    seconds = int(round(seconds))
    step = 30 if seconds > 60 else 5
    remaining = seconds

    while remaining > 0:
        if remaining % step == 0 or remaining <= 5:
            print(f"      ... {label}{remaining}s restantes")
        time.sleep(1)
        remaining -= 1


def random_sequence_order(sensors):
    """Genera un orden aleatorio alternando sensores 1-4 y sensores 5-8."""
    top = [sensor for sensor in sensors if sensor <= 4]
    bottom = [sensor for sensor in sensors if sensor >= 5]
    random.shuffle(top)
    random.shuffle(bottom)

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
    port,
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
    ser=None,
    verbose=True,
):
    """Envía el comando IV y guarda la respuesta completa en un TXT."""
    own_connection = ser is None

    if own_connection:
        ser = serial.Serial(port, BAUDRATE, timeout=SERIAL_TIMEOUT_S)
        time.sleep(2)

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
        print(
            f"    [CMD] {iv_command.strip()} "
            f"  (bitmask sensor={value})"
        )

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
                        print(
                            f"      ... {number_of_points} puntos "
                            f"(Vfg≈{vg:.4g})"
                        )
                elif verbose:
                    print(f"      · {line}")
            elif time.time() - last_data_time > timeout:
                if verbose:
                    print("      [WARN] timeout esperando datos — corto la lectura")
                break

            if line == "(GRATMA) Measurement sweep completed":
                break

    if own_connection:
        ser.close()

    if verbose:
        extra = ""
        if max_vg is not None:
            extra = f" | Vfg_max={max_vg:.4g}"
            if dirac_vg is not None:
                extra += f" | min|Id| en Vfg={dirac_vg:.4g}"
        print(
            f"    -> {number_of_points} puntos guardados "
            f"en {output_file}{extra}"
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
            print(
                "    [WARN] Se ha encontrado más de un bloque de datos; "
                "solo se conserva el primero."
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
        print(f"    Guardado: {output_filename}")

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
# Programa principal
# -----------------------------------------------------------------
def main(port, wafer, chip, folder_path):
    """Ejecuta todas las secuencias de medida."""
    sensors = list(NSENSOR)

    print("\n" + "=" * 62)
    print(f"GRATMA I-V ALEATORIO — chip {chip}")
    print(f"Wafer: {wafer}")
    print(f"Puerto: {port}")
    print(f"Sensores: {sensors} | Secuencias: {NUM_REP}")
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

    try:
        serial_connection = serial.Serial(
            port,
            BAUDRATE,
            timeout=SERIAL_TIMEOUT_S,
        )
    except serial.SerialException as error:
        print(f"\n[ERROR] No se ha podido abrir el puerto {port}: {error}")
        print("Comprueba el puerto seleccionado y que no esté siendo usado.")
        return

    time.sleep(2)

    try:
        if GND_UNSELECTED:
            print(
                "[SETUP] Activando tierra en los sensores "
                "no medidos (um 1) ..."
            )
            send_cmd(serial_connection, "um 1")

        print(
            f"[ESPERA] Estabilizando {STABILIZE_S}s "
            f"({STABILIZE_S / 60:.1f} min) antes de empezar ..."
        )
        countdown_sleep(STABILIZE_S)

        first_measurement = True

        for sequence in range(1, NUM_REP + 1):
            order = random_sequence_order(sensors)
            print("\n" + "#" * 62)
            print(
                f"# Secuencia {sequence}/{NUM_REP} "
                f"— orden aleatorio: {order}"
            )
            print("#" * 62)

            for sensor in order:
                if not first_measurement:
                    print(
                        f"[ESPERA] {BETWEEN_SENSORS_S}s "
                        f"antes de pasar a S{sensor} ..."
                    )
                    countdown_sleep(BETWEEN_SENSORS_S)
                first_measurement = False

                grounded = [value for value in range(1, 9) if value != sensor]
                grounded_text = ", ".join(f"S{value}" for value in grounded)
                print(
                    f"\n>>> [seq {sequence}/{NUM_REP}] Midiendo S{sensor} "
                    f"(a tierra: {grounded_text})"
                )

                metadata = build_measurement_metadata(
                    port=port,
                    wafer=wafer,
                    chip=chip,
                    sensor=sensor,
                    sequence=sequence,
                    random_order=order,
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
                    port=port,
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
                    ser=serial_connection,
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
                    )

                    if saved_filename is not None:
                        temporary_txt_path = os.path.join(
                            folder_path,
                            temporary_txt_filename,
                        )
                        os.remove(temporary_txt_path)
                except Exception as error:
                    print(f"    [WARN] split_txt_by_reps falló: {error}")
                    print(
                        "    El TXT temporal se conserva para poder "
                        f"recuperar los datos: {temporary_txt_filename}"
                    )

    finally:
        serial_connection.close()

    print("\n\033[1mFinish\033[0m")


if __name__ == "__main__":
    command_line_arguments = parse_arguments()
    runtime_configuration = get_runtime_configuration(command_line_arguments)
    main(**runtime_configuration)
