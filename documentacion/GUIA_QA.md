# MATEcito — guía de módulos para QA

Documento para probar cada módulo de forma independiente. Cada sección tiene:
qué hace el módulo, cómo ejecutarlo, qué columnas produce, y **qué revisar**.

Los datos de columnas y umbrales de este documento están tomados del código,
no de memoria. Si algo no coincide con lo que ves en pantalla, es un bug —
reportalo.

---

## 0. Antes de empezar

```bash
cd app
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python run.py                     # http://localhost:8000
```

**Configuración del padrón BCRA** (solo si vas a probar los módulos marcados
🔑 más abajo):

```bash
cd ..
python configurar_padron.py
python configurar_padron.py --probar
```

Sin esto, MATEcito arranca igual y todos los demás módulos funcionan.

### Reglas que valen para todos los módulos

| | |
|---|---|
| **La tabla origen nunca se modifica** | Ningún proceso hace UPDATE ni DELETE sobre lo que lee |
| **Cada corrida crea una tabla nueva** | `{USUARIO}_{CLIENTE}_{AAAAMMDD_HHMMSS}` — dos corridas no se pisan |
| **Todo o nada** | Se inserta en una transacción, se cuenta antes del COMMIT, y si no cuadra se hace ROLLBACK |
| **Siempre queda el original** | El dato de entrada se guarda sin tocar, así toda corrección se puede revertir |
| **Todos generan CSV** | Descargable desde el panel de progreso y desde el historial |

### Prueba transversal (hacer una vez, aplica a todos)

1. Correr cualquier proceso y verificar que la tabla origen quedó **idéntica**
   (mismo `COUNT(*)`, mismos datos).
2. Correr dos veces el mismo proceso: deben quedar **dos tablas distintas**.
3. Comparar el `COUNT(*)` de la tabla resultante contra la cantidad de filas
   del CSV. **Tienen que ser iguales.**
4. Cortar la conexión a mitad de un proceso: no debe quedar una tabla a medias.

---

## Categoría 1 · NORMALIZACIÓN

Separa columnas que traen varios valores en una sola celda, usando el pipe `|`.
Es lo primero que se corre sobre un export sucio.

**Procesos**: `norm_telefonos`, `norm_mails`, `norm_ambos`

**Cómo ejecutarlo**: menú → Normalización → elegir. Acepta archivo o tabla.

**Qué revisar**
- Una celda con `123|456|789` produce **tres filas**, no una.
- Una celda con un solo valor produce una fila y no se rompe.
- Una celda vacía no genera fila fantasma.
- El identificador se repite en las filas hijas para poder reagrupar.

---

## Categoría 2 · DEPURACIÓN

**Transforma, no juzga.** Ningún proceso de esta categoría da de baja nada.
Un dato que la depuración no tocó **no es un dato malo**: es un dato que ya
estaba bien escrito. Si ves bajas acá, es un bug.

### 2.1 `dep_mails` — Depurar mails

Corrige acentos, `arroba`/`punto` escritos como texto, y typos de dominio.

**Columnas de salida** (6)
```
ID_ORIGEN · MAIL_ORIGINAL · MAIL_DEPURADO · FUE_DEPURADO · CAMBIOS · FECHA_PROCESO
```

**Casos de prueba**

| Entrada | Salida esperada | `FUE_DEPURADO` |
|---|---|---|
| `José@gmáil.com` | `jose@gmail.com` | SI |
| `ana arroba hotmail punto com` | `ana@hotmail.com` | SI |
| `luis@gmeil.com` | `luis@gmail.com` | SI |
| `notiene@nomail.com` | *sin cambios* | NO |
| `juan@fibertel.com.ar` | *sin cambios* | NO |
| *(celda vacía)* | vacía, sin error | NO |

**Qué revisar**
- `notiene@nomail.com` **no se da de baja**. Es basura, pero juzgarla es tarea
  de validación. Si aparece marcado como baja, es un bug.
- `CAMBIOS` describe qué se hizo, en texto legible.
- Un mail que ya está bien sale igual y con `FUE_DEPURADO = NO`.
- La `ñ` se conserva en el usuario (`muñoz@…`) pero no en el dominio.

### 2.2 `dep_telefonos` — Depurar teléfonos

Quita símbolos, separa prefijo de país y numeración, agrega `+54` / `+598`.
**No dice si el número existe.**

**Columnas de salida** (10)
```
ID_ORIGEN · TELEFONO_ORIGINAL · PREFIJO_PAIS · NUMERO_NACIONAL ·
TELEFONO_DEPURADO · E164 · ORIGEN_PAIS · FUE_DEPURADO · CAMBIOS · FECHA_PROCESO
```

**Casos de prueba**

| Entrada | `PREFIJO_PAIS` | `NUMERO_NACIONAL` | `E164` |
|---|---|---|---|
| `(011) 15-4123-4567` | 54 | 1141234567 | +541141234567 |
| `011 4123 4567` | 54 | 1141234567 | +541141234567 |
| `+54 9 11 4123-4567` | 54 | 1141234567 | +541141234567 |
| `1141234567` | 54 | 1141234567 | +541141234567 |
| `0351 155-123456` | 54 | 3515123456 | +543515123456 |
| `Cel: 0223 15 4567890` | 54 | 2234567890 | +542234567890 |
| `+598 91854820` | 598 | 91854820 | +59891854820 |

**Los cuatro primeros tienen que dar el MISMO `E164`.** Son cuatro escrituras
del mismo teléfono.

**Qué revisar**
- **Una celda con varios números produce varias filas.** `+598 91854820 / +598
  47255621` sale como dos filas, ambas con el mismo `ID_ORIGEN`. No es un bug.
- El `0` de larga distancia y el `15` de celular **no** aparecen en `E164`.
  El `15` argentino va *después* del código de área, no al principio.
- `ORIGEN_PAIS` dice `explicito` (el dato traía `+54`), `detectado` (empezaba
  con el código sin `+`) o `asumido` (no había señal, se usó el país del
  formulario). **Si el origen mezcla países, `asumido` es lo que hay que
  auditar.**
- Cambiar el país en el formulario y volver a correr: los `asumido` cambian de
  prefijo, los `explicito` no.

---

## Categoría 3 · VALIDACIÓN

**Juzga, no transforma.** Ningún proceso de esta categoría corrige el dato.

Los tres estados son `VALIDO`, `INVALIDO` y `REVISION`. **`REVISION` no es un
inválido tibio: es la ausencia de dictamen.** Un registro en `REVISION` no se
da de baja ni se modifica — queda como está, marcado.

### 3.1 `mails` — Validación de mails por reglas

**Columnas de salida** (10)
```
ID_ORIGEN · MAIL_ORIGINAL · MAIL_DEPURADO · ESTADO · VALIDO · MOTIVO ·
FECHA_BAJA · USUARIO_BAJA · MOTIVO_BAJA · FECHA_PROCESO
```

**Casos de prueba**

| Entrada | `ESTADO` | Por qué |
|---|---|---|
| `juan.perez@gmail.com` | VALIDO | |
| `notiene@nomail.com` | INVALIDO | usuario en lista negra |
| `jb1583564@gmail.com` | INVALIDO | patrón de ID/teléfono |
| `ana?lucia@gmail.com` | **REVISION** | `?` = carácter perdido, no reconstruible |
| `no.posee@hotmail.com` | INVALIDO | placeholder de "no tiene mail" |
| *(cadena de 250 caracteres)* | REVISION | supera el largo razonable |

**Qué revisar**
- El caso del `?` es el más importante: **no** debe darse de baja ni
  "corregirse" borrando el signo. Va a `REVISION`.
- Correr este proceso sobre datos crudos da **más inválidos** que correrlo
  después de `dep_mails`. Eso es correcto, son dos preguntas distintas.

### 3.2 `telefonos` — Validación de teléfonos

Usa la librería `phonenumbers` (el port de libphonenumber de Google).

**Columnas de salida** (14)
```
ID_ORIGEN · TELEFONO_ORIGINAL · TELEFONO_NORMALIZADO · CODIGO_PAIS · PREFIJO ·
TELEFONO · TIPO_TELEFONO · TIPO_LINEA · VALIDO · MOTIVO · FECHA_BAJA ·
USUARIO_BAJA · MOTIVO_BAJA · FECHA_PROCESO
```

**Qué revisar**
- `TIPO_LINEA` distingue FIJO de MOVIL.
- Un número con la cantidad correcta de dígitos pero prefijo inexistente debe
  dar inválido.
- Números de relleno (`0000000000`, `1111111111`) deben dar inválido.

### 3.3 `osint` — Validación de mails contra proveedores

**No tocar.** Es el módulo de otro desarrollador y no tiene cambios nuestros.
Se prueba con su documentación, no con esta.

### 3.4 `cuit` — Denominación contra CUIT 🔑

Requiere padrón BCRA configurado. Recibe **dos columnas**: CUIT/DNI y
denominación. Dice si ese nombre corresponde a ese CUIT.

**Columnas de salida** (17)
```
ID · CUIT_ORIGEN · DNI_ORIGEN · DENOMINACION_ORIGEN · CUIT_PADRON ·
DENOMINACION_PADRON · PORCENTAJE · UMBRAL · ESTADO_VALIDACION · CANDIDATOS ·
MARCA_BAJA · FECHA_FALLECIMIENTO · CUIT_REEMPLAZO · ALERTAS ·
USUARIO_DECISION · FECHA_DECISION · FECHA_PROCESO
```

**Qué revisar**
- **CUIT completo (11 dígitos)**: se consulta solo ese CUIT, sin expandir a
  los hermanos del DNI.
- **DNI suelto**: trae todas las personas con ese DNI y **todas** quedan
  marcadas `REVISION = SI`. Nunca elige una sola.
- Bajar el umbral debe aumentar las coincidencias, no cambiar el
  `PORCENTAJE`.

---

## Categoría 4 · BÚSQUEDA

**Consulta, no modifica ni dictamina.**

### 4.1 CUIT / DNI puntual 🔑

Ventana de consulta de a un número. Trae los datos del padrón.

**Qué revisar**: un CUIT inexistente da "sin resultados", no un error.

### 4.2 `cuitificacion` — CUIT/DNI en lote 🔑

Un listado de números → los datos del padrón para cada uno.

**Columnas de salida** (13)
```
ID · NUMERO_ORIGEN · NUMERO_BUSCADO · CUIT_ENCONTRADO ·
DENOMINACION_ENCONTRADA · DNI_ENCONTRADO · MARCA_BAJA · FECHA_FALLECIMIENTO ·
CUIT_REEMPLAZO · ESTADO · REVISION · COINCIDENCIAS · FECHA_PROCESO
```

**Qué revisar**
- Un DNI con varias personas produce **varias filas**, todas con
  `REVISION = SI` y `COINCIDENCIAS` mayor a 1.
- `CUIT_REEMPLAZO` y `FECHA_FALLECIMIENTO` se traen cuando existen.

### 4.3 `cruce_redes` — Cruce de denominaciones

**El módulo más nuevo y el que más atención necesita.**

Compara la columna de nombre de un archivo contra una columna de nombre de una
base, con 6 algoritmos. **No usa el padrón BCRA**: las credenciales de la base
se cargan en el propio panel, porque el padrón destino cambia entre corridas.

#### Cómo ejecutarlo

Menú → Búsqueda → Cruce de denominaciones. Tres pasos:

1. **Archivo** — subir CSV o Excel, "Leer columnas", y elegir: nombre a
   comparar, identificador, usuario, teléfono y mail.
2. **Base** — motor, host, puerto, usuario, contraseña, base. Después esquema,
   tabla, columna del identificador, **columna del nombre** y **columna del
   documento** (DNI/RUT/CUIT, según el cliente).
3. **Confirmar** — muestra las 10 primeras filas de los dos lados.

El botón **"Ejecutar cruce" queda deshabilitado hasta que se vio la muestra
del paso 3**. Es intencional.

#### Columnas de salida (22)
```
ID_ARCHIVO · DENOM_ARCHIVO · CLAVE_ARCHIVO · COLUMNA_USADA ·
USERNAME · TELEFONO · EMAIL ·
ID_BASE · DOC_BASE · DENOM_BASE · CLAVE_BASE ·
JARO_WINKLER · JARO_WINKLER_ORD · LEVENSHTEIN · DAMERAU ·
OVERLAP · DICE · JACCARD ·
MOTIVO · COINCIDE · RANKING · FECHA_PROCESO
```

#### Cómo leer el resultado

**El `RANKING` es un orden, no una nota.** Cada fila del archivo genera hasta
5 filas de salida, ordenadas del candidato más parecido al menos parecido.
`RANKING = 1` es el mejor de los encontrados — **no** significa que sea bueno.
Un `RANKING = 1` puede tener `COINCIDE = NO`.

`RANKING = 0` significa que no se encontró ningún candidato.

**El veredicto es `COINCIDE`**, no el ranking:

| | |
|---|---|
| `SI` | los nombres coinciden |
| `RE` | zona gris, **sin dictamen** — va a revisión humana |
| `NO` | no coinciden |

Para el veredicto por contacto, filtrar `RANKING = 1`.

**Umbrales**: `SI` a partir de 0.92, `RE` entre 0.80 y 0.92.

**Los 6 algoritmos no devuelven lo mismo**, a propósito:
- `JARO_WINKLER`, `JARO_WINKLER_ORD`, `OVERLAP`, `DICE`, `JACCARD` → ratio de
  0 a 1, donde 1 es idéntico.
- `LEVENSHTEIN` y `DAMERAU` → **distancia entera**, donde **0 es idéntico**.
  No están normalizados a propósito: la diferencia entre los dos detecta
  transposiciones de caracteres.

`JARO_WINKLER_ORD` compara los nombres con los tokens ordenados
alfabéticamente. Si `JARO_WINKLER` da bajo y `JARO_WINKLER_ORD` da alto, es el
mismo nombre con el orden invertido (`Juan Pérez` vs `PEREZ JUAN`).

#### Casos de prueba

| Archivo | Base | Esperado |
|---|---|---|
| `Juan Pérez` | `PEREZ JUAN` | JW ~0.30, **JWo 1.000**, SI |
| `Ana Muñoz` | `MUNOZ ANA MARIA` | SI, `SUBCONJUNTO_TOKENS` |
| `𝐋𝐮𝐜𝐢𝐚 𝐅𝐞𝐫𝐧𝐚𝐧𝐝𝐞𝐳` | `FERNANDEZ LUCIA` | SI (Unicode decorativo plegado) |
| `jperez@gmail.com` | *(cualquiera)* | NO, `CAMPO_NOMBRE_CONTIENE_EMAIL` |
| `Barraca El Encuentro SRL` | *(personas físicas)* | NO, indicio de jurídica |
| `Rodriguez Nataly` | `RODRIGUEZ NATALIA` | **RE** — ambiguo, correcto que no decida |

#### Qué revisar con atención

1. **Nombres con Unicode decorativo.** Los perfiles de redes usan small-caps
   (`ᴄᴀʟᴅᴇʀóɴ`) y matemáticas (`𝐋𝐮𝐜𝐢𝐚`). Deben plegarse a ASCII en
   `CLAVE_ARCHIVO`. Si una clave queda de 1-2 letras, el registro debe
   apartarse, no compararse.
2. **El teléfono y el mail no participan de la comparación.** Cambiar el
   teléfono de una fila del archivo **no debe cambiar ningún score**.
3. **El documento no se compara.** Se arrastra para identificar a la persona.
4. **La previsualización avisa columnas todas nulas.** Elegir a propósito una
   columna vacía como documento: tiene que salir el aviso en rojo.

---

## Limitaciones conocidas — no reportar como bugs nuevos

Estas ya están identificadas y pendientes de resolver:

| # | Qué pasa | Impacto |
|---|---|---|
| 1 | **`conflictos.py` no está enganchado.** Dos filas del archivo pueden dar `SI` contra la misma persona, o una fila dar `SI` contra varias personas, sin que nada lo marque | **Alto** — si se enriquece la base filtrando solo por `COINCIDE = SI`, se pueden cargar contactos a la persona equivocada |
| 2 | **Coincidencia por un solo token.** `Maite` da `SI` contra `MAITE S.R.L.` porque Overlap devuelve 1.0 cuando un conjunto está contenido en el otro | **Alto** — falsos positivos silenciosos |
| 3 | **El orden del ranking y el veredicto usan criterios distintos.** El candidato 1 puede quedar en `RE` y el 3 en `SI` | Medio — confunde a quien lee la tabla |
| 4 | **El apóstrofo parte los apellidos.** `D'Andrea` → clave `D ANDREA`, no matchea con `DANDREA` | Medio — afecta O'Brien, D'Amico, etc. |
| 5 | **SQL Server no está probado.** El SQL se genera correcto pero solo se ejecutó contra MariaDB | Bajo — no está en uso |
| 6 | **`denominacion` y `comparacion` siguen despachando** aunque no aparecen en el menú | Bajo — intencional, para no romper llamadas existentes |

---

## Cómo reportar

Incluir siempre:

1. **Módulo y categoría** (ej. "Depuración → `dep_telefonos`")
2. **El dato de entrada exacto**, con sus espacios y símbolos
3. **Qué salió** y **qué se esperaba**
4. **El log del panel de progreso** — dice cuántas filas leyó, cuántas insertó
   y si el COUNT de verificación cuadró
5. **El CSV descargado**, si el problema está en los datos de salida

Para el cruce de denominaciones, agregar el `MOTIVO` de la fila: ese campo
explica por qué el algoritmo decidió lo que decidió.
