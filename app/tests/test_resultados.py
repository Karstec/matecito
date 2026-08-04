"""Pruebas del armado y exportación de resultados."""

import csv
import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matecito.nucleo.resultados import (
    calcular_estadisticas,
    completar_resultado,
    guardar_csv_resultados,
    nombre_tabla_resultado,
    sanitizar_identificador,
)


class NombreResultadoTest(unittest.TestCase):
    def test_sanitiza_acentos_espacios_y_simbolos(self):
        self.assertEqual("JOSE_PEREZ_S_A", sanitizar_identificador(" José Pérez S.A. "))

    def test_nombre_incluye_usuario_cliente_y_timestamp(self):
        ahora = datetime(2026, 8, 4, 16, 30, 5)
        nombre = nombre_tabla_resultado("Ana", "Cliente Sur", ahora=ahora)
        self.assertEqual("ANA_CLIENTE_SUR_20260804_163005", nombre)

    def test_oracle_limita_el_nombre_sin_perder_timestamp(self):
        ahora = datetime(2026, 8, 4, 16, 30, 5)
        nombre = nombre_tabla_resultado(
            "USUARIO_MUY_EXTENSO", "CLIENTE_MUY_EXTENSO", "oracle", ahora
        )
        self.assertEqual(30, len(nombre))
        self.assertTrue(nombre.endswith("_20260804_163005"))


class EstadisticasResultadoTest(unittest.TestCase):
    def test_estadisticas_de_mails(self):
        filas = [
            {"ESTADO": "CONSERVADO"},
            {"ESTADO": "MODIFICADO"},
            {"ESTADO": "BAJA"},
            {"ESTADO": "REVISION MANUAL"},
        ]
        self.assertEqual(
            {
                "total": 4,
                "conservados": 1,
                "modificados": 1,
                "bajas": 1,
                "revision_manual": 1,
            },
            calcular_estadisticas("mails", filas),
        )

    def test_estadisticas_de_telefonos(self):
        filas = [
            {"VALIDO": 1, "TIPO_LINEA": "MOVIL"},
            {"VALIDO": 0, "TIPO_LINEA": "FIJO"},
        ]
        self.assertEqual(
            {"total": 2, "validos": 1, "bajas": 1, "moviles": 1, "fijos": 1},
            calcular_estadisticas("telefonos", filas),
        )

    def test_normalizacion_usa_estadisticas_recibidas(self):
        stats = calcular_estadisticas(
            "normalizacion",
            [{"CUIT": "1"}, {"CUIT": "2"}],
            {"claves_unicas": 2, "valores_totales": 5},
        )
        self.assertEqual({"total": 2, "cuit_unicos": 2, "medios": 5}, stats)


class CsvResultadoTest(unittest.TestCase):
    def test_guarda_csv_utf8_con_bom(self):
        with tempfile.TemporaryDirectory() as directorio:
            path = guardar_csv_resultados(
                [{"ID": "1", "NOMBRE": "José"}], "PRUEBA", directorio
            )
            with open(path, "rb") as archivo:
                self.assertTrue(archivo.read().startswith(b"\xef\xbb\xbf"))
            with open(path, encoding="utf-8-sig", newline="") as archivo:
                self.assertEqual(
                    [{"ID": "1", "NOMBRE": "José"}],
                    list(csv.DictReader(archivo)),
                )

    def test_no_crea_csv_para_resultado_vacio(self):
        with tempfile.TemporaryDirectory() as directorio:
            self.assertIsNone(guardar_csv_resultados([], "VACIO", directorio))
            self.assertEqual([], os.listdir(directorio))

    def test_completar_resultado_actualiza_el_job(self):
        class JobFalso:
            stats = {}
            csv_path = None

        job = JobFalso()
        with tempfile.TemporaryDirectory() as directorio:
            completar_resultado(
                job,
                "mails",
                [{"ESTADO": "CONSERVADO"}],
                "JOB",
                directorio=directorio,
            )
            self.assertEqual(1, job.stats["conservados"])
            self.assertTrue(os.path.isfile(job.csv_path))


if __name__ == "__main__":
    unittest.main()
