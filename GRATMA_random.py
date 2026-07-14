"""
GRATMA_IV_random.py — medida I-V del GRATMA por terminal serie (sin app),
midiendo los sensores en ORDEN ALEATORIO igual que la aplicación.

Qué hace (como el modo I-V Randomised de la app):
  1. Abre el puerto serie y activa 'um 1' → el firmware pone a TIERRA los
     sensores que NO se están midiendo en cada cambio de sensor.
  2. Espera STABILIZE_S (3 min por defecto) para estabilizar el sistema.
  3. Repite NUM_REP secuencias (5 por defecto). En cada secuencia mide TODOS
     los sensores en un orden aleatorio nuevo (alternando el grupo superior
     1-4 y el inferior 5-8, como la app). Entre sensor y sensor espera
     BETWEEN_SENSORS_S (10 s).
  4. Va sacando información relevante por la terminal mientras mide (comando
     enviado, mensajes del dispositivo, nº de puntos, VG alcanzado, punto de
     Dirac, cuentas atrás de las esperas) y guarda los datos igual que antes
     (All_info_*.txt + Id_Vfg__*_GRATMA-<rep>.txt).

Reconstruido a partir de tu GRATMA_IV.py: se conservan tus funciones
read_serial_to_file y split_txt_by_reps; solo se añade la lógica aleatoria.

ANTES DE EJECUTAR revisa el bloque de parámetros del final:
  - port         : tu puerto COM (aquí COM13). Ejecuta list_com_ports() para verlos.
  - folder_path  : carpeta de salida.
  - chip / NSENSOR / VD / VGINIT / VGEND / VGSWEEP / FBWD / NUM_REP / esperas.

Ejecutar:
    python GRATMA_IV_random.py
"""
import serial
import serial.tools.list_ports
import time
import os
import re
import random


def list_com_ports():
    """Lista los puertos COM disponibles (para averiguar el valor de 'port')."""
    ports = serial.tools.list_ports.comports()
    if not ports:
        print("❌ No se detecta ningún puerto COM.")
    else:
        print("✅ Puertos COM detectados:")
        for p in ports:
            print(f" - {p.device} | {p.description} | HWID: {p.hwid}")


def sensor_bitmask(sensor):
    """Sensor 1..8 -> máscara de bit que espera el comando 'iv'."""
    return 1 << (sensor - 1)


def send_cmd(ser, cmd, wait=0.4, verbose=True):
    """Envía una línea de comando cruda y muestra por terminal la respuesta
    del dispositivo (útil para 'um 1', 'sw', 'sv', etc.)."""
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
    t0 = time.time()
    while time.time() - t0 < wait + 0.8:
        line = ser.readline().decode(errors="ignore").strip()
        if line:
            replies.append(line)
            if verbose:
                print(f"      · {line}")
        else:
            break
    return replies


def countdown_sleep(seconds, label=""):
    """Duerme 'seconds' mostrando una cuenta atrás por terminal, para que se
    vea que sigue vivo durante las esperas largas."""
    seconds = int(round(seconds))
    step = 30 if seconds > 60 else 5
    remaining = seconds
    while remaining > 0:
        if remaining % step == 0 or remaining <= 5:
            print(f"      ... {label}{remaining}s restantes")
        time.sleep(1)
        remaining -= 1


def random_sequence_order(sensors):
    """Orden aleatorio alternando el grupo superior (1-4) y el inferior (5-8),
    igual que el modo I-V Randomised de la app. Dentro de cada grupo el orden
    es aleatorio; los grupos se van alternando arriba→abajo→arriba…"""
    top = [s for s in sensors if s <= 4]
    bottom = [s for s in sensors if s >= 5]
    random.shuffle(top)
    random.shuffle(bottom)
    order = []
    ti = bi = 0
    take_top = True
    while ti < len(top) or bi < len(bottom):
        if take_top and ti < len(top):
            order.append(top[ti]); ti += 1
        elif (not take_top) and bi < len(bottom):
            order.append(bottom[bi]); bi += 1
        elif ti < len(top):
            order.append(top[ti]); ti += 1
        elif bi < len(bottom):
            order.append(bottom[bi]); bi += 1
        take_top = not take_top
    return order


def read_serial_to_file(port, vd, vginit, vgend, vgsweep, sensor, fbwd, rep,
                        output_file, timeout, folder_path, ser=None, verbose=True):
    """
    Envía el comando 'iv' por el puerto serie y guarda toda la información
    recibida. Igual que tu versión original, pero:
      - si se le pasa un 'ser' ya abierto, lo reutiliza (no lo cierra) para
        que el modo 'um 1' se mantenga entre medidas;
      - saca información relevante por la terminal mientras mide.

    :param ser: conexión serie ya abierta (opcional). Si es None abre/cierra la suya.
    """
    own_conn = ser is None
    if own_conn:
        ser = serial.Serial(port, 115200, timeout=1)
        time.sleep(2)  # Esperar a que el puerto esté listo

    value = sensor_bitmask(sensor)
    # iv vs vginit vgfinal vgsweep sensor fwd(0)/fwd+back(1) repeticiones
    iv = f"iv {vd} {vginit} {vgend} {vgsweep} {value} {fbwd} {rep}\n"
    try:
        ser.reset_input_buffer()
    except Exception:
        pass
    ser.write(iv.encode())
    if verbose:
        print(f"    [CMD] {iv.strip()}   (bitmask sensor={value})")

    current_file = os.path.join(folder_path, output_file)

    def parse_point(s):
        """Devuelve (Vfg, Id) si la línea son 4 números 'Vfg;Id;Ig;Is'
        (acepta notación científica con exponente negativo), o None."""
        cols = s.split(";")
        if len(cols) != 4:
            return None
        try:
            return float(cols[0]), float(cols[1])
        except ValueError:
            return None

    npoints = 0
    max_vg = None
    dirac_vg = None
    min_id = None

    with open(current_file, "w", encoding="utf-8") as f:
        last_data_time = time.time()
        while True:
            line = ser.readline().decode(errors="ignore").strip()
            if line:
                f.write(line + "\n")
                last_data_time = time.time()
                pt = parse_point(line)
                if pt is not None:
                    npoints += 1
                    vg, idv = pt
                    if max_vg is None or vg > max_vg:
                        max_vg = vg
                    if min_id is None or abs(idv) < min_id:
                        min_id = abs(idv); dirac_vg = vg
                    if verbose and npoints % 25 == 0:
                        print(f"      ... {npoints} puntos (Vfg≈{vg:.4g})")
                elif verbose:
                    # Líneas de estado del dispositivo (relevantes para seguir
                    # la medida y para recuperar datos si algo falla).
                    print(f"      · {line}")
            else:
                if time.time() - last_data_time > timeout:
                    if verbose:
                        print("      [WARN] timeout esperando datos — corto la lectura")
                    break
            if line == "(GRATMA) Measurement sweep completed":
                break

    if own_conn:
        ser.close()

    if verbose:
        extra = ""
        if max_vg is not None:
            extra = f" | Vfg_max={max_vg:.4g}"
            if dirac_vg is not None:
                extra += f" | min|Id| en Vfg={dirac_vg:.4g}"
        print(f"    -> {npoints} puntos guardados en {output_file}{extra}")
    return npoints


def split_txt_by_reps(chip, sensor, numiter, info_extra, rep_offset, folder_path):
    """
    Extract from the init file all the data (Vfg;Id;Ig;Is)

    :param chip: Chip name (FxCx)
    :param sensor: Sensor number (1..8)
    :param numiter: Number of iteratitions performed
    :param info_extra: Add any extra information that it is needed
    :param rep_offset:
    :param folder_path: folder path
    """
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)

    input_file = f"All_info_{chip}_{sensor}_{numiter}_{info_extra}.txt"
    current_file = os.path.join(folder_path, input_file)
    # print(current_file)
    with open(current_file, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    rep = None
    collecting = False
    buffer = []
    num_pattern = re.compile(r"^-?[\d\.eE]+;-?[\d\.eE]+;-?[\d\.eE]+;-?[\d\.eE]+$")

    for line in lines:
        line_stripped = line.strip()

        # Detectar el inicio de un bloque
        match = re.match(r"\(GRATMA\) Sensor (\d+) \(rep=(\d+)\)", line_stripped)
        if match:
            sensor_found = int(match.group(1))
            rep = int(match.group(2))
            rep = rep + rep_offset
            if sensor_found == sensor:
                collecting = True
                buffer = []  # limpiar para un nuevo bloque
            else:
                collecting = False
            continue

        if line_stripped == "Vfg;Id;Ig;Is" and rep is not None:
            buffer = [line_stripped]  # empezamos un nuevo bloque con el encabezado
            collecting = True
            continue
        if collecting:
                if num_pattern.match(line_stripped):
                    buffer.append(line_stripped)
                else:
                    # Se acabó el bloque → guardamos
                    output_name = f"Id_Vfg__{chip}_{sensor}_{info_extra}_GRATMA-{rep}.txt"
                    output_path = os.path.join(folder_path, output_name)

                    with open(output_path, "w", encoding="utf-8") as out_f:
                        out_f.write("\n".join(buffer) + "\n")

                    print(f"    Guardado: {output_name}")

                    collecting = False
                    buffer = []

    # Por si el archivo termina justo al final de un bloque
    if collecting and buffer:
        output_name = f"Id_Vfg__{chip}_{sensor}_{info_extra}_GRATMA-{rep}.txt"
        output_path = os.path.join(folder_path, output_name)
        with open(output_path, "w", encoding="utf-8") as out_f:
            out_f.write("\n".join(buffer) + "\n")
        print(f"    Guardado: {output_name}")


def main():
    """
    Mide TODOS los sensores en orden aleatorio, repitiendo NUM_REP secuencias,
    igual que el modo I-V Randomised de la app: espera inicial, tierra a los no
    seleccionados (um 1), y 10 s entre sensor y sensor.
    """
    sensors = list(NSENSOR)

    print("=" * 62)
    print(f"GRATMA I-V ALEATORIO — chip {chip}")
    print(f"Sensores: {sensors}   |   secuencias (repeticiones): {NUM_REP}")
    print(f"VD={VD}  VGINIT={VGINIT}  VGEND={VGEND}  VGSWEEP={VGSWEEP}  FBWD={FBWD}")
    print(f"Estabilización inicial: {STABILIZE_S}s ({STABILIZE_S/60:.1f} min)")
    print(f"Espera entre sensores : {BETWEEN_SENSORS_S}s")
    print(f"Tierra a los no medidos (um 1): {'SÍ' if GND_UNSELECTED else 'NO'}")
    print(f"Carpeta de salida: {folder_path}")
    print(f"Puerto: {port}")
    print("=" * 62)

    if not os.path.exists(folder_path):
        os.makedirs(folder_path)

    # Una única conexión para toda la sesión (así 'um 1' se mantiene y no se
    # reinicia el dispositivo entre medidas).
    ser = serial.Serial(port, 115200, timeout=1)
    time.sleep(2)  # Esperar a que el puerto/dispositivo esté listo
    try:
        # Poner a tierra los sensores NO medidos, como la app (comando 'um 1').
        if GND_UNSELECTED:
            print("[SETUP] Activando tierra en los sensores no medidos (um 1) ...")
            send_cmd(ser, "um 1")

        # Espera de estabilización inicial.
        print(f"[ESPERA] Estabilizando {STABILIZE_S}s "
              f"({STABILIZE_S/60:.1f} min) antes de empezar ...")
        countdown_sleep(STABILIZE_S)

        first = True
        for seq in range(1, NUM_REP + 1):
            order = random_sequence_order(sensors)
            print("\n" + "#" * 62)
            print(f"# Secuencia {seq}/{NUM_REP} — orden aleatorio: {order}")
            print("#" * 62)

            for sensor in order:
                # 10 s al cambiar de sensor (no antes del primero de todos).
                if not first:
                    print(f"[ESPERA] {BETWEEN_SENSORS_S}s antes de pasar a S{sensor} ...")
                    countdown_sleep(BETWEEN_SENSORS_S)
                first = False

                grounded = [s for s in range(1, 9) if s != sensor]
                print(f"\n>>> [seq {seq}/{NUM_REP}] Midiendo S{sensor}   "
                      f"(a tierra: {', '.join('S' + str(g) for g in grounded)})")

                numiter = seq
                out = f"All_info_{chip}_{sensor}_{numiter}_{info_extra}.txt"
                read_serial_to_file(
                    port=port, vd=VD, vginit=VGINIT, vgend=VGEND, vgsweep=VGSWEEP,
                    sensor=sensor, fbwd=FBWD, rep=1, output_file=out, timeout=50,
                    folder_path=folder_path, ser=ser)

                # Extraer el fichero limpio Id_Vfg (numerado por secuencia).
                try:
                    split_txt_by_reps(chip, sensor, numiter, info_extra,
                                      rep_offset=seq - 1, folder_path=folder_path)
                except Exception as e:
                    print(f"    [WARN] split_txt_by_reps falló: {e}")
    finally:
        ser.close()

    print("\n\033[1mFinish\033[0m")


# ==================== Información del chip / sensores ====================
wafer = "USA1"
folder_path = r"C:\GRATMA\medidas"      # <-- carpeta de salida (cámbiala a la tuya)
chip = f"{wafer}_F5C9"
NSENSOR = [1, 2, 3, 4, 5, 6, 7, 8]      # TODOS los sensores (edítalo si quieres menos)

# ==================== Parámetros de medida ====================
VD = 50          # Drain voltage (mV)
VGINIT = 0       # Vg init (mV)
VGEND = 1200     # Vg end (mV)
VGSWEEP = 15     # Vg step (mV)
FBWD = 1         # solo forward -> 0 ; forward y backward -> 1
NUM_REP = 5      # número de secuencias (repeticiones sobre TODOS los sensores)

# ==================== Tiempos y modo (como la app) ====================
STABILIZE_S = 180        # espera inicial de estabilización (3 min)
BETWEEN_SENSORS_S = 10   # espera al cambiar de sensor (10 s)
GND_UNSELECTED = True    # poner a tierra los sensores no medidos (um 1)

# ==================== Extra / puerto ====================
info_extra = "rnd"       # texto extra en el nombre de los ficheros
port = "COM13"           # tu puerto COM (Administrador de dispositivos)

# =======================================================
if __name__ == "__main__":
    # Descomenta para listar los puertos COM antes de medir:
    # list_com_ports()
    main()