"""Pruebas de los orquestadores de normalización."""

import os
import sys
import unittest
from unittest.mock import patch


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matecito.procesos.normalizacion import normalizar_archivo, normalizar_db


class JobFalso:
    def __init__(self):
        self.estado = "EN_CURSO"
        self.error = None
        self.log = []
        self.stats = {}
        self.csv_path = None
        self.tabla_resultado = None

    def escribir(self, mensaje):
        self.log.append(mensaje)

    def finalizar(self, estado):
        self.estado = estado


class ConexionFalsa:
    db_type = "oracle"


class JobNormalizacionTest(unittest.TestCase):
    def test_db_rechaza_lista_de_medios_vacia(self):
        job = JobFalso()
        normalizar_db(
            job,
            ConexionFalsa(),
            {
                "esquema": "DATOS",
                "tabla": "CLIENTES",
                "col_clave": "CUIT",
                "cols_medios": [],
                "usuario": "Ana",
            },
        )
        self.assertEqual("ERROR", job.estado)
        self.assertIn("al menos una columna", job.error)

    def test_archivo_rechaza_clave_inexistente(self):
        job = JobFalso()
        normalizar_archivo(job, [], [], None, [1], [], "clientes.csv")
        self.assertEqual("ERROR", job.estado)
        self.assertIn("columna clave", job.error)

    def test_archivo_rechaza_medios_inexistentes(self):
        job = JobFalso()
        normalizar_archivo(job, [], [], 0, [], [], "clientes.csv")
        self.assertEqual("ERROR", job.estado)
        self.assertIn("columna de medio", job.error)

    @patch("matecito.procesos.normalizacion._stats_y_csv")
    def test_archivo_normaliza_y_finaliza(self, completar):
        job = JobFalso()
        completar.side_effect = lambda trabajo, proceso, filas, nombre, est=None: (
            setattr(trabajo, "csv_path", "resultado.csv")
        )
        normalizar_archivo(
            job,
            [["20-1", "111|222", "zona norte"]],
            ["CUIT", "TELÉFONOS", "ZONA"],
            0,
            [1],
            [2],
            "clientes agosto.csv",
        )
        self.assertEqual("OK", job.estado)
        self.assertIsNone(job.error)
        args, kwargs = completar.call_args
        self.assertEqual("normalizacion", args[1])
        self.assertEqual(2, len(args[2]))
        self.assertTrue(args[3].startswith("CLIENTES_AGOSTO_NORM_"))
        self.assertEqual(2, kwargs["est"]["filas_normalizadas"])
        self.assertTrue(any("2 filas" in linea for linea in job.log))


if __name__ == "__main__":
    unittest.main()
