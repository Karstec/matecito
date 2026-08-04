"""Pruebas de caracterización de la API de MATEcito.

Estas pruebas fijan el contrato público antes de modularizar ``app.py``.
No requieren bases de datos, VPN, credenciales ni escritura persistente.
"""

import os
import sys
import unittest

from fastapi.testclient import TestClient
from pydantic import ValidationError


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matecito.api.schemas import ConexionRequest, ProcesoDBRequest
from matecito.app import app
from matecito.config import LIMITE_INTERACCIONES_OSINT


RUTAS_API = {
    ("GET", "/"),
    ("GET", "/api/estado"),
    ("GET", "/api/osint/proveedores"),
    ("POST", "/api/usuario"),
    ("GET", "/api/presets"),
    ("POST", "/api/presets"),
    ("POST", "/api/conexion"),
    ("GET", "/api/conexion/{sid}/tablas"),
    ("GET", "/api/conexion/{sid}/columnas"),
    ("GET", "/api/conexion/{sid}/muestra"),
    ("POST", "/api/procesos/db"),
    ("POST", "/api/normalizacion/db"),
    ("POST", "/api/normalizacion/archivo"),
    ("POST", "/api/archivo/muestra"),
    ("POST", "/api/procesos/archivo"),
    ("GET", "/api/padron/buscar"),
    ("GET", "/api/historial"),
    ("GET", "/api/procesos/{job_id}"),
    ("GET", "/api/procesos/{job_id}/csv"),
    ("GET", "/api/cruce-redes/columnas-archivo"),
    ("POST", "/api/cruce-redes/subir"),
    ("POST", "/api/cruce-redes/ejecutar"),
}


class ContratoRutasTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_mapa_de_rutas_publicas(self):
        rutas = {
            (metodo.upper(), path)
            for path, operaciones in app.openapi()["paths"].items()
            for metodo in operaciones
            if metodo.upper() in {"GET", "POST"}
            and (path == "/" or path.startswith("/api/"))
        }
        self.assertEqual(RUTAS_API, rutas)

    def test_pagina_principal(self):
        respuesta = self.client.get("/")
        self.assertEqual(200, respuesta.status_code)
        self.assertIn("MATEcito", respuesta.text)
        self.assertIn('/static/js/main.js', respuesta.text)

    def test_estado(self):
        respuesta = self.client.get("/api/estado")
        self.assertEqual(200, respuesta.status_code)
        datos = respuesta.json()
        self.assertTrue(datos["ok"])
        self.assertEqual("MATEcito Web", datos["app"])
        self.assertIsInstance(datos["paises_telefono"], dict)
        self.assertIn("AR", datos["paises_telefono"])

    def test_historial_devuelve_una_lista(self):
        respuesta = self.client.get("/api/historial")
        self.assertEqual(200, respuesta.status_code)
        self.assertIsInstance(respuesta.json(), list)

    def test_job_inexistente_conserva_el_404(self):
        respuesta = self.client.get("/api/procesos/__contrato_inexistente__")
        self.assertEqual(404, respuesta.status_code)
        self.assertEqual("Proceso no encontrado", respuesta.json()["detail"])

    def test_sesion_inexistente_conserva_el_404(self):
        respuesta = self.client.get(
            "/api/conexion/__contrato_inexistente__/tablas",
            params={"esquema": ""},
        )
        self.assertEqual(404, respuesta.status_code)

    def test_openapi_expone_los_schemas_extraidos(self):
        respuesta = self.client.get("/openapi.json")
        self.assertEqual(200, respuesta.status_code)
        schemas = respuesta.json()["components"]["schemas"]
        for nombre in (
            "ConexionRequest",
            "NormalizacionDBRequest",
            "PresetRequest",
            "ProcesoDBRequest",
            "UsuarioRequest",
        ):
            self.assertIn(nombre, schemas)


class ContratoSchemasTest(unittest.TestCase):
    def test_defaults_de_conexion(self):
        request = ConexionRequest(db_type="oracle", host="db", user="matecito")
        self.assertEqual("", request.port)
        self.assertEqual("", request.password)
        self.assertEqual("", request.dbname)

    def test_defaults_de_proceso_db(self):
        request = ProcesoDBRequest(
            session_id="sid",
            proceso="mails",
            esquema="PUBLIC",
            tabla="CONTACTOS",
            col_id="ID",
            col_dato="MAIL",
            usuario="TEST",
        )
        self.assertEqual("cuit", request.tipo_busqueda)
        self.assertEqual("AR", request.pais)
        self.assertEqual([], request.proveedores_osint)
        self.assertEqual({}, request.mapa_domicilio)
        self.assertEqual(
            LIMITE_INTERACCIONES_OSINT,
            request.limite_interacciones_osint,
        )

    def test_limite_osint_rechaza_valores_fuera_de_rango(self):
        datos = {
            "session_id": "sid",
            "proceso": "osint",
            "esquema": "PUBLIC",
            "tabla": "CONTACTOS",
            "col_id": "ID",
            "col_dato": "MAIL",
            "usuario": "TEST",
        }
        for limite in (0, LIMITE_INTERACCIONES_OSINT + 1):
            with self.subTest(limite=limite):
                with self.assertRaises(ValidationError):
                    ProcesoDBRequest(
                        **datos,
                        limite_interacciones_osint=limite,
                    )


if __name__ == "__main__":
    unittest.main()
