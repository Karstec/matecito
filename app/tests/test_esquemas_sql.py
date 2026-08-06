"""Pruebas de los esquemas SQL de salida."""

import os
import sys
import unittest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matecito.nucleo.esquemas_sql import (
    crear_ddl,
    tipos_columnas,
    tipos_columnas_normalizacion,
)
from matecito.validadores import comparadores


class TiposColumnasTest(unittest.TestCase):
    def test_oracle_usa_tipos_oracle(self):
        columnas = dict(tipos_columnas("oracle", "telefonos"))
        self.assertEqual("VARCHAR2(80)", columnas["ID_ORIGEN"])
        self.assertEqual("NUMBER(1)", columnas["VALIDO"])
        self.assertEqual("DATE", columnas["FECHA_PROCESO"])

    def test_mysql_usa_tipos_compatibles(self):
        columnas = dict(tipos_columnas("mysql", "telefonos"))
        self.assertEqual("VARCHAR(80)", columnas["ID_ORIGEN"])
        self.assertEqual("TINYINT", columnas["VALIDO"])
        self.assertEqual("DATETIME", columnas["FECHA_PROCESO"])

    def test_sql_server_comparte_tipos_no_oracle(self):
        self.assertEqual(
            tipos_columnas("mysql", "mails"),
            tipos_columnas("sqlserver", "mails"),
        )

    def test_proceso_desconocido_conserva_esquema_de_mails(self):
        self.assertEqual(
            tipos_columnas("oracle", "mails"),
            tipos_columnas("oracle", "desconocido"),
        )

    def test_comparacion_delega_en_su_registro(self):
        self.assertEqual(
            comparadores.columnas_tabla("oracle"),
            tipos_columnas("oracle", "comparacion"),
        )

    def test_cuit_conserva_identidad_por_motor(self):
        oracle = dict(tipos_columnas("oracle", "cuit"))
        mysql = dict(tipos_columnas("mysql", "cuit"))
        self.assertEqual("NUMBER GENERATED ALWAYS AS IDENTITY", oracle["ID"])
        self.assertEqual("INT AUTO_INCREMENT PRIMARY KEY", mysql["ID"])


class NormalizacionSqlTest(unittest.TestCase):
    def test_normalizacion_sanitiza_columnas(self):
        columnas = tipos_columnas_normalizacion(
            "oracle", "Número cliente", ["Teléfono móvil"], ["Área / Región"]
        )
        self.assertEqual(
            [
                ("NUMERO_CLIENTE", "VARCHAR2(300)"),
                ("TELEFONO_MOVIL", "VARCHAR2(300)"),
                ("AREA_REGION", "VARCHAR2(300)"),
            ],
            columnas,
        )

    def test_normalizacion_usa_clave_por_defecto(self):
        self.assertEqual(
            [("CLAVE", "VARCHAR(300)")],
            tipos_columnas_normalizacion("mysql", "", [], []),
        )

    def test_crear_ddl_respeta_orden_y_tipos(self):
        ddl = crear_ddl("RESULTADO", [("ID", "NUMBER"), ("MAIL", "VARCHAR2(300)")])
        self.assertEqual(
            "CREATE TABLE RESULTADO (ID NUMBER, MAIL VARCHAR2(300))", ddl
        )


if __name__ == "__main__":
    unittest.main()
