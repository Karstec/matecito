"""Pruebas puras de lectura y detección de archivos tabulares."""

import io
import os
import sys
import unittest

import openpyxl


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matecito.nucleo.archivos import (
    celda_muestra,
    crear_muestra,
    detectar_columnas,
    detectar_columnas_normalizacion,
    detectar_encoding,
    leer_archivo,
)


class LecturaArchivosTest(unittest.TestCase):
    def test_detecta_utf8_con_bom(self):
        contenido = "MAIL;NOMBRE\nana@example.com;Ana".encode("utf-8-sig")
        self.assertEqual("utf-8-sig", detectar_encoding(contenido))

    def test_detecta_cp1252(self):
        contenido = "NOMBRE\nJosé Núñez".encode("cp1252")
        self.assertEqual("cp1252", detectar_encoding(contenido))

    def test_csv_detecta_delimitador_y_encabezado(self):
        contenido = "ID;CORREO;NOMBRE\n1;ana@example.com;Ana".encode("utf-8")
        encabezado, filas, delimitador = leer_archivo("contactos.csv", contenido)
        self.assertEqual(";", delimitador)
        self.assertEqual(["ID", "CORREO", "NOMBRE"], encabezado)
        self.assertEqual([["1", "ana@example.com", "Ana"]], filas)

    def test_csv_sin_encabezado_conserva_la_primera_fila(self):
        contenido = "1,ana@example.com\n2,luis@example.com".encode("utf-8")
        encabezado, filas, _ = leer_archivo("contactos.csv", contenido)
        self.assertIsNone(encabezado)
        self.assertEqual("1", filas[0][0])

    def test_excel_usa_la_primera_hoja(self):
        libro = openpyxl.Workbook()
        hoja = libro.active
        hoja.append(["CUIT", "NOMBRE"])
        hoja.append([20123456789, "Ana"])
        buffer = io.BytesIO()
        libro.save(buffer)

        encabezado, filas, delimitador = leer_archivo("contactos.xlsx", buffer.getvalue())
        self.assertEqual(["CUIT", "NOMBRE"], encabezado)
        self.assertEqual([[20123456789, "Ana"]], filas)
        self.assertEqual(",", delimitador)


class DeteccionColumnasTest(unittest.TestCase):
    def test_detecta_mail_e_identificador_por_encabezado(self):
        self.assertEqual(
            (0, 2),
            detectar_columnas(["ID", "NOMBRE", "EMAIL"], [], "mails"),
        )

    def test_detecta_mail_por_contenido_sin_encabezado(self):
        self.assertEqual(
            (None, 1),
            detectar_columnas(None, [["1", "ana@example.com"]], "mails"),
        )

    def test_detecta_telefono_por_cantidad_de_digitos(self):
        self.assertEqual(
            (None, 1),
            detectar_columnas(None, [["ANA", "+54 11 5555 5555"]], "telefonos"),
        )

    def test_detecta_cuit_y_denominacion(self):
        self.assertEqual(
            (0, 1),
            detectar_columnas(["CUIT", "RAZÓN SOCIAL"], [], "cuit"),
        )

    def test_detecta_dos_denominaciones(self):
        self.assertEqual(
            (1, 2),
            detectar_columnas(
                ["ID", "NOMBRE ORIGEN", "DENOMINACION DESTINO"],
                [],
                "comparacion",
            ),
        )

    def test_normalizacion_separa_medios_y_extras(self):
        resultado = detectar_columnas_normalizacion(
            ["CUIT", "TELEFONO", "EMAIL", "ORIGEN"],
            [],
            {"telefonos", "mails"},
        )
        self.assertEqual((0, [1, 2], [3]), resultado)


class MuestraArchivosTest(unittest.TestCase):
    def test_celda_muestra_limpia_y_recorta(self):
        self.assertEqual("línea uno línea dos", celda_muestra(" línea uno\nlínea dos "))
        self.assertEqual("abc…", celda_muestra("abcdef", largo=4))
        self.assertIsNone(celda_muestra(None))

    def test_muestra_sin_encabezado_numera_columnas(self):
        muestra = crear_muestra(None, [["1", ""], ["2", None]], limite=10)
        self.assertEqual(["col1", "col2"], muestra["columnas"])
        self.assertEqual(2, muestra["cantidad"])
        self.assertEqual(1, muestra["diagnostico"][1]["nulos"])
        self.assertEqual(1, muestra["diagnostico"][1]["vacios"])

    def test_muestra_limita_a_cincuenta_filas(self):
        filas = [[numero] for numero in range(80)]
        muestra = crear_muestra(["ID"], filas, limite=1000)
        self.assertEqual(50, muestra["cantidad"])
        self.assertEqual(80, muestra["total"])

    def test_muestra_vacia_conserva_el_contrato(self):
        self.assertEqual(
            {
                "columnas": [],
                "filas": [],
                "cantidad": 0,
                "total": 0,
                "diagnostico": [],
            },
            crear_muestra(None, []),
        )


if __name__ == "__main__":
    unittest.main()
