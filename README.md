Validación y depuración de datos de contacto e identificación, con comparación
contra el padrón BCRA. Repositorio **privado**.

Este directorio tiene dos carpetas, separadas a propósito:

```
MATEcito/
├── app/              ← LO QUE SE EJECUTA
│   │                    Solo lo indispensable para correr MATEcito.
│   ├── run.py               punto de entrada
│   ├── matecito/            el paquete (código)
│   ├── static/              interfaz web
│   ├── listas/              listas editables del validador de mails
│   ├── configurar_padron.py
│   ├── requirements.txt
│   ├── INICIAR_MATECITO.bat
│   ├── README.md            arranque rápido
│   └── tests/
│
└── documentacion/    ← LO QUE SE LEE
    │                    Qué es MATEcito, cómo funciona, por qué.
    ├── mapa_interactivo.html    ← abrir con doble clic: mapa de funciones
    ├── ARQUITECTURA.md          las tres capas + grafo de módulos
    ├── DECISIONES_TECNICAS.md   el porqué de cada decisión de diseño
    ├── COMO_PUNTUA_CADA_ALGORITMO.docx  los 6 algoritmos: cálculo y cuándo usar cada uno
    ├── AGREGAR_PROCESO.md       cómo sumar una función nueva
    ├── CONTRIBUTING.md          convención de commits, ramas
    ├── LEEME.txt               guía para el usuario final
    └── arquitectura_modulos.svg
```

## Para correr MATEcito

Todo está en `app/`:

```bash
cd app
pip install -r requirements.txt
python configurar_padron.py     # una vez por PC
python run.py
```

En Windows: doble clic en `app/INICIAR_MATECITO.bat`.

## Para entender MATEcito

Empezá por `documentacion/mapa_interactivo.html` (doble clic, se abre en el
navegador): es un mapa de todas las funciones, qué está desarrollado y qué
falta. Después, `documentacion/ARQUITECTURA.md`.

## Sobre las dos carpetas

- **`app/` no depende de `documentacion/`.** Podés copiar solo `app/` a una PC
  y MATEcito corre igual. La documentación es para quien mantiene o estudia el
  proyecto, no para ejecutarlo.
- **Las listas de `app/listas/` NO son documentación** aunque sean `.txt`: el
  validador de mails las lee en tiempo de ejecución. Por eso viven en `app/`.
- **Los comentarios del código explican el _porqué_ de cada línea** y se
  quedan en los `.py`. La documentación explica el _qué_ y el _cómo_ del
  sistema. Son cosas distintas; ver `documentacion/DECISIONES_TECNICAS.md`.
=======
# matecito
app matecito para validacion y depuracion
>>>>>>> d1035c5a1a2a25e66d705261e0abc846595736f5
