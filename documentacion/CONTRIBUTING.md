# Cómo trabajar en este repo

Notas para mantener el historial limpio y no pisarse entre quienes tocan
el proyecto.

## Antes de tu primer commit

Verificá que el `.gitignore` esté haciendo su trabajo:

```bash
git status
```

Si aparece `padron_conexion.enc`, `jueves_usuario.json`, algún `.csv` o
`__pycache__`, **frená**: no los agregues. El `.gitignore` ya los excluye;
si aparecen es que se agregaron antes de tenerlo.

> Si alguna vez se subió `padron_conexion.enc`, cambiá la contraseña del
> padrón: quedó en el historial de Git y ahí no se borra fácil.

## Ramas

- `main` — siempre funcionando. No se commitea directo.
- `feat/<algo>` — una funcionalidad nueva (ej. `feat/comparacion-dos-origenes`).
- `fix/<algo>` — un arreglo (ej. `fix/lookup-dni-cero`).

Se trabaja en una rama, se prueba, y recién ahí se mergea a `main`.

## Convención de commits

Formato: `tipo: descripción corta en presente`

| Tipo | Cuándo |
|---|---|
| `feat` | funcionalidad nueva |
| `fix` | corrección de un bug |
| `refactor` | reorganización sin cambio de comportamiento |
| `docs` | documentación |
| `test` | tests |
| `chore` | tareas de mantenimiento (deps, config) |

Ejemplos reales de este proyecto:

```
feat: modulo REDES SOCIALES con 6 algoritmos de comparacion
fix: lookup de DNI leia una sola variante de las tres consultadas
refactor: unificar armado de claves de padron en nucleo/claves_padron
refactor: recorte a 30 chars de Oracle absorbido en nombre_tabla_resultado
docs: guia de arquitectura con grafo de modulos
```

## Menos commits, mejores commits

El pedido de "no hacer más commits del necesario" se cumple así:

- **Un commit = un cambio con sentido.** No un commit por archivo tocado.
  Si agregar un proceso toca el registro, un validador y el HTML, es UN
  commit (`feat: ...`), no tres.
- **No commitees ruido.** Con el `.gitignore` puesto, los `.pyc` y las
  salidas dejan de ensuciar el `git status`, así que no hay "commits de
  limpieza".
- **Probá antes de commitear.** Un commit que no pasa los tests obliga a
  un segundo commit de arreglo. Corré `tests/` primero.
- **Amend en vez de commit nuevo** para arreglar el último commit que
  todavía no pusheaste:
  ```bash
  git commit --amend
  ```

## Antes de mergear a main

```bash
python tests/test_comparadores.py
python tests/test_integracion.py
python tests/test_e2e.py
```

Los tres tienen que pasar.
