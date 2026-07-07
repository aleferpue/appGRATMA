"""
gratma_usb.py — Librería Python para comunicación con el dispositivo GRATMA
           vía USB Vendor (Interface 2, EP3 Bulk IN/OUT).

Requisitos:
    pip install pyusb libusb

Backend: libusb-1.0.  Se detecta automáticamente en este orden:
  1. libusb-1.0.dll en el PATH del sistema o en la carpeta del script.
  2. DLL incluida en el paquete PyPI 'libusb' (pip install libusb).
  3. libusb0 como último recurso.

El driver WinUSB debe estar instalado en la interfaz 2 del dispositivo.
Ver: drv_windows/gratma_winusb.inf  o usar Zadig → Interface 2 → WinUSB.

Protocolo (host -> device):
    Byte 0 : CMD
    Byte 1 : LEN  (bytes de payload, 0..62)
    Byte 2+: PAYLOAD

Protocolo (device -> host):
    Byte 0 : CMD | 0x80
    Byte 1 : STATUS  (0=OK, 1=ERROR, 2=BUSY, 3=UNKNOWN_CMD)
    Byte 2+: DATA
"""

import os
import platform
import struct
import time
import usb.core
import usb.util
import usb.backend.libusb1
import usb.backend.libusb0


# ---------------------------------------------------------------------------
# Resolución automática del backend libusb-1.0
# ---------------------------------------------------------------------------
def _find_libusb1_backend():
    """
    Devuelve un backend libusb-1.0 funcional o None.

    Orden de búsqueda:
      1. DLL del sistema (PATH, System32, script dir) — búsqueda nativa de pyusb.
      2. DLL dentro del paquete PyPI 'libusb' (pip install libusb).
    """
    # Intento 1: búsqueda nativa de pyusb (sistema / PATH)
    backend = usb.backend.libusb1.get_backend()
    if backend:
        return backend

    # Intento 2: paquete PyPI 'libusb'
    try:
        import libusb as _libusb_pkg
        _pkg_dir = os.path.dirname(_libusb_pkg.__file__)
        _machine = platform.machine().lower()
        _arch = (
            "arm64" if _machine in ("arm64", "aarch64")
            else "x86_64" if _machine in ("amd64", "x86_64")
            else "x86"
        )
        _dll = os.path.join(_pkg_dir, "_platform", "windows", _arch, "libusb-1.0.dll")
        if os.path.isfile(_dll):
            backend = usb.backend.libusb1.get_backend(find_library=lambda _x: _dll)
            if backend:
                return backend
    except ImportError:
        pass

    return None


_LIBUSB1_BACKEND = _find_libusb1_backend()
_LIBUSB0_BACKEND = usb.backend.libusb0.get_backend() if _LIBUSB1_BACKEND is None else None

# Backend a usar en todas las llamadas a usb.core.find / usb.core.Device
_USB_BACKEND = _LIBUSB1_BACKEND or _LIBUSB0_BACKEND

# ---------------------------------------------------------------------------
# Constantes del dispositivo
# ---------------------------------------------------------------------------
GRATMA_VID       = 0x04D8
GRATMA_PID       = 0xAAAA
VENDOR_INTERFACE = 2
EP_OUT           = 0x03          # EP3 OUT (host -> device)
EP_IN            = 0x83          # EP3 IN  (device -> host), bit 7 = direction IN
EP_MAX_PKT       = 64
USB_TIMEOUT_MS   = 2000          # Timeout por defecto para transferencias

# ---------------------------------------------------------------------------
# Comandos (deben coincidir con usb_vendor.h del firmware)
# ---------------------------------------------------------------------------
class Cmd:
    PING             = 0x01
    START_SWEEP      = 0x02
    GET_STATUS       = 0x03
    GET_RESULT       = 0x04
    START_SWEEP_EX   = 0x05
    START_IDT        = 0x06
    START_TEST       = 0x07
    STOP             = 0x08
    SET_VOLTAGE      = 0x10
    SET_VREF         = 0x11
    SET_GAIN         = 0x12
    SET_SWITCH       = 0x13
    GET_VBUS         = 0x20
    GET_VSHUNT       = 0x21
    GET_TEMP         = 0x22
    GET_DATA_COUNT   = 0x30
    GET_DATA         = 0x31

# ---------------------------------------------------------------------------
# Status codes devueltos por el dispositivo
# ---------------------------------------------------------------------------
class Status:
    OK          = 0x00
    ERROR       = 0x01
    BUSY        = 0x02
    UNKNOWN_CMD = 0x03

# ---------------------------------------------------------------------------
# Device status (devuelto por GET_STATUS)
# ---------------------------------------------------------------------------
class DeviceStatus:
    IDLE     = 0x00
    SWEEPING = 0x01
    ERROR    = 0x02

# ---------------------------------------------------------------------------
# Tipos de registro de datos de medición
# ---------------------------------------------------------------------------
class RecordType:
    SWEEP_POINT = 0x01   # (Vg, Is, Vs, Ig) — un punto del sweep
    SWEEP_END   = 0x02   # fin del sweep (seq=total_points, v1..v4=0)
    IDT_SAMPLE  = 0x03   # (Vs_bus, Is, Vg, Ig) — una muestra IDT
    IDT_END     = 0x04   # fin del IDT (seq=total_samples, v1..v4=0)

RECORD_SIZE = 23  # bytes por registro en el wire format
RECORDS_PER_PACKET = 2  # registros que caben en un paquete USB de 64 bytes

# Bit 7 del byte 'sensor' del registro: el punto pertenece a la fase backward
SENSOR_BACKWARD_FLAG = 0x80

# ---------------------------------------------------------------------------
# Excepciones
# ---------------------------------------------------------------------------
class GratmaError(Exception):
    """Error de comunicación o de protocolo con el dispositivo GRATMA."""

class GratmaDeviceError(GratmaError):
    """El dispositivo devolvió STATUS=ERROR."""

class GratmaDeviceBusy(GratmaError):
    """El dispositivo devolvió STATUS=BUSY."""

# ---------------------------------------------------------------------------
# Clase principal
# ---------------------------------------------------------------------------
class GratmaUSB:
    """
    Cliente USB para el dispositivo GRATMA.

    Uso básico:
        with GratmaUSB() as dev:
            dev.ping()
            dev.start_sweep()
            while dev.get_status() == DeviceStatus.SWEEPING:
                time.sleep(0.5)
            records = dev.drain_data()
    """

    def __init__(self, vid: int = GRATMA_VID, pid: int = GRATMA_PID,
                 timeout_ms: int = USB_TIMEOUT_MS):
        self._vid = vid
        self._pid = pid
        self._timeout = timeout_ms
        self._dev: usb.core.Device | None = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------
    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *_):
        self.close()

    # ------------------------------------------------------------------
    # Conexión / desconexión
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Enumeración de dispositivos disponibles
    # ------------------------------------------------------------------
    @staticmethod
    def scan(vid: int = GRATMA_VID, pid: int = GRATMA_PID) -> list[dict]:
        """
        Devuelve la lista de dispositivos GRATMA conectados.
        Cada entrada es un dict con claves:
          'device'    : usb.core.Device
          'bus'       : int
          'address'   : int
          'bcdDevice' : int
          'serial'    : str | None
          'label'     : str  (texto listo para mostrar en UI)
        """
        if _USB_BACKEND is None:
            return []
        found = usb.core.find(idVendor=vid, idProduct=pid,
                              find_all=True, backend=_USB_BACKEND)
        result = []
        for dev in (found or []):
            serial = None
            try:
                if dev.iSerialNumber:
                    serial = usb.util.get_string(dev, dev.iSerialNumber)
            except Exception:
                pass
            label = f"GRATMA  bus={dev.bus} addr={dev.address}"
            if serial:
                label += f"  s/n {serial}"
            label += f"  [rev {dev.bcdDevice:04X}]"
            result.append({
                'device':    dev,
                'bus':       dev.bus,
                'address':   dev.address,
                'bcdDevice': dev.bcdDevice,
                'serial':    serial,
                'label':     label,
            })
        return result

    def open(self, device: 'usb.core.Device | None' = None) -> None:
        """
        Abre el dispositivo GRATMA.
        Si se pasa 'device' (de scan()), usa ese handle directamente.
        Si no, busca el primero disponible por VID/PID.
        Lanza GratmaError si no se encuentra o no se puede reclamar Interface 2.
        """
        if _USB_BACKEND is None:
            raise GratmaError(
                "No se encontró ningún backend libusb disponible. "
                "Ejecutar: pip install libusb"
            )

        # Reintentos: Windows puede tardar un momento en activar el handle
        # WinUSB tras instalar el driver o tras reconectar el dispositivo.
        last_exc: Exception | None = None
        for attempt in range(3):
            if device is not None:
                dev = device
            else:
                dev = usb.core.find(idVendor=self._vid, idProduct=self._pid,
                                    backend=_USB_BACKEND)
            if dev is None:
                raise GratmaError(
                    f"Dispositivo GRATMA no encontrado "
                    f"(VID=0x{self._vid:04X} PID=0x{self._pid:04X}). "
                    "Verificar conexión USB y driver WinUSB en Interface 2."
                )
            try:
                # En Windows con WinUSB sobre dispositivo compuesto NO llamar
                # set_configuration(): causaría un reset USB del dispositivo.
                usb.util.claim_interface(dev, VENDOR_INTERFACE)
                break   # éxito
            except usb.core.USBError as e:
                last_exc = e
                usb.util.dispose_resources(dev)
                if getattr(e, 'errno', None) != 2 or attempt == 2:
                    raise GratmaError(
                        f"No se pudo reclamar Interface {VENDOR_INTERFACE}: {e}"
                    ) from e
                time.sleep(0.8)
                device = None   # forzar re-find en siguiente intento

        # SET_INTERFACE(2, altsetting=0): reinicia data toggles y pipes.
        self._reinit_interface(dev)
        self._dev = dev

    def _reinit_interface(self, dev=None) -> None:
        """Envía SET_INTERFACE(2,0) para reinicializar los pipes de la interfaz vendor."""
        target = dev if dev is not None else self._dev
        if target is None:
            return
        try:
            target.set_interface_altsetting(
                interface=VENDOR_INTERFACE, alternate_setting=0
            )
            time.sleep(0.05)   # Margen para que el firmware procese el request
        except usb.core.USBError:
            # Algunos firmwares no responden a SET_INTERFACE si solo hay altsetting 0;
            # en ese caso el host-side WinUSB aún puede reiniciar sus pipe handles.
            pass

    def close(self) -> None:
        """Libera la interfaz y el dispositivo."""
        if self._dev is not None:
            try:
                usb.util.release_interface(self._dev, VENDOR_INTERFACE)
                usb.util.dispose_resources(self._dev)
            except Exception:
                pass
            self._dev = None

    @property
    def is_open(self) -> bool:
        return self._dev is not None

    # ------------------------------------------------------------------
    # Capa de transporte (raw)
    # ------------------------------------------------------------------
    def _send(self, cmd: int, payload: bytes = b"") -> None:
        """Construye y envía un paquete [CMD][LEN][PAYLOAD...]."""
        if len(payload) > EP_MAX_PKT - 2:
            raise GratmaError(f"Payload demasiado grande: {len(payload)} bytes (máximo 62)")
        pkt = bytes([cmd, len(payload)]) + payload
        try:
            self._dev.write(EP_OUT, pkt, timeout=self._timeout)
        except usb.core.USBError as e:
            if getattr(e, 'errno', None) == 32:   # Pipe error
                # Recuperación: reinicializar la interfaz con SET_INTERFACE
                # (más efectivo que CLEAR_HALT en WinUSB) y reintentar una vez.
                self._reinit_interface()
                self._dev.write(EP_OUT, pkt, timeout=self._timeout)
            else:
                raise

    def _recv(self, expected_cmd: int) -> tuple[int, bytes]:
        """
        Lee un paquete de respuesta.
        Devuelve (status, data_bytes).
        Lanza GratmaDeviceError / GratmaDeviceBusy según el status recibido.
        """
        try:
            raw = self._dev.read(EP_IN, EP_MAX_PKT, timeout=self._timeout)
        except usb.core.USBError as e:
            if getattr(e, 'errno', None) == 32:   # Pipe error
                self._reinit_interface()
                raise GratmaError(
                    f"Pipe error en lectura EP_IN (CMD=0x{expected_cmd:02X}). "
                    "Interfaz reinicializada; reintentar el comando."
                ) from e
            raise
        if len(raw) < 2:
            raise GratmaError(f"Respuesta demasiado corta: {len(raw)} bytes")

        reply_cmd = raw[0]
        status    = raw[1]
        data      = bytes(raw[2:]) if len(raw) > 2 else b""

        expected_reply = (expected_cmd | 0x80) & 0xFF
        if reply_cmd != expected_reply:
            raise GratmaError(
                f"CMD de respuesta inesperado: 0x{reply_cmd:02X} "
                f"(esperado 0x{expected_reply:02X})"
            )

        if status == Status.ERROR:
            raise GratmaDeviceError(f"El dispositivo devolvió ERROR para CMD=0x{expected_cmd:02X}")
        if status == Status.BUSY:
            raise GratmaDeviceBusy(f"El dispositivo está ocupado (CMD=0x{expected_cmd:02X})")
        if status == Status.UNKNOWN_CMD:
            raise GratmaError(f"Comando desconocido por el dispositivo: 0x{expected_cmd:02X}")

        return status, data

    def _cmd(self, cmd: int, payload: bytes = b"") -> bytes:
        """Envía un comando y recibe la respuesta. Devuelve los bytes de datos."""
        self._send(cmd, payload)
        _, data = self._recv(cmd)
        return data

    # ------------------------------------------------------------------
    # Comandos de alto nivel
    # ------------------------------------------------------------------

    def ping(self) -> None:
        """Verifica la comunicación con el dispositivo."""
        self._cmd(Cmd.PING)

    def get_status(self) -> int:
        """
        Devuelve el estado del dispositivo (DeviceStatus.*).
        0 = IDLE, 1 = SWEEPING, 2 = ERROR
        """
        data = self._cmd(Cmd.GET_STATUS)
        return data[0] if data else DeviceStatus.ERROR

    def get_result(self) -> float:
        """Devuelve el último resultado de medición (Vg mínimo global, en Voltios)."""
        data = self._cmd(Cmd.GET_RESULT)
        (value,) = struct.unpack_from("<f", data, 0)
        return value

    def start_sweep(self) -> None:
        """Inicia un sweep con los parámetros por defecto del firmware."""
        self._cmd(Cmd.START_SWEEP)

    def start_sweep_ex(
        self,
        vs_mv: int,
        vg_start_mv: int,
        vg_end_mv: int,
        vg_step_mv: int,
        sensors: int = 0xFF,
        reverse: bool = False,
        repetitions: int = 1,
        parallel: bool = False,
    ) -> None:
        """
        Inicia un sweep con parámetros personalizados.

        Args:
            vs_mv:        Tensión VS en mV (0..4000)
            vg_start_mv:  Tensión VG inicial en mV
            vg_end_mv:    Tensión VG final en mV
            vg_step_mv:   Paso VG en mV (>= 1)
            sensors:      Máscara de sensores activos (0x01..0xFF)
            reverse:      True para sweep inverso
            repetitions:  Número de repeticiones (1..20)
            parallel:     True para medir todos los sensores a la vez (mapa de
                          switches combinado); False = uno a uno (secuencial)
        """
        payload = struct.pack(
            "<hhhh BBB B",
            vs_mv, vg_start_mv, vg_end_mv, vg_step_mv,
            sensors & 0xFF,
            1 if reverse else 0,
            repetitions & 0xFF,
            1 if parallel else 0,
        )
        self._cmd(Cmd.START_SWEEP_EX, payload)

    def start_idt(
        self,
        sensor: int,
        vg_mv: int,
        vs_mv: int,
        total_s: int,
        period_s: int,
        parallel: bool = False,
        sensors_mask: int = 0,
    ) -> None:
        """
        Inicia una medición IDT (corriente en función del tiempo).

        Args:
            sensor:       Índice del sensor (0-based), usado si parallel=False
            vg_mv:        Tensión VG en mV
            vs_mv:        Tensión VS en mV
            total_s:      Duración total en segundos
            period_s:     Período de muestreo en segundos
            parallel:     True para medir todos los sensores de sensors_mask a la vez
            sensors_mask: Máscara de sensores (usada si parallel=True)
        """
        payload = struct.pack("<B hh II BB", sensor, vg_mv, vs_mv, total_s, period_s,
                              1 if parallel else 0, sensors_mask & 0xFF)
        self._cmd(Cmd.START_IDT, payload)

    def start_test(self) -> None:
        """Inicia el modo de test del firmware."""
        self._cmd(Cmd.START_TEST)

    def stop(self) -> None:
        """Detiene la medición en curso (sweep / IDT / test)."""
        self._cmd(Cmd.STOP)

    # -- DAC / Switch control -----------------------------------------------

    def set_voltage(self, dac: int, out: int, mv: int) -> None:
        """
        Establece la tensión de salida de un DAC.

        Args:
            dac: Índice del DAC (0=VG, 1=VS)
            out: Canal de salida del DAC (0 o 1)
            mv:  Tensión en mV (signed, 0..4000)
        """
        payload = struct.pack("<BBh", dac, out, mv)
        self._cmd(Cmd.SET_VOLTAGE, payload)

    def set_vref(self, dac: int, out: int, mode: int) -> None:
        """Configura la referencia de tensión de un canal DAC."""
        payload = struct.pack("<BBB", dac, out, mode)
        self._cmd(Cmd.SET_VREF, payload)

    def set_gain(self, dac: int, out: int, gain: int) -> None:
        """Configura la ganancia de un canal DAC."""
        payload = struct.pack("<BBB", dac, out, gain)
        self._cmd(Cmd.SET_GAIN, payload)

    def set_switch(self, sw: int, sw_map: int) -> None:
        """
        Configura el mapa de conmutación de un MAX14662.

        Args:
            sw:     Índice del switch (0 o 1)
            sw_map: Mapa de 8 bits (bit N = canal N activo)
        """
        payload = struct.pack("<BB", sw, sw_map & 0xFF)
        self._cmd(Cmd.SET_SWITCH, payload)

    # -- Lecturas de instrumentos (asíncronas en firmware) ------------------

    def get_vbus(self, n: int) -> float:
        """
        Lee la tensión de bus del INA228 n (0=VS, 1=VG).
        Devuelve voltios (float32).
        Nota: la respuesta es asíncrona en el firmware; puede tardar ~5 ms.
        """
        data = self._cmd(Cmd.GET_VBUS, bytes([n & 0xFF]))
        (value,) = struct.unpack_from("<f", data, 0)
        return value

    def get_vshunt(self, n: int) -> float:
        """Lee la tensión de shunt del INA228 n. Devuelve voltios."""
        data = self._cmd(Cmd.GET_VSHUNT, bytes([n & 0xFF]))
        (value,) = struct.unpack_from("<f", data, 0)
        return value

    def get_temp(self, n: int) -> float:
        """Lee la temperatura del die del INA228 n. Devuelve °C."""
        data = self._cmd(Cmd.GET_TEMP, bytes([n & 0xFF]))
        (value,) = struct.unpack_from("<f", data, 0)
        return value

    # -- Streaming de datos de medición --------------------------------------

    def get_data_count(self) -> int:
        """Devuelve el número de registros pendientes en el buffer del firmware."""
        data = self._cmd(Cmd.GET_DATA_COUNT)
        (count,) = struct.unpack_from("<H", data, 0)
        return count

    def get_data(self, max_records: int = RECORDS_PER_PACKET) -> list[dict]:
        """
        Descarga hasta max_records (1..2) del buffer del firmware.

        Devuelve una lista de dicts con campos:
            type     : int   (RecordType.*)
            sensor   : int   (1-based, 0 si N/A o grupo paralelo)
            backward : bool  (True si el punto pertenece a la fase backward del sweep)
            rep      : int   (nº de repetición 1-based; 0 si N/A)
            seq      : int   (punto o elapsed_ms)
            v1       : float (sweep: Vg [V];  IDT: Vs_bus [V])
            v2       : float (sweep: Is [A];  IDT: Is [A])
            v3       : float (sweep: Vs [V];  IDT: Vg [V])
            v4       : float (sweep: Ig [A];  IDT: Ig [A])
        """
        max_records = max(1, min(RECORDS_PER_PACKET, max_records))
        data = self._cmd(Cmd.GET_DATA, bytes([max_records]))

        records = []
        offset = 0
        while offset + RECORD_SIZE <= len(data):
            rec_type, sensor_raw, rep, seq, v1, v2, v3, v4 = struct.unpack_from(
                "<BBBIffff", data, offset)
            records.append({
                "type":     rec_type,
                "sensor":   sensor_raw & ~SENSOR_BACKWARD_FLAG,
                "backward": bool(sensor_raw & SENSOR_BACKWARD_FLAG),
                "rep":      rep,
                "seq":      seq,
                "v1":       v1,
                "v2":       v2,
                "v3":       v3,
                "v4":       v4,
            })
            offset += RECORD_SIZE

        return records

    def drain_data(self, poll_interval_s: float = 0.1) -> list[dict]:
        """
        Espera hasta que el sweep/IDT termine y descarga todos los registros.

        Devuelve la lista completa de registros (todos los SWEEP_POINT / IDT_SAMPLE).
        Lanza GratmaError si el dispositivo entra en estado ERROR.

        Uso:
            dev.start_sweep_ex(vs_mv=100, vg_start_mv=0, vg_end_mv=1200,
                               vg_step_mv=50, sensors=0x01)
            records = dev.drain_data()
        """
        all_records: list[dict] = []
        finished = False

        while not finished:
            # Leer hasta vaciar el buffer
            while True:
                count = self.get_data_count()
                if count == 0:
                    break
                batch = self.get_data(4)
                all_records.extend(batch)
                # Detectar marca de fin
                for r in batch:
                    if r["type"] in (RecordType.SWEEP_END, RecordType.IDT_END):
                        finished = True

            if not finished:
                status = self.get_status()
                if status == DeviceStatus.ERROR:
                    raise GratmaDeviceError("El dispositivo entró en estado ERROR durante la medición")
                if status == DeviceStatus.IDLE:
                    # El dispositivo terminó pero puede quedar algún dato en buffer
                    # Hacer un último drenado
                    while True:
                        count = self.get_data_count()
                        if count == 0:
                            break
                        batch = self.get_data(4)
                        all_records.extend(batch)
                    finished = True
                else:
                    time.sleep(poll_interval_s)

        return all_records
