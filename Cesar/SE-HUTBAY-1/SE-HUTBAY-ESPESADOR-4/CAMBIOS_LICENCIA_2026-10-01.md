# Nueva licencia de prototipo hasta el 31-oct (2026-10-01)

Pedido de Cesar: extender la licencia, que vencia el **2026-10-12**, hasta **fin de
octubre**.

## Estado final

| | Antes | Ahora |
|---|---|---|
| Tope del codigo (`LICENCIA_FECHA_MAXIMA`) | 2026-10-12 | **2026-10-31** |
| Licencia local (`config/espesador/licencia.json`) | activada 2026-07-20, vence 2026-10-12 | **activada 2026-10-01, vence 2026-10-31** |
| Licencia de planta (VM) | vence 2026-10-12 | **pendiente de copiar** (ver abajo) |

Vale hasta el **31-oct inclusive** y deja de valer el **1-nov**. Verificado con el
`_license_check` de produccion sobre el archivo instalado, con reloj simulado:

| Momento (hora local) | Resultado |
|---|---|
| 2026-10-01 14:20 | valida |
| 2026-10-31 23:59 | valida |
| 2026-11-01 00:00 | vencida por fecha |

## 1. Tope del codigo

El tope esta duplicado y hay que moverlo en los dos lados:

- `web/api/config.py` -> `LICENCIA_FECHA_MAXIMA = date(2026, 10, 31)`
- `web/state.py` -> `_LICENCIA_FECHA_MAXIMA_ISO = "2026-10-31"`
- `web/templates/index.html` -> los tres textos por defecto de la pagina Licenciamiento.

Tests nuevos en `tests/test_licencia.py`:

- `test_el_tope_de_licencia_es_el_31_de_octubre_en_los_dos_modulos`: falla si las
  dos copias del tope dejan de coincidir.
- `test_activar_recorta_al_tope_del_31_de_octubre`: activar el 1-oct y el 31-oct
  recorta al 31; activar el 1-nov se rechaza (400).

`tests/test_licencia.py`: **22 pasan**.

## 2. La licencia nueva

Se genero con el **endpoint real** `POST /api/licencia/activar` (codigo prototipo,
3 meses, recortado al tope), contra una copia del archivo, y se firmo con el mismo
`_license_sign` del programa. No es un JSON editado a mano: la firma HMAC es la del
codigo.

Dos cuidados:

- **Hora de Santiago al generar.** `last_seen_at` es hora local *naive*. Generada con
  un reloj UTC habria quedado 3 h en el futuro y el equipo local la habria marcado
  **"Reloj del sistema retrocedido" -> manipulada**.
- **`duration_seconds` = 31 dias, no 30.** La activacion calcula
  `(vence - hoy).days` = 30 dias. El contador de uso avanza con el reloj (tambien con
  la app apagada), asi que la licencia habria vencido **por uso el 31-oct a media
  tarde**, antes que por fecha. Con 31 dias manda la fecha. El calculo del boton
  Activar **no se cambio**: una activacion futura desde la pagina tiene el mismo
  detalle.

> El `history` trae la licencia del 20-jul dos veces como "Reemplazada". No es un
> error de esta generacion: es lo que hace la activacion de la pagina (marca la
> entrada activa como reemplazada y ademas agrega la previa).

## 3. Planta: lo que falta

Copia lista en `Claude outputs/licencia_hasta_2026-10-31.json`. Copiarla sobre
`config/espesador/licencia.json` en la carpeta de despliegue de la VM.

- **No hace falta redesplegar.** `_license_check` no mira el tope: solo verifica la
  firma, la fecha y el uso. Este archivo vale tambien con el codigo 0.45 que corre hoy.
- **No hace falta reiniciar.** La licencia se relee del disco en cada revision.
- **Si el contenedor usa UTC**, alla vence el 31-oct a las **21:00 hora de Chile**.
- **Si la pagina dice "Manipulada"**, el reloj de la VM esta atrasado respecto de
  `last_seen_at` (2026-10-01T14:11:45): TRAMPA 8 de `DESPLIEGUE.txt`.
- El tope nuevo (31-oct) llega a planta recien con el proximo despliegue. Hasta
  entonces la pagina de planta muestra el tope viejo (12-oct) como texto, sin efecto
  sobre la licencia instalada.

## Respaldo

`_backup_rediseno_20260922/antes_tope_licencia_20261001/`: `config.py`, `state.py`,
`index.html`, `test_licencia.py`, `CLAUDE.md` y la licencia anterior
(`licencia.json.20261001`).
