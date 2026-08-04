"""Pruebas unitarias del núcleo de conexiones, sin bases externas."""

import os
import sys
import unittest
from unittest.mock import Mock


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matecito.nucleo.conexiones import ConexionWeb


def conexion(motor):
    return ConexionWeb(motor, "host", None, "usuario", "secreto", "base")


class ConexionWebTest(unittest.TestCase):
    def test_marcadores_por_motor(self):
        self.assertIsNone(conexion("oracle").marcador)
        self.assertEqual("?", conexion("sqlserver").marcador)
        self.assertEqual("%s", conexion("mysql").marcador)
        self.assertEqual("%s", conexion("mariadb").marcador)

    def test_motor_no_soportado(self):
        with self.assertRaisesRegex(ValueError, "Motor no soportado"):
            conexion("sqlite").conectar()

    def test_esquemas_oracle_filtra_cuentas_de_sistema(self):
        cx = conexion("oracle")
        cx.fetchall = Mock(return_value=[("SYS",), ("CLIENTE",), ("SYSTEM",)])
        self.assertEqual(["CLIENTE"], cx.esquemas())

    def test_esquemas_mysql_filtra_catalogos_internos(self):
        cx = conexion("mysql")
        cx.fetchall = Mock(
            return_value=[("information_schema",), ("cliente",), ("mysql",)]
        )
        self.assertEqual(["cliente"], cx.esquemas())

    def test_tablas_oracle_normaliza_el_esquema(self):
        cx = conexion("oracle")
        cx.fetchall = Mock(return_value=[("CONTACTOS",)])
        self.assertEqual(["CONTACTOS"], cx.tablas("cliente"))
        _, parametros = cx.fetchall.call_args.args
        self.assertEqual(("CLIENTE",), parametros)

    def test_columnas_conserva_el_contrato_de_salida(self):
        cx = conexion("mariadb")
        cx.fetchall = Mock(return_value=[("MAIL", "varchar", 255)])
        self.assertEqual(
            [{"nombre": "MAIL", "tipo": "varchar", "largo": 255}],
            cx.columnas("cliente", "contactos"),
        )

    def test_cerrar_delega_en_la_conexion(self):
        cx = conexion("mysql")
        cx.conn = Mock()
        cx.cerrar()
        cx.conn.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
