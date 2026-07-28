# -*- coding: utf-8 -*-
r"""

Versión portable del programa de gráficas GRATMA.

No contiene rutas personales ni nombres de usuario. Al ejecutarlo:
  1. Abre una ventana para elegir la carpeta que contiene las medidas.
  2. Propone guardar las imágenes dentro de una subcarpeta
     ``graficas_GRATMA``.
  3. Permite elegir otra carpeta de salida si se prefiere.
  4. Recuerda la última carpeta utilizada para la próxima ejecución.

También puede usarse desde PowerShell o Terminal:

    python gratma_graph_para_todos.py "C:\\ruta\\Medidas_GRATMA"

Indicando además una carpeta de salida:

    python gratma_graph_para_todos.py "C:\\ruta\\Medidas_GRATMA" ^
        --salida "C:\\ruta\\Resultados"

Archivos admitidos:
    <wafer>_<chip>_Array<sensor>_random_<secuencia>_<electrolito>.txt
    All_info_<wafer>_<chip>_Array<sensor>_random_<secuencia>_<electrolito>.txt

También mantiene compatibilidad con los nombres antiguos:
    Id_Vfg__<chip>_<sensor>_<extra>_GRATMA-<rep>.txt
    All_info_<chip>_<sensor>_<rep>_<extra>.txt

La cantidad de sensores y repeticiones se detecta automáticamente.
Genera cinco gráficas con todas las repeticiones y otras cinco con las
últimas repeticiones indicadas en NUM_ULTIMAS_REPETICIONES.

Las curvas de medida se dibujan con línea continua y sin puntos.
Las gráficas de Dirac sí conservan los puntos.
No utiliza pandas. Solo necesita numpy y matplotlib.
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    import numpy as np
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.ticker import ScalarFormatter
except ModuleNotFoundError as exc:
    modulo = getattr(exc, "name", "numpy o matplotlib")
    print(f"ERROR: falta el módulo '{modulo}'.")
    print("Instálalo con:")
    print("    python -m pip install numpy matplotlib")
    raise SystemExit(1)


# ============================ CONFIGURACIÓN =============================

# Nombre de la subcarpeta propuesta para guardar las imágenes.
NOMBRE_CARPETA_SALIDA = "graficas_GRATMA"

# Número de repeticiones finales que se comparan en el segundo grupo.
NUM_ULTIMAS_REPETICIONES = 3

# None = todos los sensores detectados.
# Ejemplo para representar solo algunos: [1, 3, 5]
SENSORES_A_GRAFICAR = None

BUSCAR_EN_SUBCARPETAS = True
MOSTRAR_FIGURAS = False

# Abre la carpeta de resultados al terminar.
ABRIR_CARPETA_AL_TERMINAR = True

# Guarda únicamente la última carpeta utilizada. No guarda medidas.
ARCHIVO_CONFIG = Path.home() / ".gratma_graph_config.json"

# Corriente en A dentro del txt -> µA en las gráficas.
A_MICROAMPERIOS = 1e6

X_LABEL = "Gate Voltage (V)"
Y_LABEL = "Sensor Current, Is (µA)"

# None = límites automáticos. Se pueden fijar, por ejemplo:
# XLIM = (-0.2, 1.2)
# YLIM = (50, 1650)
XLIM = None
YLIM = None
XLIM_DIRAC = None

# Gráfica de repetibilidad del punto de Dirac por repetición.
# El nombre de la muestra y del chip se deduce SIEMPRE de cada archivo.
# Esta variable se conserva por compatibilidad, pero no sustituye al nombre
# automático del chip, para evitar que todos los gráficos muestren el mismo.
TITULO_MUESTRA_DIRAC = None

# Texto opcional bajo el nombre de la muestra.
# Ejemplo: "NaOH105'"
SUBTITULO_MUESTRA_DIRAC = None

# None: límites verticales automáticos compartidos por Forward y Backward.
# Ejemplo: YLIM_DIRAC_REPETIBILIDAD = (0.60, 1.00)
YLIM_DIRAC_REPETIBILIDAD = None

TAMANIO_FIGURA_CURVAS = (11, 7)
TAMANIO_FIGURA_MEDIA = (9, 6)
TAMANIO_FIGURA_DIRAC = (8, 4.5)
TAMANIO_FIGURA_DIRAC_REPETIBILIDAD = (14.5, 6.2)
DPI = 220

# =======================================================================


def analizar_argumentos():
    parser = argparse.ArgumentParser(
        description=(
            "Genera las gráficas GRATMA seleccionando una carpeta de medidas."
        )
    )
    parser.add_argument(
        "carpeta_medidas",
        nargs="?",
        help="Carpeta que contiene los archivos .txt de las medidas.",
    )
    parser.add_argument(
        "-o",
        "--salida",
        help="Carpeta donde se guardarán las imágenes.",
    )
    parser.add_argument(
        "--no-abrir",
        action="store_true",
        help="No abrir automáticamente la carpeta de resultados al terminar.",
    )
    return parser.parse_args()


def cargar_configuracion():
    try:
        with open(ARCHIVO_CONFIG, "r", encoding="utf-8") as archivo:
            datos = json.load(archivo)
        return datos if isinstance(datos, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def guardar_configuracion(carpeta_medidas, carpeta_salida):
    datos = {
        "ultima_carpeta_medidas": str(carpeta_medidas),
        "ultima_carpeta_salida": str(carpeta_salida),
    }
    try:
        with open(ARCHIVO_CONFIG, "w", encoding="utf-8") as archivo:
            json.dump(datos, archivo, ensure_ascii=False, indent=2)
    except OSError:
        pass


def seleccionar_carpeta_ventana(titulo, carpeta_inicial=None):
    """
    Abre el selector nativo de carpetas. Devuelve Path o None si se cancela.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog

        raiz = tk.Tk()
        raiz.withdraw()
        try:
            raiz.attributes("-topmost", True)
        except Exception:
            pass

        opciones = {"title": titulo, "mustexist": True}
        if carpeta_inicial and Path(carpeta_inicial).is_dir():
            opciones["initialdir"] = str(carpeta_inicial)

        seleccion = filedialog.askdirectory(**opciones)
        raiz.destroy()
        return Path(seleccion).expanduser().resolve() if seleccion else None
    except Exception:
        return None


def elegir_carpeta_medidas(ruta_argumento=None):
    """
    Prioridad:
      1. Ruta pasada al ejecutar el programa.
      2. Ventana de selección.
      3. Ruta escrita en la terminal si la ventana no está disponible.
    """
    if ruta_argumento:
        candidata = Path(ruta_argumento).expanduser().resolve()
        if candidata.is_dir():
            return candidata
        print(f"AVISO: la carpeta indicada no existe: {candidata}")

    config = cargar_configuracion()
    ultima = config.get("ultima_carpeta_medidas")
    carpeta_inicial = Path(ultima) if ultima else Path(__file__).resolve().parent

    seleccionada = seleccionar_carpeta_ventana(
        "Selecciona la carpeta que contiene las medidas GRATMA",
        carpeta_inicial,
    )
    if seleccionada:
        return seleccionada

    print("\nNo se seleccionó ninguna carpeta en la ventana.")
    try:
        respuesta = input(
            "Escribe o pega la ruta de la carpeta de medidas "
            "(Enter para cancelar): "
        ).strip().strip('"')
    except (EOFError, KeyboardInterrupt):
        return None

    if not respuesta:
        return None

    candidata = Path(respuesta).expanduser().resolve()
    if candidata.is_dir():
        return candidata

    print(f"ERROR: la carpeta no existe: {candidata}")
    return None


def elegir_carpeta_salida(carpeta_medidas, ruta_argumento=None):
    """
    Propone una subcarpeta dentro de las medidas y permite escoger otra.
    """
    if ruta_argumento:
        salida = Path(ruta_argumento).expanduser().resolve()
        salida.mkdir(parents=True, exist_ok=True)
        return salida

    salida_propuesta = carpeta_medidas / NOMBRE_CARPETA_SALIDA

    try:
        import tkinter as tk
        from tkinter import messagebox

        raiz = tk.Tk()
        raiz.withdraw()
        try:
            raiz.attributes("-topmost", True)
        except Exception:
            pass

        usar_propuesta = messagebox.askyesno(
            "Carpeta de salida",
            "¿Quieres guardar las gráficas en esta carpeta?\n\n"
            f"{salida_propuesta}\n\n"
            "Pulsa «No» para elegir otra carpeta.",
            parent=raiz,
        )
        raiz.destroy()

        if usar_propuesta:
            salida_propuesta.mkdir(parents=True, exist_ok=True)
            return salida_propuesta

        otra = seleccionar_carpeta_ventana(
            "Selecciona dónde guardar las gráficas",
            carpeta_medidas,
        )
        if otra:
            otra.mkdir(parents=True, exist_ok=True)
            return otra
    except Exception:
        pass

    salida_propuesta.mkdir(parents=True, exist_ok=True)
    return salida_propuesta


def abrir_carpeta(path):
    """Abre la carpeta con el explorador del sistema operativo."""
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


def buscar_archivos(carpeta, patron):
    if BUSCAR_EN_SUBCARPETAS:
        return glob.glob(str(Path(carpeta) / "**" / patron), recursive=True)
    return glob.glob(str(Path(carpeta) / patron))


def descomponer_muestra(nombre_muestra):
    """
    Separa el identificador completo de la muestra y el nombre del chip.

    Convención esperada:
      USA_APS2_F4C8 -> muestra/base: USA_APS2, chip: F4C8

    El último bloque separado por '_' se considera el nombre del chip.
    """
    nombre_muestra = str(nombre_muestra).strip("_")
    if "_" in nombre_muestra:
        base, chip = nombre_muestra.rsplit("_", 1)
    else:
        base, chip = "", nombre_muestra

    titulo = f"{base} - {chip}" if base else chip
    return {
        "muestra": nombre_muestra,
        "base": base,
        "chip": chip,
        "titulo": titulo,
    }


def nombre_seguro_archivo(texto):
    """Convierte un identificador en un nombre válido para una carpeta."""
    limpio = re.sub(r"[^A-Za-z0-9._-]+", "_", str(texto)).strip("._-")
    return limpio or "muestra_GRATMA"


def extraer_metadata_nuevo(path):
    """
    Obtiene los datos desde el formato nuevo:

      Wafer_Chip_ArrayN_random_Secuencia_Electrolito.txt

    También acepta el archivo de respaldo:

      All_info_Wafer_Chip_ArrayN_random_Secuencia_Electrolito.txt

    El patrón se interpreta desde ``_ArrayN_random_``. Por eso el wafer puede
    contener guiones bajos y el electrolito también, por ejemplo PB-S0_01.
    """
    nombre = Path(path).name
    es_all_info = nombre.lower().startswith("all_info_")
    nombre_medida = nombre[len("All_info_"):] if es_all_info else nombre

    patron = re.compile(
        r"^(?P<muestra>.+)_Array(?P<sensor>\d+)_random_"
        r"(?P<rep>\d+)_(?P<electrolito>.+)\.txt$",
        re.IGNORECASE,
    )
    coincidencia = patron.match(nombre_medida)
    if coincidencia is None:
        return None

    datos_muestra = descomponer_muestra(coincidencia.group("muestra"))
    electrolito = coincidencia.group("electrolito")
    titulo_base = datos_muestra["titulo"]

    return {
        **datos_muestra,
        "titulo": f"{titulo_base} — {electrolito}",
        "sensor": int(coincidencia.group("sensor")),
        "rep": int(coincidencia.group("rep")),
        "electrolito": electrolito,
        "grupo": f"{datos_muestra['muestra']}__{electrolito}",
        "tipo": "All_info_nuevo" if es_all_info else "nombre_nuevo",
    }


def extraer_metadata_limpio(path):
    """Obtiene muestra, chip, sensor y repetición desde Id_Vfg__...."""
    nombre = Path(path).name
    patron = re.compile(
        r"^Id_Vfg__(?P<chip>.+)_(?P<sensor>\d+)_"
        r"(?P<extra>.+)_GRATMA-(?P<rep>\d+)\.txt$",
        re.IGNORECASE,
    )
    coincidencia = patron.match(nombre)
    if coincidencia is None:
        return None
    datos_muestra = descomponer_muestra(coincidencia.group("chip"))
    return {
        **datos_muestra,
        "sensor": int(coincidencia.group("sensor")),
        "rep": int(coincidencia.group("rep")),
        "electrolito": "",
        "grupo": datos_muestra["muestra"],
    }


def extraer_metadata_bruto(path):
    """Obtiene muestra, chip, sensor y repetición desde All_info_ antiguo."""
    nombre = Path(path).name
    patron = re.compile(
        r"^All_info_(?P<chip>.+)_(?P<sensor>\d+)_"
        r"(?P<rep>\d+)_(?P<extra>.+)\.txt$",
        re.IGNORECASE,
    )
    coincidencia = patron.match(nombre)
    if coincidencia is None:
        return None
    datos_muestra = descomponer_muestra(coincidencia.group("chip"))
    return {
        **datos_muestra,
        "sensor": int(coincidencia.group("sensor")),
        "rep": int(coincidencia.group("rep")),
        "electrolito": "",
        "grupo": datos_muestra["muestra"],
    }


def clave_medida(metadata):
    """Clave única que evita mezclar chips o electrolitos diferentes."""
    return (
        metadata.get("grupo", metadata["muestra"]),
        metadata["sensor"],
        metadata["rep"],
    )


def localizar_medidas(carpeta):
    """
    Devuelve una medida por (muestra, electrolito, sensor, repetición).

    Prioridad de lectura:
      1. Archivo definitivo con el nombre nuevo.
      2. Archivo All_info con el nombre nuevo.
      3. Formatos antiguos Id_Vfg y All_info.

    Así, un archivo de respaldo nunca sustituye a un TXT definitivo.
    """
    medidas = {}

    # Formato nuevo definitivo. El filtro también encuentra All_info, que se
    # omite aquí para cargarlo después con menor prioridad.
    for path in sorted(buscar_archivos(carpeta, "*_Array*_random_*.txt")):
        if Path(path).name.lower().startswith("all_info_"):
            continue
        metadata = extraer_metadata_nuevo(path)
        if metadata is None:
            continue
        medidas[clave_medida(metadata)] = {
            **metadata,
            "path": Path(path),
        }

    # Respaldo nuevo: se usa solo si no existe el definitivo equivalente.
    for path in sorted(buscar_archivos(carpeta, "All_info_*_Array*_random_*.txt")):
        metadata = extraer_metadata_nuevo(path)
        if metadata is None:
            continue
        medidas.setdefault(
            clave_medida(metadata),
            {
                **metadata,
                "path": Path(path),
            },
        )

    # Compatibilidad con archivos antiguos.
    for path in sorted(buscar_archivos(carpeta, "Id_Vfg__*GRATMA-*.txt")):
        metadata = extraer_metadata_limpio(path)
        if metadata is None:
            continue
        medidas.setdefault(
            clave_medida(metadata),
            {
                **metadata,
                "path": Path(path),
                "tipo": "Id_Vfg",
            },
        )

    for path in sorted(buscar_archivos(carpeta, "All_info_*.txt")):
        # Los All_info nuevos ya se han procesado arriba.
        if extraer_metadata_nuevo(path) is not None:
            continue
        metadata = extraer_metadata_bruto(path)
        if metadata is None:
            continue
        medidas.setdefault(
            clave_medida(metadata),
            {
                **metadata,
                "path": Path(path),
                "tipo": "All_info",
            },
        )

    return medidas


def convertir_numero(texto):
    return float(texto.strip().replace(",", "."))


def leer_curva(path):
    """
    Lee Vfg y la corriente útil sin pandas.

    Preferencia:
      - Si hay cabecera Vfg;Id;Ig;Is, representa Is.
      - Si solo existe Vfg;Id, representa Id.
      - Sin cabecera: 4 columnas -> columna 4; 2 columnas -> columna 2.
    """
    voltajes = []
    corrientes = []

    indice_v = None
    indice_i = None
    nombre_corriente = None
    dentro_tabla = False

    with open(path, "r", encoding="utf-8", errors="ignore") as archivo:
        for linea in archivo:
            texto = linea.strip()
            if not texto:
                continue

            # Buscar una cabecera separada por ';'.
            partes_cabecera = [p.strip() for p in texto.split(";")]
            normalizadas = [p.lower() for p in partes_cabecera]

            if "vfg" in normalizadas:
                indice_v = normalizadas.index("vfg")
                if "is" in normalizadas:
                    indice_i = normalizadas.index("is")
                    nombre_corriente = "Is"
                elif "id" in normalizadas:
                    indice_i = normalizadas.index("id")
                    nombre_corriente = "Id"
                else:
                    continue
                dentro_tabla = True
                continue

            # Si ya conocemos las columnas, leer filas de esa tabla.
            if dentro_tabla and indice_v is not None and indice_i is not None:
                columnas = [p.strip() for p in texto.split(";")]
                if len(columnas) <= max(indice_v, indice_i):
                    continue
                try:
                    v = convertir_numero(columnas[indice_v])
                    i = convertir_numero(columnas[indice_i])
                except ValueError:
                    continue
                if np.isfinite(v) and np.isfinite(i):
                    voltajes.append(v)
                    corrientes.append(i)
                continue

            # Compatibilidad con archivos antiguos sin cabecera.
            separadores = texto.replace(",", ".").replace(";", " ").split()
            try:
                numeros = [float(x) for x in separadores]
            except ValueError:
                continue

            if len(numeros) >= 4:
                voltajes.append(numeros[0])
                corrientes.append(numeros[3])
                nombre_corriente = "Is"
            elif len(numeros) >= 2:
                voltajes.append(numeros[0])
                corrientes.append(numeros[1])
                nombre_corriente = "Id"

    if len(voltajes) < 3:
        raise ValueError("no se encontraron al menos tres puntos numéricos")

    v = np.asarray(voltajes, dtype=float)
    i = np.asarray(corrientes, dtype=float) * A_MICROAMPERIOS

    if np.allclose(i, 0.0):
        raise ValueError(
            f"la columna {nombre_corriente or 'de corriente'} contiene solo ceros"
        )

    return v, i, (nombre_corriente or "corriente")


def separar_forward_backward(v, i):
    """
    Separa por el máximo Vfg. Incluye el punto máximo en ambas ramas.
    """
    v = np.asarray(v, dtype=float)
    i = np.asarray(i, dtype=float)

    if len(v) < 3:
        return (v, i), (np.array([]), np.array([]))

    vertice = int(np.argmax(v))
    forward = (v[: vertice + 1], i[: vertice + 1])

    if vertice >= len(v) - 2:
        backward = (np.array([]), np.array([]))
    else:
        backward = (v[vertice:], i[vertice:])

    return forward, backward


def cargar_curvas(medidas):
    curvas = []
    columnas_usadas = set()

    for clave in sorted(medidas):
        registro = medidas[clave]
        sensor = registro["sensor"]
        rep = registro["rep"]

        if SENSORES_A_GRAFICAR is not None and sensor not in SENSORES_A_GRAFICAR:
            continue

        try:
            v, i, columna = leer_curva(registro["path"])
            forward, backward = separar_forward_backward(v, i)
        except Exception as exc:
            print(
                f"  [WARN] S{sensor} Rep {rep}: "
                f"{registro['path'].name}: {exc}"
            )
            continue

        columnas_usadas.add(columna)
        curvas.append(
            {
                "muestra": registro["muestra"],
                "base": registro["base"],
                "chip": registro["chip"],
                "titulo_muestra": registro["titulo"],
                "electrolito": registro.get("electrolito", ""),
                "grupo": registro.get("grupo", registro["muestra"]),
                "sensor": sensor,
                "rep": rep,
                "path": registro["path"],
                "v": v,
                "i": i,
                "forward": forward,
                "backward": backward,
            }
        )

    return curvas, columnas_usadas


def configurar_ejes(ax, titulo=None, x_label=X_LABEL, y_label=Y_LABEL):
    ax.tick_params(
        axis="both",
        which="both",
        direction="in",
        top=True,
        bottom=True,
        left=True,
        right=True,
        length=6,
        width=1.4,
        labelsize=14,
    )
    for lado in ("bottom", "top", "left", "right"):
        ax.spines[lado].set_visible(True)
        ax.spines[lado].set_linewidth(1.7)

    for etiqueta in ax.get_xticklabels() + ax.get_yticklabels():
        etiqueta.set_fontweight("bold")

    ax.set_xlabel(x_label, fontsize=18, fontweight="bold", labelpad=10)
    ax.set_ylabel(y_label, fontsize=18, fontweight="bold", labelpad=10)

    if titulo:
        ax.set_title(titulo, fontsize=17, fontweight="bold", pad=12)

    ax.yaxis.set_major_formatter(ScalarFormatter(useMathText=True))
    ax.ticklabel_format(style="plain", axis="y")

    if XLIM is not None:
        ax.set_xlim(*XLIM)
    if YLIM is not None:
        ax.set_ylim(*YLIM)


def estilos(sensores):
    try:
        mapa = plt.colormaps.get_cmap("tab10")
    except AttributeError:
        mapa = plt.cm.get_cmap("tab10")
    colores = {
        sensor: mapa(indice % 10)
        for indice, sensor in enumerate(sorted(sensores))
    }
    lineas = {
        1: "-",
        2: "-",
        3: "-",
        4: "-",
        5: "-",
    }
    return colores, lineas


def leyendas(ax, sensores, repeticiones, colores, lineas):
    elementos_sensores = [
        Line2D([0], [0], color=colores[s], lw=2.3, label=f"Sensor {s}")
        for s in sensores
    ]

    ax.legend(
        handles=elementos_sensores,
        title="Sensores",
        bbox_to_anchor=(1.02, 1.0),
        loc="upper left",
        frameon=False,
        fontsize=10,
        title_fontsize=11,
    )


def guardar_figura(fig, carpeta_salida, nombre):
    carpeta_salida.mkdir(parents=True, exist_ok=True)
    ruta = carpeta_salida / nombre
    fig.savefig(ruta, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"    Guardada: {ruta}")
    return ruta


def graficar_ramas(curvas, repeticiones, rama, titulo, salida, nombre):
    seleccionadas = [c for c in curvas if c["rep"] in repeticiones]
    sensores = sorted({c["sensor"] for c in seleccionadas})
    colores, lineas = estilos(sensores)

    fig, ax = plt.subplots(figsize=TAMANIO_FIGURA_CURVAS)
    dibujadas = 0

    for curva in seleccionadas:
        v, i = curva[rama]
        if len(v) < 2:
            continue
        ax.plot(
            v,
            i,
            color=colores[curva["sensor"]],
            linestyle="-",
            linewidth=1.4,
            alpha=0.95,
            solid_capstyle="round",
            solid_joinstyle="round",
        )
        dibujadas += 1

    configurar_ejes(ax, f"{titulo} ({dibujadas} curvas)")
    leyendas(ax, sensores, repeticiones, colores, lineas)
    fig.tight_layout(rect=(0, 0, 0.80, 1))
    return guardar_figura(fig, salida, nombre)


def preparar_interpolacion(lista_curvas):
    """
    Interpola sobre el intervalo común real de todas las curvas.
    Evita extrapolar fuera de los datos.
    """
    validas = [(np.asarray(v), np.asarray(i)) for v, i in lista_curvas if len(v) >= 2]
    if not validas:
        raise ValueError("no hay curvas válidas para interpolar")

    minimo_comun = max(float(np.min(v)) for v, _ in validas)
    maximo_comun = min(float(np.max(v)) for v, _ in validas)
    if minimo_comun >= maximo_comun:
        raise ValueError("las curvas no comparten un rango de Vfg")

    x_comun = np.linspace(minimo_comun, maximo_comun, 300)
    interpoladas = []

    for v, i in validas:
        orden = np.argsort(v)
        v_ord = v[orden]
        i_ord = i[orden]

        # np.interp necesita una x estrictamente creciente.
        v_unica, indices = np.unique(v_ord, return_index=True)
        i_unica = i_ord[indices]
        if len(v_unica) < 2:
            continue

        interpoladas.append(np.interp(x_comun, v_unica, i_unica))

    if not interpoladas:
        raise ValueError("no se pudo interpolar ninguna curva")

    matriz = np.asarray(interpoladas, dtype=float)
    return x_comun, matriz.mean(axis=0), matriz.std(axis=0), len(matriz)


def graficar_media_std(curvas, repeticiones, titulo, salida, nombre):
    seleccionadas = [c for c in curvas if c["rep"] in repeticiones]
    forward = [c["forward"] for c in seleccionadas if len(c["forward"][0]) >= 2]
    backward = [c["backward"] for c in seleccionadas if len(c["backward"][0]) >= 2]

    x_f, media_f, std_f, n_f = preparar_interpolacion(forward)

    fig, ax = plt.subplots(figsize=TAMANIO_FIGURA_MEDIA)
    ax.plot(x_f, media_f, linewidth=2.2, label=f"Mean Forward (n={n_f})")
    ax.fill_between(x_f, media_f - std_f, media_f + std_f, alpha=0.25)

    if backward:
        x_b, media_b, std_b, n_b = preparar_interpolacion(backward)
        ax.plot(x_b, media_b, linewidth=2.2, label=f"Mean Backward (n={n_b})")
        ax.fill_between(x_b, media_b - std_b, media_b + std_b, alpha=0.25)

    configurar_ejes(ax, titulo)
    leyenda = ax.legend(fontsize=13, frameon=False)
    for texto in leyenda.get_texts():
        texto.set_fontweight("bold")

    fig.tight_layout()
    return guardar_figura(fig, salida, nombre)


def puntos_dirac(curvas, repeticiones):
    forward = []
    backward = []

    for curva in curvas:
        if curva["rep"] not in repeticiones:
            continue

        v_f, i_f = curva["forward"]
        if len(v_f):
            forward.append(float(v_f[int(np.argmin(i_f))]))

        v_b, i_b = curva["backward"]
        if len(v_b):
            backward.append(float(v_b[int(np.argmin(i_b))]))

    return forward, backward


def graficar_dirac(curvas, repeticiones, titulo, salida, nombre):
    forward, backward = puntos_dirac(curvas, repeticiones)

    datos = []
    etiquetas = []
    if backward:
        datos.append(backward)
        etiquetas.append("Backward")
    if forward:
        datos.append(forward)
        etiquetas.append("Forward")

    if not datos:
        raise ValueError("no se pudieron calcular puntos de Dirac")

    fig, ax = plt.subplots(figsize=TAMANIO_FIGURA_DIRAC)
    posiciones = np.arange(1, len(datos) + 1)

    partes = ax.violinplot(
        datos,
        positions=posiciones,
        vert=False,
        showmeans=False,
        showextrema=False,
        showmedians=False,
    )

    for cuerpo in partes["bodies"]:
        cuerpo.set_alpha(0.30)

    rng = np.random.default_rng(12345)
    for posicion, valores in zip(posiciones, datos):
        mediana = float(np.median(valores))
        ax.plot(
            [mediana, mediana],
            [posicion - 0.30, posicion + 0.30],
            linewidth=2.2,
        )
        jitter = rng.normal(posicion, 0.04, size=len(valores))
        ax.scatter(valores, jitter, alpha=0.82, s=30)

    ax.set_yticks(posiciones)
    ax.set_yticklabels(etiquetas, fontsize=16, fontweight="bold")
    ax.set_xlabel(
        r"$\mathbf{V}_{\mathbf{Dirac}}\ \mathbf{(V)}$",
        fontsize=20,
    )
    ax.set_title(titulo, fontsize=17, fontweight="bold", pad=12)
    ax.tick_params(
        direction="in",
        length=6,
        width=1.4,
        which="both",
        top=True,
        bottom=True,
        left=True,
        right=True,
        labelsize=14,
    )
    for etiqueta in ax.get_xticklabels() + ax.get_yticklabels():
        etiqueta.set_fontweight("bold")
    for borde in ax.spines.values():
        borde.set_linewidth(1.7)

    if XLIM_DIRAC is not None:
        ax.set_xlim(*XLIM_DIRAC)

    fig.tight_layout()
    return guardar_figura(fig, salida, nombre)


def extraer_titulo_muestra_dirac(curvas):
    """
    Devuelve el título automático de la muestra/chip de estas curvas.

    Ejemplo:
      Id_Vfg__USA_APS2_F4C8_1_rnd_GRATMA-1.txt
      -> USA_APS2 - F4C8

    El título se toma de los metadatos de cada curva y no de la primera ruta
    encontrada. Así se evita reutilizar el nombre de otro chip.
    """
    titulos = sorted(
        {
            curva.get("titulo_muestra", "").strip()
            for curva in curvas
            if curva.get("titulo_muestra", "").strip()
        }
    )

    if not titulos:
        return "GRATMA"
    if len(titulos) == 1:
        return titulos[0]
    return " / ".join(titulos)


def puntos_dirac_por_sensor_y_repeticion(curvas, repeticiones):
    """
    Devuelve los puntos de Dirac conservando sensor, repetición y rama.

    Estructura:
      {
        "forward": {sensor: {repetición: Vdirac}},
        "backward": {sensor: {repetición: Vdirac}},
      }
    """
    resultado = {"forward": {}, "backward": {}}
    repeticiones = set(repeticiones)

    for curva in curvas:
        rep = curva["rep"]
        sensor = curva["sensor"]
        if rep not in repeticiones:
            continue

        for rama in ("forward", "backward"):
            v, i = curva[rama]
            if len(v) == 0 or len(i) == 0:
                continue
            indice_minimo = int(np.argmin(i))
            resultado[rama].setdefault(sensor, {})[rep] = float(v[indice_minimo])

    return resultado


def limites_dirac_repetibilidad(datos):
    """Calcula límites Y comunes y redondeados para los dos paneles."""
    if YLIM_DIRAC_REPETIBILIDAD is not None:
        return YLIM_DIRAC_REPETIBILIDAD

    valores = []
    for rama in datos.values():
        for por_repeticion in rama.values():
            valores.extend(por_repeticion.values())

    if not valores:
        return None

    minimo = float(np.min(valores))
    maximo = float(np.max(valores))
    rango = maximo - minimo
    margen = max(0.02, 0.12 * rango)
    paso = 0.05

    inferior = np.floor((minimo - margen) / paso) * paso
    superior = np.ceil((maximo + margen) / paso) * paso
    if np.isclose(inferior, superior):
        inferior -= paso
        superior += paso

    return float(inferior), float(superior)


def graficar_dirac_repetibilidad(curvas, repeticiones, salida, nombre):
    """
    Dibuja VDirac frente a la repetición, con una línea por sensor y dos
    paneles independientes: Forward y Backward.
    """
    repeticiones = sorted(repeticiones)
    datos = puntos_dirac_por_sensor_y_repeticion(curvas, repeticiones)
    sensores = sorted(
        set(datos["forward"].keys()) | set(datos["backward"].keys())
    )

    if not sensores:
        raise ValueError("no se pudieron calcular puntos de Dirac por sensor")

    colores, _ = estilos(sensores)
    posiciones = np.arange(len(repeticiones), dtype=float)
    etiquetas_x = [f"Rep {rep}" for rep in repeticiones]

    fig, ejes = plt.subplots(
        1,
        2,
        figsize=TAMANIO_FIGURA_DIRAC_REPETIBILIDAD,
        sharey=True,
    )

    for ax, rama, titulo_rama in zip(
        ejes,
        ("forward", "backward"),
        ("Forward", "Backward"),
    ):
        for sensor in sensores:
            por_repeticion = datos[rama].get(sensor, {})
            valores = [
                por_repeticion.get(rep, np.nan)
                for rep in repeticiones
            ]
            if np.all(np.isnan(valores)):
                continue

            ax.plot(
                posiciones,
                valores,
                color=colores[sensor],
                marker="o",
                markersize=6.2,
                linewidth=1.8,
                label=f"S{sensor}",
                solid_capstyle="round",
                solid_joinstyle="round",
            )

        ax.set_title(titulo_rama, fontsize=21, fontweight="bold", pad=8)
        ax.set_xlabel("Repetition", fontsize=17, fontweight="bold", labelpad=8)
        ax.set_xticks(posiciones)
        ax.set_xticklabels(etiquetas_x)
        ax.grid(axis="y", alpha=0.25, linewidth=0.8)
        ax.tick_params(
            axis="both",
            which="both",
            direction="in",
            top=True,
            bottom=True,
            left=True,
            right=True,
            length=6,
            width=1.4,
            labelsize=13,
        )
        for etiqueta in ax.get_xticklabels() + ax.get_yticklabels():
            etiqueta.set_fontweight("bold")
        for borde in ax.spines.values():
            borde.set_visible(True)
            borde.set_linewidth(1.7)

        leyenda = ax.legend(
            title="Sensor",
            ncol=2,
            loc="upper right",
            frameon=False,
            fontsize=10.5,
            title_fontsize=11,
        )
        if leyenda is not None:
            leyenda.get_title().set_fontweight("bold")

    for ax in ejes:
        ax.set_ylabel(
            "Dirac Voltage (V)",
            fontsize=17,
            fontweight="bold",
            labelpad=8,
        )
        # Matplotlib oculta las etiquetas Y del segundo panel al compartir eje.
        ax.tick_params(axis="y", labelleft=True)

    limites_y = limites_dirac_repetibilidad(datos)
    if limites_y is not None:
        ejes[0].set_ylim(*limites_y)

    lineas_titulo = [extraer_titulo_muestra_dirac(curvas)]
    if SUBTITULO_MUESTRA_DIRAC:
        lineas_titulo.append(SUBTITULO_MUESTRA_DIRAC)
    lineas_titulo.append("Dirac Point Repeatability")

    fig.suptitle(
        "\n".join(lineas_titulo),
        fontsize=23,
        fontweight="bold",
        y=0.995,
        linespacing=0.88,
    )

    # Ajuste manual para mantener una composición similar a la referencia:
    # título centrado arriba y dos paneles anchos con poca separación.
    margen_superior = 0.66 if SUBTITULO_MUESTRA_DIRAC else 0.71
    fig.subplots_adjust(
        left=0.07,
        right=0.985,
        bottom=0.15,
        top=margen_superior,
        wspace=0.12,
    )
    return guardar_figura(fig, salida, nombre)


def comprobar_repeticiones(curvas, repeticiones_esperadas):
    sensores = sorted({c["sensor"] for c in curvas})
    disponibles = {(c["sensor"], c["rep"]) for c in curvas}

    print("\nComprobación de archivos:")
    for sensor in sensores:
        reps = [
            r for r in repeticiones_esperadas
            if (sensor, r) in disponibles
        ]
        faltan = [r for r in repeticiones_esperadas if r not in reps]
        mensaje = f"  Sensor {sensor}: repeticiones {reps}"
        if faltan:
            mensaje += f" | faltan {faltan}"
        print(mensaje)


def generar_grupo(
    curvas,
    repeticiones,
    etiqueta,
    prefijo,
    salida,
    titulo_muestra,
):
    print(f"\nGenerando grupo: {titulo_muestra} | {etiqueta}")

    graficar_ramas(
        curvas,
        repeticiones,
        "forward",
        f"{titulo_muestra} — Todos los sensores — Forward — {etiqueta}",
        salida,
        f"{prefijo}_forward.png",
    )
    graficar_ramas(
        curvas,
        repeticiones,
        "backward",
        f"{titulo_muestra} — Todos los sensores — Backward — {etiqueta}",
        salida,
        f"{prefijo}_backward.png",
    )
    graficar_media_std(
        curvas,
        repeticiones,
        f"{titulo_muestra} — Mean curve ± standard deviation — {etiqueta}",
        salida,
        f"{prefijo}_media_std.png",
    )
    graficar_dirac(
        curvas,
        repeticiones,
        f"{titulo_muestra} — Dirac point distribution — {etiqueta}",
        salida,
        f"{prefijo}_dirac_violin.png",
    )
    graficar_dirac_repetibilidad(
        curvas,
        repeticiones,
        salida,
        f"{prefijo}_dirac_repetibilidad.png",
    )


def main():
    args = analizar_argumentos()

    print("=" * 72)
    print("GRATMA — GRÁFICAS PORTABLES")
    print("Selecciona las carpetas mediante ventanas; no hay rutas personales.")
    print("Curvas de medida lisas; las gráficas de Dirac conservan los puntos.")
    print("=" * 72)

    carpeta = elegir_carpeta_medidas(args.carpeta_medidas)
    if carpeta is None:
        print("\nOperación cancelada: no se seleccionó carpeta de medidas.")
        raise SystemExit(0)

    salida = elegir_carpeta_salida(carpeta, args.salida)
    print(f"Carpeta de medidas: {carpeta}")
    print(f"Carpeta de salida : {salida}")

    medidas = localizar_medidas(carpeta)
    if not medidas:
        print("\nERROR: no se encontraron archivos compatibles.")
        print(
            "Se esperaban nombres como "
            "Wafer_Chip_Array1_random_1_PB-S0_01.txt"
        )
        print("También se admiten los formatos antiguos Id_Vfg y All_info.")
        print("\nAsegúrate de seleccionar la carpeta, no un archivo individual.")
        raise SystemExit(1)

    print(f"Archivos únicos detectados: {len(medidas)}")
    curvas, columnas = cargar_curvas(medidas)

    if not curvas:
        print("\nERROR: se encontraron archivos, pero ninguna curva fue válida.")
        raise SystemExit(1)

    curvas_por_muestra = {}
    for curva in curvas:
        grupo = curva.get("grupo", curva["muestra"])
        curvas_por_muestra.setdefault(grupo, []).append(curva)

    titulos_detectados = [
        extraer_titulo_muestra_dirac(curvas_muestra)
        for _, curvas_muestra in sorted(curvas_por_muestra.items())
    ]

    print(f"Muestras/chips detectados: {titulos_detectados}")
    print(f"Curvas válidas: {len(curvas)}")
    print(f"Columna(s) de corriente utilizada(s): {', '.join(sorted(columnas))}")

    varias_muestras = len(curvas_por_muestra) > 1

    for grupo_muestra, curvas_muestra in sorted(curvas_por_muestra.items()):
        titulo_muestra = extraer_titulo_muestra_dirac(curvas_muestra)
        muestra = curvas_muestra[0]["muestra"]
        electrolito = curvas_muestra[0].get("electrolito", "")
        sensores = sorted({c["sensor"] for c in curvas_muestra})
        repeticiones = sorted({c["rep"] for c in curvas_muestra})
        ultimas = repeticiones[
            -min(NUM_ULTIMAS_REPETICIONES, len(repeticiones)):
        ]

        # Cuando hay varios chips, cada uno se guarda en su propia carpeta.
        # Así no se mezclan datos ni se sobrescriben imágenes con el mismo nombre.
        nombre_grupo_salida = (
            f"{muestra}_{electrolito}" if electrolito else muestra
        )
        salida_muestra = (
            salida / nombre_seguro_archivo(nombre_grupo_salida)
            if varias_muestras
            else salida
        )

        print("\n" + "-" * 72)
        print(f"Muestra/chip: {titulo_muestra}")
        print(f"Sensores detectados: {sensores}")
        print(f"Repeticiones detectadas: {repeticiones}")
        print(f"Carpeta específica: {salida_muestra}")

        comprobar_repeticiones(curvas_muestra, repeticiones)

        etiqueta_todas = (
            f"{len(repeticiones)} repeticiones "
            f"({', '.join(map(str, repeticiones))})"
        )
        generar_grupo(
            curvas_muestra,
            repeticiones,
            etiqueta_todas,
            "01_todas_repeticiones",
            salida_muestra,
            titulo_muestra,
        )

        etiqueta_ultimas = (
            f"últimas {len(ultimas)} repeticiones "
            f"({', '.join(map(str, ultimas))})"
        )
        generar_grupo(
            curvas_muestra,
            ultimas,
            etiqueta_ultimas,
            f"02_ultimas_{len(ultimas)}_repeticiones",
            salida_muestra,
            titulo_muestra,
        )

    guardar_configuracion(carpeta, salida)

    print("\n" + "=" * 72)
    print("HECHO")
    print(f"Las imágenes están en: {salida}")
    print("=" * 72)

    if MOSTRAR_FIGURAS:
        plt.show()

    if ABRIR_CARPETA_AL_TERMINAR and not args.no_abrir:
        abrir_carpeta(salida)




if __name__ == "__main__":
    main()
