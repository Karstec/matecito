"""Pruebas de transformación y presupuesto de correo."""

import os
import sys
import unittest
from datetime import datetime


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matecito.nucleo.correo import limitar_emails_osint, procesar_fila_mail
from matecito.validadores.telefonos import USUARIO_AGENTE


class AgenteFalso:
    def __init__(self, respuesta):
        self.respuesta = respuesta

    def validar_y_corregir_email(self, mail):
        return self.respuesta


class PresupuestoOsintTest(unittest.TestCase):
    def test_prorratea_por_cantidad_de_proveedores(self):
        emails = [f"persona{i}@example.com" for i in range(10)]
        seleccion, maximo = limitar_emails_osint(
            emails, ["github", "facebook", "linkedin"], limite=8
        )
        self.assertEqual(2, maximo)
        self.assertEqual(emails[:2], seleccion)

    def test_sin_proveedores_no_divide_por_cero(self):
        emails = ["persona@example.com"]
        self.assertEqual((emails, 5), limitar_emails_osint(emails, [], limite=5))


class ResultadoCorreoTest(unittest.TestCase):
    def setUp(self):
        self.ahora = datetime(2026, 8, 4, 17, 0, 0)

    def procesar(self, respuesta):
        return procesar_fila_mail(
            AgenteFalso(respuesta), "cliente-1", "original@example.com", self.ahora
        )

    def test_correo_conservado(self):
        fila = self.procesar(("original@example.com", True, False, "OK"))
        self.assertEqual("CONSERVADO", fila["ESTADO"])
        self.assertEqual(1, fila["VALIDO"])
        self.assertIsNone(fila["FECHA_BAJA"])

    def test_correo_modificado(self):
        fila = self.procesar(("corregido@example.com", True, True, "Corregido"))
        self.assertEqual("MODIFICADO", fila["ESTADO"])
        self.assertEqual("corregido@example.com", fila["MAIL_DEPURADO"])

    def test_correo_dado_de_baja(self):
        fila = self.procesar(("", False, False, "Dominio inválido"))
        self.assertEqual("BAJA", fila["ESTADO"])
        self.assertEqual(0, fila["VALIDO"])
        self.assertEqual(self.ahora, fila["FECHA_BAJA"])
        self.assertEqual(USUARIO_AGENTE, fila["USUARIO_BAJA"])
        self.assertEqual("Dominio inválido", fila["MOTIVO_BAJA"])

    def test_revision_manual_tiene_prioridad(self):
        respuesta = ("dudoso@example.com", True, True, "Revisar", None, True)
        fila = self.procesar(respuesta)
        self.assertEqual("REVISION MANUAL", fila["ESTADO"])
        self.assertEqual(0, fila["VALIDO"])
        self.assertIsNone(fila["MAIL_DEPURADO"])
        self.assertIsNone(fila["FECHA_BAJA"])


if __name__ == "__main__":
    unittest.main()
