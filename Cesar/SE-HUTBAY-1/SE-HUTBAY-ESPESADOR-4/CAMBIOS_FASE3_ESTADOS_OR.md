# Fase 3 — Alternativas (OR) dentro de un estado (2026-09-22)

Cierra los puntos 2, 3 y 4 del listado de afinacion.

## El problema

El motor evalua `AND` (minimo), `OR` (maximo) y `NOT` (1 - mu, fail-closed)
desde siempre, y el editor de **reglas** ya tenia su boton *+ Grupo OR*. El
editor de **Estados** no: un estado era un AND de condiciones y nada mas.

Consecuencia practica: el escenario que pide el experto de planta

    Alto = (Alto y NO bajando)  o  (OK y subiendo)
    Bajo = (Bajo y NO subiendo) o  (OK y bajando)

**no se podia guardar como un estado**. Habia que partirlo en dos y rearmarlo
con un grupo OR *dentro de cada regla*: el escenario no existia en ningun lado
como objeto con nombre, cada regla se llevaba su propia copia, y cambiar el
criterio obligaba a abrir regla por regla.

## Que cambio

| Archivo | Cambio |
|---|---|
| `web/api/config.py` | `_normalizar_estado_payload` acepta grupos `OR`; `_refs_de_estado` mira dentro del OR; `_copia_igual` normaliza el OR |
| `web/templates/index.html` | Boton **+ Grupo OR** en el modal de estados, render de los grupos al editar, guardado por hijos directos (conserva el orden), y la tabla de estados usa el mismo renderizador que la lista de reglas |
| `tests/test_estados_or.py` | **Nuevo.** 16 tests, incluida la evaluacion de Alto y de Bajo con grados parciales |

### El NO no necesita operador propio

Cada variable ofrece sus **etiquetas negadas** — `NO-HIGH`, `NO-LOW`, `NO-OK`,
`NO-INC`, `NO-DEC`, `NO-STABLE` —, que el nucleo genera solo para cada etiqueta
definida y valen exactamente `1 - mu`. "NO bajando" se escribe eligiendo
`NO-DEC`, no envolviendo la condicion en un operador. Es lo que ya hacia el
editor de reglas y es mas corto de leer.

### Lo que un grupo OR admite, y lo que no

Adentro van **condiciones sueltas o estados enteros**. No va otro grupo OR:
`(A o B) o C` es `A o B o C` escrito raro, y anidar alternativas hace la
condicion ilegible de un vistazo — justo lo que un estado con nombre viene a
evitar. Un grupo necesita **al menos 2 opciones**: con una sola, la condicion
suelta dice lo mismo.

### La regla de UN SOLO NIVEL ahora se cumple tambien dentro del OR

`_refs_de_estado` no miraba dentro de los grupos OR. Sin ese cambio bastaba con
esconder el anidamiento ahi para armar cadenas de tres niveles, y el backend lo
habria rechazado recien despues de que el operador armo todo. Hay un test que
cubre exactamente ese atajo.

Lo demas de la maquinaria de estados ya recorria los OR y no hubo que tocarlo:
el aviso de *copia vieja* (`_usos_de_estado`) y la propagacion
(`_refrescar_copias_de_estado`).

### El motor no se toco

Las copias de estado viajan dentro de las reglas y `cargar_reglas_json` las
normaliza recursivamente (`_coerce`), diccionarios incluidos. Verificado sobre
la configuracion real de esta planta:

| Escenario | Resultado |
|---|---|
| Alto y NO bajando | 1.00 |
| OK y subiendo | 1.00 |
| Alto pero bajando | 0.00 |
| OK y no subiendo | 0.00 |
| Alto 0.6 / no-bajando 0.8 | 0.60 (manda el minimo de la rama) |

## Como armar el Alto del punto 3

1. **Estados** -> *+ Nuevo Estado*, tipo `subestado`, nombre `Hopper_Alto_A`:
   condiciones `hopper_nvl_pv_a = HIGH` y `pend_... = NO-DEC`.
2. Igual con `Hopper_Alto_B`: `hopper_nvl_pv_a = OK` y `pend_... = INC`.
3. Nuevo estado `Hopper_Alto`, boton **+ Grupo OR**, y dentro *+ estado* dos
   veces: `Hopper_Alto_A` y `Hopper_Alto_B`.

El Bajo es el espejo: `LOW` + `NO-INC`, u `OK` + `DEC`.

Tambien se puede armar en un solo estado sin subestados cuando las ramas son
condiciones sueltas, pero con dos condiciones por rama conviene nombrarlas: el
grupo OR toma estados enteros y asi cada rama se lee por su nombre.

## Estado de los tests

`422 passed, 2 failed, 7 skipped`. Los dos que fallan son los mismos de antes
de la Fase 1 (nombre del SP del contrato, reincidencia de A17/A22).
