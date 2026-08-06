"""Pruebas de consultas de historial y progreso."""

import os
import sys
import tempfile
import unittest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matecito.nucleo.seguimiento import (
    entrada_historial,
    listar_procesos,
    obtener_progreso,
    resolver_csv,
)


class JobFalso:
    def __init__(self, job_id, entrada=None, snapshot=None, csv_path=None):
        self.id = job_id
        self._entrada = entrada or {"id": job_id}
        self._snapshot = snapshot or {"id": job_id, "estado": "EN_CURSO"}
        self.csv_path = csv_path

    def a_entrada(self):
        return self._entrada

    def snapshot(self, desde=0):
        return {**self._snapshot, "desde": desde}


class SeguimientoTest(unittest.TestCase):
    def test_busca_entrada_por_id(self):
        entradas = [{"id": "uno"}, {"id": "dos"}]
        self.assertEqual({"id": "dos"}, entrada_historial("dos", entradas))
        self.assertIsNone(entrada_historial("tres", entradas))

    def test_trabajo_activo_reemplaza_version_persistida(self):
        historica = {"id": "job", "estado": "EN_CURSO", "fecha_inicio": "1"}
        activa = {"id": "job", "estado": "OK", "fecha_inicio": "2", "log": []}
        resultado = listar_procesos(
            {"job": JobFalso("job", entrada=activa)}, [historica]
        )
        self.assertEqual("OK", resultado[0]["estado"])
        self.assertNotIn("log", resultado[0])

    def test_listado_ordena_mas_reciente_primero(self):
        entradas = [
            {"id": "viejo", "fecha_inicio": "2026-01-01"},
            {"id": "nuevo", "fecha_inicio": "2026-08-04"},
        ]
        self.assertEqual(
            ["nuevo", "viejo"],
            [fila["id"] for fila in listar_procesos({}, entradas)],
        )

    def test_progreso_activo_delega_snapshot(self):
        job = JobFalso("job", snapshot={"id": "job", "estado": "OK"})
        self.assertEqual(
            {"id": "job", "estado": "OK", "desde": 3},
            obtener_progreso("job", 3, {"job": job}, []),
        )

    def test_progreso_persistido_marca_reinicio(self):
        entrada = {
            "id": "job",
            "tipo": "mails",
            "estado": "EN_CURSO",
            "log": ["uno", "dos", "tres"],
            "stats": None,
        }
        resultado = obtener_progreso("job", 1, {}, [entrada])
        self.assertEqual("INTERRUMPIDO", resultado["estado"])
        self.assertEqual(["dos", "tres"], resultado["log"])
        self.assertEqual(3, resultado["total_log"])
        self.assertEqual({}, resultado["stats"])

    def test_progreso_inexistente_devuelve_none(self):
        self.assertIsNone(obtener_progreso("inexistente", jobs={}, entradas=[]))

    def test_resuelve_csv_activo_y_persistido(self):
        with tempfile.TemporaryDirectory() as directorio:
            activo = os.path.join(directorio, "activo.csv")
            persistido = os.path.join(directorio, "persistido.csv")
            open(activo, "w").close()
            open(persistido, "w").close()
            job = JobFalso("activo", csv_path=activo)
            self.assertEqual(activo, resolver_csv("activo", {"activo": job}, []))
            self.assertEqual(
                persistido,
                resolver_csv(
                    "viejo",
                    {},
                    [{"id": "viejo", "csv": "persistido.csv"}],
                    directorio,
                ),
            )

    def test_csv_persistido_no_admite_recorrer_directorios(self):
        with tempfile.TemporaryDirectory() as directorio:
            entrada = {"id": "job", "csv": "../fuera.csv"}
            self.assertIsNone(resolver_csv("job", {}, [entrada], directorio))


if __name__ == "__main__":
    unittest.main()
