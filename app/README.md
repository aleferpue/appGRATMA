# GRATMA Multipuerto

Script en Python utilizado para realizar **medidas I-V con varios dispositivos GRATMA al mismo tiempo** mediante distintos puertos serie.

Cada GRATMA funciona de forma independiente en su propio hilo, permitiendo medir varios chips en paralelo. Las medidas se realizan sobre los 8 sensores siguiendo un orden aleatorio y los resultados se guardan automáticamente en una carpeta diferente para cada chip.

## Requisitos

Para ejecutar el programa es necesario tener Python instalado junto con la librería:

```bash
pip install pyserial
```

Los principales módulos utilizados son:

- Serial
- Threading
- Random
- Time
- Datetime
- Argparse
- Os
- Re

## Parámetros de medida

Los principales parámetros de la medida se pueden modificar al principio del código:

```python
VD = 50
VGINIT = 0
VGEND = 1200
VGSWEEP = 15
FBWD = 1
NUM_REP = 5
```

Donde:

- **VD**: tensión de drain en mV.
- **VGINIT**: tensión inicial de gate en mV.
- **VGEND**: tensión final de gate en mV.
- **VGSWEEP**: paso de tensión utilizado durante el barrido en mV.
- **FBWD**: tipo de barrido.
  - `0`: solamente barrido forward.
  - `1`: barrido forward y backward.
- **NUM_REP**: número de secuencias completas sobre los 8 sensores.

También se pueden modificar los tiempos utilizados durante la medida:

```python
STABILIZE_S = 180
BETWEEN_SENSORS_S = 10
```

En este caso se realiza una estabilización inicial de **180 segundos** y una espera de **10 segundos entre sensores**.

## Carpeta de salida

La carpeta principal donde se guardan las medidas se define al comienzo del código:

```python
FOLDER_PATH = r"C:\Users\labor\Desktop\chips_aging"
```

Esta ruta debe modificarse dependiendo del ordenador donde se ejecute el programa.

Dentro de esta carpeta se crea automáticamente una carpeta diferente para cada chip:

```text
chips_aging/
├── FxCy_aging/
├── FwCz_aging/
└── ...
```

## Ejecución del programa

El programa puede ejecutarse directamente desde una terminal:

```bash
python GRATMA_multipuerto.py
```

Si no se introducen argumentos, el programa pregunta por terminal:

1. Número de equipos que se van a medir.
2. Puerto COM de cada equipo.
3. Nombre del wafer.
4. Nombre del chip.

Por ejemplo:

```text
Número de equipos a medir en paralelo: 2

--- Equipo 1/2 ---
Puerto COM: COM8
Nombre del wafer: USAGRAPH1
Código del chip: F5C9

--- Equipo 2/2 ---
Puerto COM: COM9
Nombre del wafer: USAGRAPH1
Código del chip: F5C10
```

También es posible introducir directamente los dispositivos al ejecutar el programa:

```bash
python GRATMA_multipuerto.py --device COM8:USAGRAPH1:F5C9 --device COM9:USAGRAPH1:F5C10
```

Si solamente se utiliza un GRATMA, también se puede ejecutar de la siguiente forma:

```bash
python GRATMA_multipuerto.py --port COM8 --wafer USAGRAPH1 --chip F5C9
```

## Funcionamiento de la medida

Una vez iniciado el programa:

1. Se abren los puertos serie de los equipos.
2. Se crea una carpeta de salida para cada chip.
3. Se envía el comando `um 1` para poner a tierra los sensores que no se están midiendo.
4. Se realiza el tiempo de estabilización inicial.
5. Todos los GRATMA comienzan las medidas en paralelo.
6. Cada equipo mide los 8 sensores siguiendo un orden aleatorio.
7. El proceso se repite según el valor definido en `NUM_REP`.
8. Los resultados se guardan automáticamente.

El orden de los sensores cambia en cada secuencia. Además, el programa intenta alternar entre los sensores 1–4 y 5–8 para evitar medir siempre sensores de la misma zona de forma consecutiva.

## Archivos generados

Los archivos finales siguen el formato:

```text
Wafer_Chip_aging_ArrayN_random_Secuencia_Electrolito.txt
```

Por ejemplo:

```text
USAGRAPH1_F5C9_aging_Array3_random_2_PB-S0_01.txt
```

Al comienzo de cada archivo se guarda información sobre la configuración utilizada durante la medida, como el chip, wafer, sensor, secuencia, tensiones utilizadas o puertos que estaban funcionando en paralelo.

Después se guardan los datos de la medida con las columnas:

```text
Vfg;Vs;Ig;Is
```

Los valores de **Vfg, Vs, Ig e Is se obtienen directamente de la información devuelta por el GRATMA**.

Durante la medida también se genera un archivo temporal cuyo nombre comienza por:

```text
All_info_
```

Este archivo contiene toda la información recibida desde el GRATMA por el puerto serie y se mantiene para poder revisar los datos en caso de que haya algún problema.

## Medidas en paralelo

Cada GRATMA se ejecuta en un hilo diferente, por lo que varios dispositivos conectados a distintos puertos COM pueden realizar las medidas al mismo tiempo.

En la terminal, los mensajes de cada dispositivo aparecen identificados con su puerto:

```text
[COM8] ...
[COM9] ...
```

De esta forma es más fácil seguir el estado de cada medida cuando se están utilizando varios dispositivos.

El programa tampoco permite configurar dos veces el mismo puerto COM ni utilizar el mismo nombre de chip para dos equipos diferentes.

## Notas

- Comprobar los puertos COM antes de comenzar la medida.
- Modificar `FOLDER_PATH` si el programa se utiliza en otro ordenador.
- Comprobar que ningún otro programa esté utilizando los puertos serie.
- Si no se puede generar correctamente el TXT final, se conserva el archivo `All_info_` para poder revisar los datos originales.
- Al terminar todas las medidas, el programa muestra un resumen con los archivos guardados y el estado de cada dispositivo.
