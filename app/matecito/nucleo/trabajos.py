"""Trabajos en segundo plano y registro de ejecuciones activas."""

import os
import threading
import uuid
from datetime import datetime

from matecito.nucleo.persistencia import persistir_en_historial


class Job:
    """Estado observable de un proceso ejecutado en segundo plano."""

    def __init__(self, tipo, origen="db", descripcion="", usuario="", cliente=""):
        self.id = uuid.uuid4().hex[:12]
        self.tipo = tipo
        self.origen = origen
        self.descripcion = descripcion
        self.usuario = usuario
        self.cliente = cliente
        self.fecha_inicio = datetime.now().isoformat(timespec="seconds")
        self.fecha_fin = None
        self.estado = "EN_CURSO"
        self.log = []
        self.stats = {}
        self.tabla_resultado = None
        self.csv_path = None
        self.error = None
        self._lock = threading.Lock()
        persistir_en_historial(self.a_entrada())

    def escribir(self, mensaje):
        with self._lock:
            hora = datetime.now().strftime("%H:%M:%S")
            self.log.append(f"{hora}  {mensaje}")

    def finalizar(self, estado):
        self.estado = estado
        self.fecha_fin = datetime.now().isoformat(timespec="seconds")
        persistir_en_historial(self.a_entrada())

    def a_entrada(self):
        """Representación completa utilizada por la persistencia histórica."""
        with self._lock:
            return {
                "id": self.id,
                "tipo": self.tipo,
                "origen": self.origen,
                "descripcion": self.descripcion,
                "usuario": self.usuario,
                "cliente": self.cliente,
                "fecha_inicio": self.fecha_inicio,
                "fecha_fin": self.fecha_fin,
                "estado": self.estado,
                "stats": self.stats,
                "tabla_resultado": self.tabla_resultado,
                "csv": os.path.basename(self.csv_path) if self.csv_path else None,
                "error": self.error,
                "log": list(self.log),
            }

    def snapshot(self, desde=0):
        """Vista incremental consumida por el polling del frontend."""
        with self._lock:
            return {
                "id": self.id,
                "tipo": self.tipo,
                "estado": self.estado,
                "descripcion": self.descripcion,
                "fecha_inicio": self.fecha_inicio,
                "log": self.log[desde:],
                "total_log": len(self.log),
                "stats": self.stats,
                "tabla_resultado": self.tabla_resultado,
                "tiene_csv": bool(self.csv_path),
                "error": self.error,
            }


JOBS = {}
