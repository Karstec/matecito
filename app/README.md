# MATEcito

Validación y depuración de datos de contacto e identificación (mails, teléfonos,
CUIT/DNI y denominaciones), con comparación contra el padrón BCRA cuando
corresponde. Servidor web local en FastAPI; se usa desde el navegador.

Repositorio **privado**. Contiene lógica de negocio de clientes; no publicar.

---

## Qué hace

| Módulo | Función |
|---|---|
| Normalización | Explota celdas con varios valores (mails/teléfonos separados por `\|`) a una fila por valor |
| Validación de mails | Corrige typos de dominio y descarta lo que no es un mail real |
| Validación de teléfonos | Valida según el plan de numeración y separa país / característica / número |
| Validación de denominación | Contrasta CUIT/DNI + nombre contra el padrón BCRA (4 estados) |
| Cuitificación | Trae del padrón la denominación de un CUIT/DNI |
| Buscar CUIT/DNI | Consulta puntual al padrón |
| **Redes sociales · Comparación** | Compara dos columnas de nombres con 6 algoritmos, un porcentaje por algoritmo |

---

## Arranque rápido

Requiere Python 3.10+ y estar conectado a la VPN corporativa (para llegar a las
bases de cliente y al padrón).

```bash
# 1. Dependencias
pip install -r requirements.txt

# 2. Configurar el padrón (una vez por PC)
python configurar_padron.py

# 3. Levantar
python run.py
```

Se abre solo en `http://localhost:8000`. Para cerrar: Ctrl+C.

En Windows, `INICIAR_MATECITO.bat` hace los tres pasos y verifica que falte.

---

## Estructura del proyecto

```
matecito/
├── run.py                     ← punto de entrada (chiquito, no se toca)
├── configurar_padron.py       ← asistente de credenciales del padrón
├── requirements.txt
├── matecito/                  ← el paquete
│   ├── app.py                 ← FastAPI: rutas, jobs, orquestación
│   ├── procesos/
│   │   └── registro.py        ← qué procesos existen (una línea por proceso)
│   ├── nucleo/                ← infraestructura reutilizable
│   │   ├── claves_padron.py   ← armado y lookup de claves CUIT/DNI
│   │   ├── lotes.py           ← procesamiento por lotes, escritura transaccional
│   │   └── normalizador.py    ← explosión de celdas multi-valor
│   ├── validadores/           ← lógica pura, sin I/O
│   │   ├── comparadores.py    ← los 6 algoritmos + diagnóstico de MOTIVO
│   │   ├── denominaciones.py
│   │   ├── telefonos.py
│   │   ├── cuit.py
│   │   ├── cuitificador.py
│   │   └── mails/
│   │       ├── agente.py       ← el depurador de mails (ex jueves.py)
│   │       └── listas_referencia.py
│   └── padron/
│       ├── bcra.py            ← acceso al padrón (auto/dblink/snapshot)
│       └── credenciales.py    ← cifrado Fernet de las credenciales
├── static/index.html          ← frontend (HTML+CSS+JS, sin framework)
├── listas/*.ejemplo.txt       ← plantillas de listas editables
├── tests/
└── ../documentacion/
```

### Las tres capas

- **validadores/** — reciben un dato, devuelven un resultado. No saben de bases
  ni de archivos. Se testean solos.
- **nucleo/** — cómo se lee, se agrupa en lotes y se escribe. Genérico, no sabe
  qué proceso lo usa.
- **app.py + procesos/** — pegan las dos capas anteriores a los endpoints HTTP.

---

## Cómo agregar un proceso nuevo

Este es el punto de la reestructuración: **no se toca `run.py` ni casi `app.py`**.

1. Escribir la lógica pura en `validadores/` (recibe datos, devuelve filas).
2. Sumar una entrada al registro en `matecito/procesos/registro.py`.
3. Agregar la rama del proceso donde el registro lo indique.

Ver `../documentacion/AGREGAR_PROCESO.md` para el detalle, con COMPARACIÓN como ejemplo.

---

## Datos sensibles

**Nunca** se versiona: `padron_conexion.enc`, `jueves_usuario.json`,
`matecito_presets.json`, la carpeta `salidas/`, ni ningún CSV/XLSX de cliente.
El `.gitignore` los bloquea. El cifrado del `.enc` es ofuscación, no seguridad
fuerte: sirve para que la contraseña no viaje en texto plano, no para compartir
el archivo.

Para seguridad real (AWS), definir la variable de entorno `MATECITO_KEY` y el
mismo código pasa a modo seguro sin cambios.

---

## Tests

```bash
python tests/test_comparadores.py    # los 6 algoritmos + diagnóstico
python tests/test_integracion.py     # registro, DDL, claves de padrón
python tests/test_e2e.py             # ciclo completo contra SQLite
```
