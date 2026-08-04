"""Pruebas de los orquestadores extraídos de ``app.py``."""

import os
import sys
import threading
import unittest
from unittest.mock import patch


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matecito.procesos.archivos import procesar_archivo
from matecito.procesos.base_datos import procesar_db
from matecito.procesos.padron import cuitificar_lotes, validar_denominacion_lotes


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


class ConexionVacia:
    db_type = "oracle"

    def fetchall(self, sql, params=None):
        return [(0,)]


class CursorFalso:
    def __init__(self):
        self.insertadas = []
        self.cerrado = False

    def execute(self, sql):
        self.ultimo_sql = sql

    def executemany(self, sql, lote):
        self.insertadas.extend(lote)

    def fetchone(self):
        return (len(self.insertadas),)

    def close(self):
        self.cerrado = True


class ConnFalsa:
    def __init__(self):
        self.cursor_falso = CursorFalso()
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_falso

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class ConexionCompleta:
    db_type = "oracle"

    def __init__(self):
        self.conn = ConnFalsa()
        self.lock = threading.Lock()

    def fetchall(self, sql, params=None):
        if "COUNT(*)" in sql:
            return [(1,)]
        return [("Ana", "Ana")]


class JobsPadronTest(unittest.TestCase):
    def parametros(self):
        return {
            "esquema": "DATOS",
            "tabla": "CLIENTES",
            "col_id": "CUIT",
            "col_dato": "NOMBRE",
            "usuario": "Ana",
        }

    def test_cuitificacion_rechaza_tabla_vacia(self):
        job = JobFalso()
        cuitificar_lotes(job, ConexionVacia(), self.parametros())
        self.assertEqual("ERROR", job.estado)
        self.assertIn("no tiene registros", job.error)

    def test_validacion_rechaza_tabla_vacia(self):
        job = JobFalso()
        validar_denominacion_lotes(job, ConexionVacia(), self.parametros())
        self.assertEqual("ERROR", job.estado)
        self.assertIn("no tiene registros", job.error)


class JobsGenericosTest(unittest.TestCase):
    @patch("matecito.procesos.base_datos._stats_y_csv")
    def test_base_datos_completa_transaccion(self, completar):
        job = JobFalso()
        cx = ConexionCompleta()
        procesar_db(
            job,
            cx,
            {
                "proceso": "denominacion",
                "esquema": "DATOS",
                "tabla": "CLIENTES",
                "col_id": "NOMBRE_A",
                "col_dato": "NOMBRE_B",
                "usuario": "Ana",
                "umbral": 80,
            },
        )
        self.assertEqual("OK", job.estado)
        self.assertEqual(1, cx.conn.commits)
        self.assertEqual(0, cx.conn.rollbacks)
        self.assertEqual(1, len(cx.conn.cursor_falso.insertadas))
        completar.assert_called_once()

    @patch("matecito.procesos.archivos._stats_y_csv")
    def test_archivo_completa_proceso_de_denominacion(self, completar):
        completar.side_effect = lambda job, *args, **kwargs: setattr(
            job, "csv_path", "resultado.csv"
        )
        job = JobFalso()
        procesar_archivo(
            job, "denominacion", [["Ana", "Ana"]], ["A", "B"],
            0, 1, "clientes.csv", "AR",
        )
        self.assertEqual("OK", job.estado)
        self.assertTrue(any("CSV de resultados" in linea for linea in job.log))

    @patch("matecito.procesos.archivos._stats_y_csv")
    @patch("matecito.procesos.archivos.consultar_padron", return_value=({}, {}))
    @patch("matecito.procesos.archivos.armar_claves", return_value=([""], ["12345678"]))
    @patch("matecito.procesos.archivos.abrir_padron")
    def test_archivo_cuit_usa_tipo_busqueda_recibido(
        self, abrir, armar, consultar, completar
    ):
        class PadronFalso:
            def cerrar(self):
                pass

        abrir.return_value = PadronFalso()
        completar.side_effect = lambda job, *args, **kwargs: setattr(
            job, "csv_path", "resultado.csv"
        )
        with patch(
            "matecito.validadores.cuit.validar_cuit_y_denominacion",
            return_value={
                "ESTADO_VALIDACION": "NO ENCONTRADO",
                "_candidatos": [],
            },
        ), patch(
            "matecito.validadores.cuit.estadisticas",
            return_value={
                "total": 1,
                "validados": 0,
                "solo_cuit": 0,
                "solo_denom": 0,
                "no_coincide": 0,
                "no_encontrados": 1,
            },
        ):
            job = JobFalso()
            procesar_archivo(
                job, "cuit", [["12345678", "Ana"]], ["DNI", "NOMBRE"],
                0, 1, "clientes.csv", "AR", tipo_busqueda="dni",
            )
        self.assertEqual("OK", job.estado)
        self.assertEqual("dni", armar.call_args.kwargs["tipo"])


if __name__ == "__main__":
    unittest.main()
