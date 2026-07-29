# Cómo agregar un proceso nuevo

Este es el objetivo de toda la reestructuración: sumar un proceso sin tocar
`run.py` y tocando `app.py` lo mínimo. Usamos COMPARACIÓN como ejemplo real,
porque es el último que se agregó.

## Los pasos

### 1. Escribir la lógica pura (validadores/)

Un archivo en `validadores/` que reciba datos y devuelva filas de resultado.
Sin tocar bases ni archivos: solo la lógica.

En COMPARACIÓN, eso es `validadores/comparadores.py`, con dos funciones clave:

```python
def fila_resultado_comparacion(nom1, nom2, ahora, id_origen=None):
    """Recibe dos nombres, devuelve un dict con una columna por algoritmo."""

def columnas_tabla(db_type):
    """El DDL de la tabla resultado, derivado del registro de algoritmos."""
```

La clave es que `columnas_tabla()` genere el DDL desde el mismo registro de
algoritmos que usa `fila_resultado_comparacion()`. Así el INSERT y el CREATE
TABLE nunca se desincronizan: agregar un algoritmo cambia las dos cosas a la vez.

### 2. Registrar el proceso (procesos/registro.py)

Una entrada en el diccionario `PROCESOS`:

```python
"comparacion": {
    "cols_origen": 2,      # compara dos columnas de nombres
    "padron": False,       # no consulta el padrón
    "umbral": False,       # sin selector de umbral
    "etiqueta": "REDES SOCIALES · Comparación de algoritmos",
},
```

Con esto, la validación del request, la detección de si pide una o dos
columnas, y varias cosas más ya funcionan sin escribir código: leen del
registro.

### 3. Enganchar el proceso donde el registro no alcanza

Quedan tres puntos donde hay que agregar la rama del proceso. Los tres siguen
el mismo patrón que el proceso más parecido (para COMPARACIÓN, `denominacion`):

**a) El DDL de la tabla** — en `app.py`, función `_tipos_columnas`:

```python
if proceso == "comparacion":
    return comparadores.columnas_tabla(db_type)
```

**b) El job que procesa** — en `app.py`, `_job_procesar_db` (flujo por base)
y `_job_procesar_archivo` (flujo por archivo), una rama:

```python
elif proceso == "comparacion":
    for i, (nom1, nom2) in enumerate(rows, 1):
        resultados.append(
            comparadores.fila_resultado_comparacion(nom1, nom2, ahora, id_origen=str(i)))
```

**c) Las estadísticas** — en `app.py`, `_stats_y_csv`:

```python
elif proceso == "comparacion":
    job.stats = comparadores.estadisticas_comparacion(resultados)
```

### 4. El frontend (static/index.html)

Un botón en el menú y el nombre del proceso. Cinco líneas, todas siguiendo el
patrón de `denominacion`.

## Lo que NO se toca

- `run.py` — nunca.
- El router de jobs — solo si el proceso necesita el pipeline por lotes
  especial (los que consultan el padrón). COMPARACIÓN no lo necesita, así que
  cae en `_job_procesar_db` por defecto.

## Para quitar un proceso

Borrar su entrada del registro y sus ramas. El `.gitignore` y la estructura
hacen que sea un cambio localizado, no una cacería por todo el código.

## Regla de oro

Un proceso nuevo es **un commit** (`feat: ...`), no diez. Toca varios archivos,
pero es un solo cambio con sentido. Ver `CONTRIBUTING.md`.
