"""Pruebas del estado compartido de sesiones y conexiones."""

import os
import sys
import unittest
from unittest.mock import patch


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matecito.nucleo.sesiones import (
    CONEXIONES,
    COOKIE_SESION,
    SESIONES_USUARIO,
    cerrar_conexion,
    nueva_sesion,
    obtener_conexion,
    obtener_usuario,
    registrar_conexion,
    registrar_usuario,
    usuario_de_sesion,
)


class ConexionFalsa:
    def __init__(self):
        self.cierres = 0

    def cerrar(self):
        self.cierres += 1


class SesionesTest(unittest.TestCase):
    def setUp(self):
        CONEXIONES.clear()
        SESIONES_USUARIO.clear()

    def tearDown(self):
        CONEXIONES.clear()
        SESIONES_USUARIO.clear()

    def test_nueva_sesion_respeta_longitud(self):
        self.assertEqual(16, len(nueva_sesion(16)))

    def test_registra_y_obtiene_conexion(self):
        conexion = ConexionFalsa()
        sid = registrar_conexion(conexion)
        self.assertIs(conexion, obtener_conexion(sid))

    def test_reemplazar_conexion_cierra_la_anterior(self):
        anterior = ConexionFalsa()
        nueva = ConexionFalsa()
        registrar_conexion(anterior, "sesion")
        registrar_conexion(nueva, "sesion")
        self.assertEqual(1, anterior.cierres)
        self.assertIs(nueva, obtener_conexion("sesion"))

    def test_cerrar_conexion_la_retira(self):
        conexion = ConexionFalsa()
        registrar_conexion(conexion, "sesion")
        self.assertTrue(cerrar_conexion("sesion"))
        self.assertEqual(1, conexion.cierres)
        self.assertIsNone(obtener_conexion("sesion"))
        self.assertFalse(cerrar_conexion("sesion"))

    def test_usuarios_quedan_aislados(self):
        sid_ana = registrar_usuario("Ana")
        sid_luis = registrar_usuario("Luis")
        self.assertEqual("Ana", obtener_usuario(sid_ana))
        self.assertEqual("Luis", obtener_usuario(sid_luis))

    @patch("matecito.nucleo.sesiones.leer_usuario_guardado", return_value="Local")
    def test_usuario_desconocido_usa_fallback(self, leer):
        self.assertEqual("Local", obtener_usuario("inexistente"))
        self.assertEqual("", obtener_usuario("inexistente", defecto=False))
        leer.assert_called_once_with()

    def test_usuario_de_sesion_lee_la_cookie(self):
        class RequestFalso:
            cookies = {COOKIE_SESION: "navegador"}

        registrar_usuario("Carla", "navegador")
        self.assertEqual("Carla", usuario_de_sesion(RequestFalso()))


if __name__ == "__main__":
    unittest.main()
