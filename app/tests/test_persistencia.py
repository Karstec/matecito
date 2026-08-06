"""Pruebas de persistencia aisladas del directorio real de la aplicación."""

import json
import os
import sys
import tempfile
import unittest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matecito.nucleo.persistencia import (
    cargar_historial,
    cargar_presets,
    guardar_presets,
    guardar_usuario,
    leer_usuario_guardado,
    persistir_en_historial,
)


class PersistenciaTest(unittest.TestCase):
    def setUp(self):
        self.temporal = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temporal.cleanup()

    def ruta(self, nombre):
        return os.path.join(self.temporal.name, nombre)

    def test_archivos_inexistentes_devuelven_defaults(self):
        self.assertEqual([], cargar_historial(self.ruta("historial.json")))
        self.assertEqual({}, cargar_presets(self.ruta("presets.json")))
        self.assertEqual("", leer_usuario_guardado(self.ruta("usuario.json")))

    def test_usuario_conserva_unicode(self):
        ruta = self.ruta("usuario.json")
        guardar_usuario("José Núñez", ruta)
        self.assertEqual("José Núñez", leer_usuario_guardado(ruta))

    def test_presets_conservan_el_contrato_json(self):
        ruta = self.ruta("presets.json")
        presets = {"Producción": {"host": "db", "dbname": "clientes"}}
        guardar_presets(presets, ruta)
        self.assertEqual(presets, cargar_presets(ruta))

    def test_historial_mas_reciente_primero(self):
        ruta = self.ruta("historial.json")
        persistir_en_historial({"id": "uno", "estado": "OK"}, ruta)
        persistir_en_historial({"id": "dos", "estado": "ERROR"}, ruta)
        self.assertEqual(["dos", "uno"], [item["id"] for item in cargar_historial(ruta)])

    def test_historial_actualiza_sin_duplicar(self):
        ruta = self.ruta("historial.json")
        persistir_en_historial({"id": "job", "estado": "EN_CURSO"}, ruta)
        persistir_en_historial({"id": "job", "estado": "OK"}, ruta)
        historial = cargar_historial(ruta)
        self.assertEqual(1, len(historial))
        self.assertEqual("OK", historial[0]["estado"])

    def test_historial_respeta_el_limite(self):
        ruta = self.ruta("historial.json")
        for numero in range(5):
            persistir_en_historial({"id": str(numero)}, ruta, limite=3)
        self.assertEqual(["4", "3", "2"], [item["id"] for item in cargar_historial(ruta)])

    def test_json_invalido_devuelve_defaults(self):
        ruta = self.ruta("invalido.json")
        with open(ruta, "w", encoding="utf-8") as archivo:
            archivo.write("esto no es json")
        self.assertEqual([], cargar_historial(ruta))
        self.assertEqual({}, cargar_presets(ruta))
        self.assertEqual("", leer_usuario_guardado(ruta))

    def test_archivos_se_escriben_como_json_legible(self):
        ruta = self.ruta("presets.json")
        guardar_presets({"local": {"host": "127.0.0.1"}}, ruta)
        with open(ruta, "r", encoding="utf-8") as archivo:
            self.assertEqual("127.0.0.1", json.load(archivo)["local"]["host"])


if __name__ == "__main__":
    unittest.main()
