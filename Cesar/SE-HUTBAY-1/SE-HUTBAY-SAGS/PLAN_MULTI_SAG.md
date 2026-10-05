# Plan — varios SAG en un mismo contenedor (2026-10-01)

**Decisión:** un solo contenedor SAG (puerto 5001) con **varios SAG adentro**, cada uno con su
propio motor, su configuración y sus tablas. Máximo **4 SAG** (`MAX_SAGS`).

## Cómo funciona por dentro

- Cada SAG corre como un **proceso propio** del SE (el mismo código del Hopper), escuchando en un
  puerto interno (`sag1` → 5101, `sag2` → 5102). Un error o un reinicio de un SAG no toca al otro.
- Un **router** delante (el que publica el puerto del contenedor) decide a qué SAG va cada
  petición: `?_sag=` → encabezado `X-SE-SAG` → cookie `se_sag` → primer SAG.
- Cada página queda **fijada al SAG con el que se abrió**: todas sus llamadas a la API llevan
  `X-SE-SAG`. Cambiar de SAG en otra pestaña no puede hacer que un *Guardar* caiga en el SAG
  equivocado.
- Configuración: `config/sags/<id>/` (mismo formato que `config/espesador/`). Registro de SAG en
  `config/sags.json`. Tablas de BD: `<id>_entrada` / `<id>_salida`.

## Tags

- **Tags KEPserver** = catálogo común (se cargan una vez).
- **Entrada de datos** = a quién pertenece cada tag: SAG 1, SAG 2 o Compartido.
- Solo los tags de **lectura** (PV, CRUDA, LIM) pueden ser compartidos. SP, handshake y heartbeat
  pertenecen a un solo SAG.

## Fases

| Fase | Qué | Estado |
|---|---|---|
| 0 | Config por SAG, registro `sags.json`, migración (lo actual → `sag1`, `sag2` vacío) | hecha — ver `CAMBIOS_MULTI_SAG_FASE0_1.md` |
| 1 | Router + un motor por SAG, tablas por SAG, página fijada a su SAG, selector mínimo | hecha |
| 2 | Tags: asignación SAG 1 / SAG 2 / Compartido en Entrada de datos (+ gestión de SAG adelantada) | hecha — ver `CAMBIOS_MULTI_SAG_FASE2.md` |
| 3 | Selector de SAG integrado en todas las páginas con color; máximo 4 SAG | hecha — ver `CAMBIOS_MULTI_SAG_FASE3.md` |
| 4 | Página **Motor**: Iniciar/Detener por SAG, HB y handshake por SAG, generador aparte; quitar Diagrama de flujo | hecha — ver `CAMBIOS_MULTI_SAG_FASE4.md` |
| 5 | Vistas con varios SAG: Explorador de Series, "Otros SAG" en Waits y Tracking, alertas por SAG en Motor | hecha — ver `CAMBIOS_MULTI_SAG_FASE5.md` |

## Páginas: un SAG a la vez o todos juntos

- **Configuración** (reglas, fuzzy, filtros, defuzzy, tracking, tags, contrato): un SAG a la vez,
  con selector. Mezclarlos en una tabla invita a editar el setpoint del molino equivocado.
- **Observación** (Motor, Explorador de Series, resúmenes): todos juntos, por color.

## A futuro: varios SAG en la misma página

Idea del 2026-10-01: en vez de cambiar de SAG con el selector, que una página (p. ej. Waits)
muestre la tabla del SAG actual y debajo las de los demás SAG. La base ya está:
`SE_SAG.fetchDe(id, url)` (Fase 3) pide datos de cualquier SAG desde cualquier página. Se puede
hacer página por página sin tocar el backend.
