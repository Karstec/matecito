# Decisiones técnicas de MATEcito

Este documento reúne las decisiones de **arquitectura** del proyecto: el
*porqué* de cómo está construido cada módulo. Se extrajo de los docstrings
largos que antes vivían al inicio de cada archivo `.py`, para aligerar el
código sin perder el razonamiento.

> **Nota importante:** los comentarios *inline* dentro de las funciones —los
> que explican una decisión pegada a una línea concreta, un bug evitado, un
> caso borde— **siguen en el código, a propósito**. Explican el *porqué* de
> una línea específica y protegen contra reintroducir bugs; despegarlos del
> código los vuelve inútiles. Este documento cubre las decisiones de
> arquitectura de alto nivel, no esos comentarios de línea.

---

## Padrón: Python como nexo, no DBLINK

El enfoque de todo el sistema:

```
        PADRÓN BCRA  <---->  PROCESO PYTHON  <---->  BASE CLIENTE
```

Python abre **dos conexiones independientes** (una a cada base) y hace de
intermediario. No se usa DBLINK. Ventaja concreta: sumar un cliente nuevo es
sumar una conexión, no pedirle a un DBA que cree un DBLINK en cada base.

---

## Pipeline por lotes (`nucleo/lotes.py`)

### Por qué por lotes y no todo junto

El flujo ingenuo haría `fetchall()` de la tabla entera y acumularía todos los
resultados en memoria antes de escribir. Con 500.000 registros eso es toda la
tabla en RAM — y en una Lambda de AWS (RAM y timeout acotados) directamente no
entra.

El ciclo real es:

```
leer N filas -> consultar el padrón por esas N -> comparar
             -> INSERTAR esas N -> siguiente lote
```

La memoria usada es constante (un lote a la vez), sin importar si la tabla
tiene mil filas o diez millones. Además, si el proceso se corta, lo ya
insertado y confirmado queda.

### Tamaño de lote automático

Se calcula según el total de registros: tablas chicas usan lotes chicos
(feedback rápido en pantalla), tablas grandes usan lotes grandes (menos viajes
a la base). El tope de 1000 respeta el límite de elementos en un `IN (...)` de
Oracle. El usuario no elige nada.

| Registros | Lote |
|---|---|
| ≤ 100 | 25 |
| ≤ 1.000 | 100 |
| ≤ 10.000 | 500 |
| > 10.000 | 1.000 |

### Seguridad transaccional

Todo en una transacción: `COUNT` de verificación al final, `COMMIT` solo si
cuadra, `ROLLBACK` si no. Nunca quedan tablas a medias.

---

## Claves de padrón (`nucleo/claves_padron.py`)

### Por qué existe este módulo

El armado de claves CUIT/DNI y su lookup estaban escritos **tres veces** en
`app.py`, en tres jobs distintos, y las tres **no** hacían lo mismo.

La divergencia importante estaba en el lookup: las tres consultaban el padrón
por tres variantes del DNI (`{d, d.zfill(8), d.lstrip("0")}`) pero al leer el
resultado, dos probaban las tres variantes en cascada y una leía solo
`mapa_dni.get(dni_n, [])`. Es decir: pedía tres llaves y abría una sola.

Un DNI de 7 dígitos que el padrón guarda con el cero adelante (`2456884` vs
`02456884`) se consultaba bien, volvía en el mapa, y se descartaba al leerlo:
NO ENCONTRADO para una persona que sí estaba. El bug estaba arreglado en el
flujo por archivo y vivo en el flujo por base de datos, que es el que más se
usa.

Este módulo deja una sola implementación. Al usarlo, el bug desaparece de los
tres lugares a la vez.

---

## Cuitificación y búsqueda (`validadores/cuitificador.py`)

### Tres funciones distintas sobre el mismo padrón

1. **Validar denominación** — tenés nombre + DNI/CUIT, ¿son la misma persona
   que en BCRA?
2. **Cuitificar** — tenés solo el DNI/CUIT, querés traer la denominación. Una
   fila por cada denominación distinta encontrada.
3. **Búsqueda manual** — no es validación, es consulta: escribís un número y
   trae lo que coincida.

### Por qué la búsqueda manual usa LIKE y los procesos no

La búsqueda manual usa `LIKE '%numero%'`: encuentra todo lo que *contenga* el
fragmento. Eso se quiere ahí — encontrar de más es una virtud cuando consultás.

Pero `LIKE '%...%'` **no puede usar el índice**: el comodín al principio obliga
a recorrer las ~65M filas del padrón. Tolerable para una consulta manual (tarda
segundos), catastrófico en un proceso masivo (500.000 filas × un scan cada
una = no termina).

Y hay un problema peor que la lentitud: `%2456884%` también matchea el DNI
`12456884` y el `24568840`. En consulta manual está bien (ves todo y elegís).
En un proceso automático significaría **asignarle a alguien el CUIT de otra
persona**, porque el número "contenía" al buscado.

Por eso los procesos masivos usan **match exacto por índice**, con un rescate
en tres pasos:

1. el número tal cual (exacto)
2. si tiene 11 dígitos, se busca solo como CUIT (no se extrae el DNI interno:
   `20-xxxxxxxx-x` y `27-xxxxxxxx-x` son personas distintas)
3. si tiene menos de 8 dígitos, se completa con ceros a la izquierda
   (`2456884` → `02456884`) — resuelve el DNI al que Excel le comió el cero

---

## Credenciales del padrón (`padron/credenciales.py`)

### Modelo de seguridad (sin vender humo)

La clave de cifrado Fernet es **híbrida**:

1. Si existe la variable de entorno `MATECITO_KEY` → se usa esa (modo seguro:
   la clave vive fuera del código y de la carpeta; ideal para AWS Secrets
   Manager).
2. Si no existe → se deriva una clave interna del propio código (modo cómodo:
   funciona sin configurar nada, para "cada uno en su PC").

En modo 2 el cifrado es **ofuscación, no seguridad fuerte**: alguien con acceso
a la carpeta *y* al código podría descifrarlo. Es suficiente para que la
contraseña no esté a la vista y no se filtre al mover el archivo. Para
seguridad real se define `MATECITO_KEY` y el mismo código pasa a modo 1 **sin
cambios**. Ese es el punto de hacerlo híbrido: no hay que tocar el código al
migrar a AWS, solo definir la variable.

### Autocifrado

Si en la carpeta hay un `padron_conexion.json` en texto plano, al arrancar se
cifra a `.enc` y se borra el plano, para no dejar la contraseña en texto.

---

## Comparación de denominaciones (`validadores/comparadores.py`)

El detalle completo de los 6 algoritmos, qué mide cada uno, y el diagnóstico
de la columna MOTIVO está en el documento aparte
`Algoritmos_de_comparacion.docx` (o su versión en este repo). Los comentarios
inline del módulo explican cada algoritmo en su lugar.
