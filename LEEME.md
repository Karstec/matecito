# MATEcito — puesta en marcha

## 1. Requisitos

- Python 3.10 o superior
- FortiClient VPN levantada si vas a conectarte a bases de clientes
- Oracle Instant Client **solo** si algún servidor Oracle es anterior a 12.1
  (Santa Fe). Para el resto alcanza el modo thin, que no necesita nada.

## 2. Instalación

```bash
cd app
py -m venv .venv
.venv\Scripts\activate          # Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt
```

## 3. Configurar el padrón BCRA — único paso pendiente

```bash
cd ..
python configurar_padron.py
```

Te pide motor, host, puerto, usuario, contraseña, base y tabla del padrón, y
los guarda cifrados en `padron_conexion.enc`, en la raíz del proyecto. Ese
archivo está en el `.gitignore`: no se sube al repo.

Verificar después:

```bash
python configurar_padron.py --ver       # qué quedó guardado (sin la contraseña)
python configurar_padron.py --probar    # intenta conectarse de verdad
```

**Sin este paso, MATEcito arranca igual.** Lo único que no funciona son los
tres procesos que consultan el padrón: CUIT/DNI en lote, denominación contra
CUIT, y el buscador puntual. Todo lo demás —normalización, depuración,
validación de mails y teléfonos, OSINT, y los dos procesos de redes
sociales— corre sin padrón.

## 4. Levantar

```bash
cd app
python run.py
```

Abre en `http://localhost:8000`.

---

## Las cuatro categorías

El menú sigue el orden del trabajo real: normalizar el archivo, depurar el
dato, juzgarlo, y consultar sin modificar nada.

### Normalización
Separa columnas con varios valores usando el pipe `|`. Teléfonos, mails o
ambos. Es lo primero que se corre sobre un export sucio.

### Depuración — *transforma, no juzga*
- **Mails**: acentos, `arroba`/`punto` escritos como texto, typos de dominio
  (`gmeil.com` → `gmail.com`). No da de baja nada.
- **Teléfonos**: quita paréntesis, guiones y etiquetas; parte celdas con
  varios números; separa prefijo de país y numeración; agrega `+54` / `+598`.
  No dice si el número existe.

Un dato que la depuración no tocó **no es un dato malo**: es un dato que ya
estaba bien escrito.

### Validación — *juzga, no transforma*
- **Mails (OSINT)** y **Mails (reglas)**, por separado
- **Teléfonos**: FIJO / MOVIL, con `phonenumbers`
- **Denominación vs CUIT**: contra el padrón BCRA *(requiere el paso 3)*

### Búsqueda — *consulta, no modifica*
- **CUIT / DNI puntual**: ventana de consulta *(requiere el paso 3)*
- **CUIT/DNI en lote**: listado → datos del padrón *(requiere el paso 3)*
- **Denominaciones (2 columnas)**
- **Redes · 6 algoritmos**: dos columnas de la misma tabla
- **Redes · Cruce contra base**: CSV/Excel contra una tabla de la base

---

## El cruce de redes sociales

El flujo es distinto al del resto: arranca por el archivo, no por la
conexión.

1. Conectate a la base desde la pantalla principal
2. Menú → Búsqueda → **Redes · Cruce contra base**
3. Subí el CSV o Excel y pulsá "Leer columnas"
4. Elegí del archivo: nombre a comparar, identificador, teléfono, mail
5. Elegí de la base: esquema, tabla, columna del identificador, columna del
   nombre y **columna del documento** (DNI / RUT / CUIT, según el cliente)
6. Ejecutar

Genera una tabla nueva `{USUARIO}_{CLIENTE}_{timestamp}` con las 6 métricas,
el documento, el teléfono y el mail arrastrados, y el estado `SI` / `RE` /
`NO`. **La tabla origen no se toca.**

El teléfono y el mail viajan a la tabla resultante pero **no participan de la
comparación ni del desempate**. Que el nombre coincida habilita a evaluar si
ese contacto corresponde a esa persona; no lo decide. Vincular el contacto es
la etapa siguiente y tiene otros criterios.

---

## Motores soportados

MariaDB / MySQL, Oracle y SQL Server. Las diferencias de SQL están aisladas
en `app/matecito/nucleo/dialecto.py`: tipos de columna, marcadores de
parámetro, comillas de identificador y paginado.

SQL Server necesita descomentar `pyodbc` en `requirements.txt`.

---

## Listas de referencia

En `app/listas/`, un `.txt` por lista (dominios válidos, inválidos,
institucionales, usuarios sin mail, correcciones de dominio y de TLD, TLDs
finales e intermedios, países propios). Los archivos `.ejemplo.txt` son la
plantilla: copiá el que necesites sin el `.ejemplo` y editalo. Si no existe
la versión propia, se usan los valores por defecto del código.

---

## Seguridad de las operaciones

Ningún proceso modifica la tabla origen. Todos crean una tabla nueva con
timestamp, así que dos corridas nunca se pisan. La escritura va en una sola
transacción con COUNT de verificación antes del COMMIT: si el destino no
tiene la misma cantidad de filas que se esperaba, se hace ROLLBACK y el
proceso falla en vez de dejar una tabla a medias.

---

## Problemas frecuentes

**`DPY-3010` al conectar a Oracle** — el servidor es anterior a 12.1 y
necesita modo thick. Dejá una carpeta `instantclient*` junto a la app o
definí `MATECITO_ORACLE_LIB` con la ruta.

**El archivo CSV se lee con caracteres raros** — MATEcito prueba utf-8,
cp1252 y latin-1 en ese orden. Si igual sale mal, el archivo probablemente ya
venía dañado desde el export.

**"No se encontró la fila de encabezado"** — el lector busca la fila que
contenga la columna de nombre esperada. El mensaje de error dice qué
codificación y qué delimitador detectó, y muestra la primera fila leída.

**Un proceso del padrón dice que no hay credenciales** — falta el paso 3.
