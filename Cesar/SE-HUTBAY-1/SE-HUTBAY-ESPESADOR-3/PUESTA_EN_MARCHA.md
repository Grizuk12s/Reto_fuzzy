# Puesta en marcha — SE HUTBAY

Guía operativa para dejar el Sistema Experto funcionando en una planta nueva.

El producto viene como **plantilla estándar** (control de espesador) y se moldea al
cliente: se cargan sus tags, se declaran sus variables y se escriben sus reglas.
Este documento es el orden en que hay que hacerlo.

> **Estado actual de esta instalación: NO está lista para operar.** El motor se
> niega a arrancar y hay dos defectos que impedirían que actúe aunque arrancara.
> El detalle está en **"¿Está listo para usarse?"**, al final de este documento.
> Ver también `CLAUDE.md`.

---

## 0. Levantar el sistema

### Con Docker (lo normal en planta)

```bash
docker compose up -d --build
docker compose logs -f se-espesador
```

Queda en `http://<ip-del-host>:5000/`.

Al arrancar, el contenedor ejecuta `scripts/seed_tags_planta.py`, que siembra los
tags PCS7 en `config/espesador/tags.json` **sin pisar nada que ya hayas editado**.
Es idempotente: en reinicios no duplica. Para saltarlo: `SEED_TAGS_PLANTA=0`.

Como `./config` es un volumen, toda la configuración sobrevive a los reinicios y a
los rebuilds de la imagen.

### Sin Docker (desarrollo)

```bash
python -m venv .venv
.venv\Scripts\activate          # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

### Verificar

```bash
curl http://localhost:5000/health          # {"status":"ok"}
```

---

## 1. Conectar el KEPserver

Página **Tags KEPserver**, panel *Conexión KEPserver*.

Host y puerto del servidor OPC-UA (por defecto `49320`), botón **Probar conexión**.
La URL efectiva se muestra bajo los campos.

> Dentro del contenedor, `host.docker.internal` apunta al host que corre Docker.
> Si el KEPserver está en otra máquina, se pone su IP directamente.

---

## 2. Cargar los tags

Los tags PCS7 ya vienen sembrados. Para agregar otros, el formulario superior de
**Tags KEPserver** pide tres cosas:

| Campo | Qué es |
|---|---|
| Nombre | Item ID exacto de KEPserver: `Canal.Dispositivo.Tag` |
| Tipo | Float, Int, Boolean o String |
| Categoría | Qué papel juega en el SE (ver abajo) |

### Las cinco categorías

| Categoría | Rol | Lo lee / escribe el SE |
|---|---|---|
| **PV** | Variable de proceso: se fuzzifica y alimenta las reglas | Lee |
| **CRUDA** | Sensor que no se fuzzifica; insumo de variables derivadas y permisivos | Lee |
| **LIM** | Límite `lmin`/`lmax` de una PV; da la escala a la fuzzificación | Lee |
| **SP** | Setpoint: la salida del experto | **Escribe** |
| **OTRO** | Handshake, heartbeat, selectores. No participa del pipeline | Solo monitoreo |

La categoría **no** se deduce del nombre del tag: se elige a mano. Eso permite
cualquier nomenclatura de planta.

### El pseudónimo importa más de lo que parece

El **identificador** de la variable dentro del SE se genera del pseudónimo del tag
(o del último segmento del nombre si no tiene) y **queda congelado**. Es lo que van
a ver las reglas, los filtros y los modelos difusos.

Por eso, antes de seguir:

- Dale a cada tag PV y SP un pseudónimo **claro y único**.
- Dos tags con el mismo pseudónimo generan el mismo identificador y se fundirían en
  una sola variable. El sistema avisa, pero conviene no llegar a eso.

Ejemplo de pseudónimos que dan problemas y su corrección:

| Tag | Pseudónimo malo | Pseudónimo bueno |
|---|---|---|
| `PU009_Velocidad_PV` | Velocidad | Velocidad PU-009 |
| `PU0026_Velocidad` | Velocidad | Velocidad PU-026 |
| `AG004_Nivel_PV_A` | Nivel (transmisor A) | Nivel TK004 A |

Editar el pseudónimo **después** de crear la variable es seguro: no rompe nada.
Cambiar el identificador ya creado sí tiene consecuencias, y por eso es una acción
aparte ("Renombrar") que muestra el impacto antes de aplicar.

---

## 3. Ver los datos moverse

### Lectura en vivo

En **Tags KEPserver**, panel *Lectura de Tags en Vivo*: botón **Iniciar lectura** y
un selector de refresco (500 ms a 10 s). Lee solo PV, CRUDA, LIM y SP — los OTRO no
se consultan. **Detener** congela los valores en pantalla.

### Simular valores (solo antes de tener el DCS real)

Panel *Generador de Datos para KEPserver*. Marca con el checkbox **Sim.** los tags
que quieras mover y pulsa Iniciar. Los desmarcados no se escriben nunca.

- Los tags nuevos aparecen aquí solos, **desactivados**, para no pisar por accidente
  una señal que ya viene de planta.
- Los tags LIM se configuran con un único campo **Valor fijo**: un límite no debe
  moverse.

> **En planta con el DCS conectado, el generador debe estar apagado.** Escribiría
> valores sintéticos encima de las señales reales de proceso.

Alternativa desde terminal, con dinámica más realista (seno lento + ruido):

```bash
docker compose exec se-espesador python scripts/simular_planta_kepserver.py --handshake
```

---

## 4. Declarar el contrato de variables

Página **Contrato de Variables**. Es la lista de variables que el experto reconoce;
todo lo demás se cuelga de ella.

1. Pulsa **Sincronizar con tags**. Propone PV y SP desde tus tags, muestra el impacto
   y, al confirmar, asigna solos los roles de los tags PV y SP.
2. Quita a mano las variables que este cliente no use.

### Regla práctica para decidir qué es PV

**Una PV necesita sus dos tags de límite.** Si una señal no tiene `MIN` y `MAX`, no
se puede fuzzificar: no la declares PV, pásala a CRUDA. Se sigue leyendo igual y
alimenta derivadas y permisivos.

Contar límites disponibles es la forma más rápida de saber cuántas PV soporta la
instrumentación entregada.

### Lo único que queda manual: los límites

Qué PV acota cada tag LIM no se puede deducir del nombre. Ese enlace se hace en
**Tags KEPserver**, columna *Rol en el SE*: al tag `Hopper_Lvl_MAX` se le asigna el
rol `nivel_hopper_lmax`.

> Después de guardar el contrato hay que **reiniciar la aplicación** para que el
> núcleo tome la nueva lista.

---

## 5. Configurar el pipeline

En este orden, porque cada paso depende del anterior.

### 5.1 Variables calculadas

Página **Variables calc.**

- *Variables de entrada (PV)*: botón **+ PV**, se eligen del catálogo, no se escriben.
- *Definiciones calculadas*: nombre libre, parámetros desde el desplegable. El orden
  importa — una definición puede referenciar otra anterior.

### 5.2 Filtros Exp-Q

Página **Filtros**. Botón **Sincronizar con tags PV**.

> **Toda PV necesita filtro.** Si llega una PV sin configurar, el tick falla. Las
> nuevas entran con `q=0.15, ventana_s=50`; se ajustan mirando crudo vs filtrado
> en la página de Traza.

Dos parámetros por variable:

| Campo | Qué es |
|---|---|
| `q` | Cuánto suaviza. `q≈0.05` suaviza mucho con lag alto, `q≈0.15` es el balance, `q≈0.40` casi no filtra |
| `ventana_s` | **Cuántos segundos de proceso mira hacia atrás** |

> **La ventana está en segundos, no en muestras.** Es deliberado: el motor corre en
> ciclo libre y la cantidad de muestras por segundo la fija la latencia de OPC-UA,
> que cambia tick a tick. Con una ventana medida en muestras, el mismo `filtros.json`
> significaba 50 s de suavizado a 5 s por tick y 0,5 s a 50 ms — o sea que el filtro
> dejaba de filtrar al acelerar el lazo, **en silencio**. Con `ventana_s` el
> comportamiento es el mismo corra el lazo a la velocidad que corra.

Un `filtros.json` viejo con `window_size` se sigue leyendo: se traduce a
`ventana_s = window_size × 5` (el período que tenía el lazo entonces), que es
numéricamente equivalente.

### 5.3 Fuzzificación

Página **Fuzzificación**. Se elige una PV y **+ Crear fuzzy**, indicando el tipo:

| Tipo | Offset que mide | Cuándo usarlo |
|---|---|---|
| `high` | `lmax − pv` | Preocupa acercarse al máximo |
| `low` | `pv − lmin` | Preocupa acercarse al mínimo |
| `norm` | `(pv − lmin) / (lmax − lmin)` | Interesa la posición dentro del rango |

Nace con una plantilla neutra de 3 puntos y las etiquetas `HIGH`, `OK`, `LOW`.

### Las filas son libres

`HIGH/OK/LOW` es **solo la plantilla**, no una obligación. La tabla tiene:

- **`+ Fila`** — crea una etiqueta nueva (`ETIQUETA_1`, que después renombrás).
- **La columna `Punto` es editable** — escribís encima para renombrar la fila.
- **`Eliminar`** por fila. Tiene que quedar al menos una.

Así una planta puede usar `CRITICO_ALTO / ALTO / NORMAL / BAJO`, o solo dos
etiquetas, según lo que su operación necesite nombrar.

El eje **`offset` no es una etiqueta**: es la escala. No se renombra ni se borra.

Los grados van de 0 a 1. Cada etiqueta que definas queda disponible en el editor de
reglas junto con su negada `NO-<ETIQUETA>`, que el motor genera solo.

> **Nombres reservados.** `NO-<X>`, `CERCA_ALTO` y `CERCA_BAJO` los genera el núcleo;
> `INC / DEC / STABLE` son de pendiente y `ON / OFF` de permisivo. Se rechazan como
> nombre de fila porque pisarían una etiqueta derivada.

> **Borrar o renombrar una fila que una regla nombra se rechaza**, y te dice qué
> reglas la usan. Visto desde el archivo, un renombre es un borrado más un alta: deja
> la regla igual de muerta. Primero se corrigen las reglas.

### 5.4 Tracking PV-SP

Página **Tracking**. Botón **Sincronizar con tags SP**.

Por cada setpoint se elige un **readback** (la PV que mide el efecto real) y un
**rango** de tolerancia. Mientras `|PV − SP| > rango`, esa familia queda bloqueada y
el motor no emite más acciones sobre ella. Evita que el experto corrija más rápido
de lo que el proceso responde.

Sin readback, el tracking no bloquea nada.

### 5.5 Defuzzificación

Página **Defuzzificación**. Acá viven las **variables manipuladas**: lo que el
experto *mueve*, a diferencia del Fuzzy, que solo *mide*.

**Todas las familias se ven y se editan a la vez**, una tarjeta por tag SP. Cada
tarjeta trae su propia tabla y sus botones (`+/- Columna`, `+ Acción`,
`Borrar familia`); arriba hay un índice para saltar entre ellas y un único botón
**Guardar todo**.

Cada tarjeta muestra además los dos extremos del enlace:

- **Arriba, la variable manipulada**: el tag SP concreto al que escribe, con
  pseudónimo, equipo, unidad y los límites del contrato que lo van a clipear.
- **Abajo, el enlace con los fuzzy**: qué reglas mueven ese setpoint y qué variables
  difusas leen esas reglas. Si no hay ninguna, te lo dice — ese SP no se va a mover.

### Los dos ejes tienen reglas distintas

| | Rango | Por qué |
|---|---|---|
| **Encabezado** (belief) | **0.0 a 1.0, de menor a mayor** | Es la convicción con que disparó la regla, no una magnitud de proceso. El motor interpola sobre este eje: desordenado, devuelve basura sin avisar |
| **Filas** (pasos) | **sin tope, admite negativos** | Están en unidades de ingeniería del SP (%, t/h, g/t). Quien acota es `limites_sp` del contrato al escribir al DCS |

Las filas son **acciones con nombre libre** — el nombre es exactamente lo que las
reglas van a poner en su `then`.

Una misma acción no puede estar en dos familias: el motor no sabría qué setpoint
mover, y el sistema lo rechaza.

### 5.6 Estados, Waits, Permisivos y Reglas

- **Estados / Subestados**: agrupaciones de condiciones reutilizables.
- **Waits**: cooldowns que bloquean una acción durante N segundos tras dispararse.
- **Permisivos**: condiciones que habilitan o inhiben acciones. Es el lugar correcto
  para las protecciones (corriente alta, flujo de sello bajo).
- **Motor de Reglas**: las reglas IF/THEN.

Cada regla tiene cuatro partes:

| Parte | Qué hace |
|---|---|
| `if` | Condiciones en AND difuso (mínimo). Si da 0, la regla se descarta |
| `fuerza` | Condición que aporta la convicción, en OR (máximo) |
| `then` | Acciones, por nombre, que el defuzzy traduce a incrementos |
| waits | Cooldowns por acción |

Los **bloques** dan jerarquía: si dispara algo del bloque `crítico`, el de
`estabilidad` ni se evalúa.

---

## 6. Arrancar el SE

Página **Tags KEPserver**, panel *Sistema*: botón **Iniciar Sistema**.

El motor **se niega a arrancar con el mapeo incompleto** y te dice exactamente qué
falta. No es un error: correr con roles sin asignar produciría fuzzificación sobre
límites en 0 y reglas disparando sobre datos inventados.

Cada ciclo el motor: lee los tags → filtra → fuzzifica → evalúa permisivos → evalúa
reglas → defuzzifica → escribe los setpoints.

### El motor corre en ciclo libre

No hay período fijo: apenas termina de escribir los setpoints, **vuelve a leer**. El
único retardo es un **piso** configurable (`piso_s`, por defecto 0,05 s) al que se le
descuenta lo que tardó el tick, así que es un período mínimo de verdad y no un tiempo
muerto que se suma. Con `piso_s = 0` no espera nada.

Tres consecuencias que conviene tener presentes:

- **El tiempo es real.** `t_s` sale de un reloj monotónico, así que un wait de 900 s
  dura 900 s. (Antes era un contador que sumaba el intervalo nominal por tick.)
- **Los setpoints se escriben solo cuando cambian.** Reescribir el mismo valor
  decenas de veces por segundo golpearía al DCS sin cambiar nada. Si una escritura
  falla, el tick siguiente la reintenta.
- **La velocidad real la fija la fuente de datos, no el bucle.** Contra un KEPserver
  simulado el lazo da más de 1000 tick/s; contra OPC-UA real manda la latencia de
  lectura. Y si el SE gira más rápido que el generador de tags, relee el mismo valor:
  pendiente 0 y filtro convergido a una constante. Para eso está el piso.

En `/api/se/status` el período **se mide**, no se declara: `periodo_ms`,
`ticks_por_s`, `dur_tick_ms`, `dur_tick_max_ms`.

### Persistencia en PostgreSQL

Como el motor gira libre, guardar en cada vuelta inundaría la base y —peor— la base
pasaría a fijar la velocidad del lazo de control. Por eso la persistencia tiene su
propio reloj: se configura en **PostgreSQL** un período de **500 ms o 1000 ms** y la
base recibe un fotograma por período. Los ticks descartados no pierden decisiones: el
SE ya actuó sobre ellos, lo que se ralea es el registro histórico.

Esa página tiene dos botones que responden preguntas distintas:

- **Probar Conexión** — la base responde.
- **Validar Persistencia** — escribe una fila sonda real, la lee de vuelta y la borra.
  Comprueba tablas, permiso de INSERT y si la base llega a tiempo para el período
  elegido. Una conexión sana no garantiza que se esté guardando.

---

## 7. Verificar qué está pasando: la Traza

Página **Traza del Pipeline** (`/espesador/traza`). Es la herramienta principal de
diagnóstico. Muestra las nueve etapas del último tick con los números reales:

1. **Lectura** — cada tag con valor, quality y si falló
2. **Filtro** — crudo vs filtrado, para ver el lag que introduce
3. **Derivadas** — las variables calculadas
4. **Fuzzy** — valor, límites usados, dominio y grados de pertenencia
5. **Permisivos** — cuáles habilitaron y cuáles bloquearon
6. **Reglas** — **todas**, no solo las que dispararon, con el motivo de cada una
7. **Waits activos** — cuánto le falta a cada uno para liberar
7b. **Últimos disparos** — historial corto y global: cuándo disparó cada regla y qué
    le hizo al setpoint. Sobrevive al anillo de la traza
8. **Defuzzy** — setpoint antes → después → delta, y aviso si el SP quedó **saturado**
   en un límite (la regla puede seguir disparando sin efecto)
9. **Escritura** — qué setpoints se escribieron, cuáles no cambiaron, o el error

> En la etapa 9, **"ningún setpoint cambió" es lo normal, no un fallo**: con
> write-on-change, la mayoría de los ticks no escriben nada.

La barra superior se corta en rojo en la etapa donde el tick abortó.

### La pregunta que contesta

*"No disparó nada, ¿por qué?"* — la etapa 6 lo dice regla por regla:

| Estado | Significado |
|---|---|
| `disparo` | Se aplicó |
| `descartada` | Activación en 0, belief bajo el mínimo, o wait activo |
| `no_evaluable` | Referencia variables que no existen en el estado fuzzy |
| `no_evaluada` | Un bloque más crítico ya disparó |
| `invalida` | La regla está mal formada (típico: un wait sin `duracion_s`) |

### Seguir UNA regla en el tiempo

La traza guarda 60 ticks — **segundos**, con el motor en ciclo libre. Si una regla
dispara cada 15 minutos, no la vas a ver disparar ahí nunca. Para eso está el
grabador:

1. En la etapa 6, **hacé click en la fila de la regla** (o en su radio).
2. **● Grabar historial** — empieza a registrar desde ese momento. La fila queda con
   un badge `REC` y la barra muestra el contador `n/500`.
3. **Ver historial ↗** — abre `/espesador/historial?regla=<id>` en otra pestaña.

La página de historial muestra cuántas veces disparó, hace cuánto fue el último
disparo, y la lista de eventos con el motivo, el wait que la bloqueó, las variables
que faltan y **el efecto real sobre el setpoint** (`95.00 → 100.00 (+5.000)`).

Detalles que conviene saber:

- No se guarda un evento por tick: se guarda **por cambio**. Una situación que se
  repite se muestra una vez con `×N`. Los disparos sí son siempre entrada propia.
- **Pulsar Grabar de nuevo borra lo anterior**: el historial arranca de cero.
- Se pueden grabar **varias reglas a la vez** y mirar cada una en su pestaña.
- El historial vive en memoria: aguanta stop/start del motor, pero se pierde si
  reiniciás la aplicación. Si te importa conservarlo, sacá una captura o pedí el
  JSON en `GET /api/se/grabador/<regla_id>`.
- Tope: **500 eventos por regla**. Cuando se llena, lo más viejo se descarta y la
  página lo avisa.

---

## Solución de problemas

| Síntoma | Causa habitual |
|---|---|
| Tags conectados pero en 0, sin moverse | Registros K del driver Simulator: son estáticos, alguien tiene que escribirlos. Usa el generador |
| "Iniciar Sistema" no hace nada | Mapeo incompleto — el motivo sale en rojo bajo el formulario |
| Tag "No existe" con quality Bad | El Item ID no coincide con KEPserver. Revisa el nombre carácter por carácter |
| La traza dice "Sin datos" | El motor no ha ejecutado ningún tick todavía |
| Una PV sin filtro | El tick falla. Sincroniza en la página de Filtros |
| Un SP sin tabla defuzzy | La acción sobre ese setpoint falla al aplicarse. El arranque ahora avisa por nombre qué acciones no tienen tabla |
| La regla dispara pero el SP no se mueve | Está saturado en un límite del contrato. La etapa 8 lo dice explícito |
| "0 disparadas" siempre en la etapa 6 | Con un wait largo, casi todos los ticks caen dentro de la espera. Usa el grabador de la regla |
| Aviso "identificador en colisión" | Dos tags comparten pseudónimo. Corrige uno |

---

## Orden resumido

```
1. Conectar KEPserver
2. Cargar tags + categoría + pseudónimos únicos
3. Contrato de Variables → Sincronizar con tags
4. Tags KEPserver → asignar rol a los LIM (manual)
5. Variables calc. → entradas y derivadas
6. Filtros → Sincronizar
7. Fuzzificación → crear y calibrar
8. Tracking → Sincronizar y definir readbacks
9. Defuzzificación → crear familias y acciones
10. Estados, Waits, Permisivos, Reglas
11. Reiniciar la aplicación
12. Iniciar Sistema y verificar en la Traza
```

---

## ¿Está listo para usarse?

**No todavía.** Verificado ejecutando contra la configuración de esta instalación
(no leyendo el código), en este orden:

### Bloqueador 1 — El motor no arranca: faltan 18 tags de límite

```
Mapeo de tags incompleto.
LIM sin mapear: nivel_hopper_a_lmin, nivel_hopper_a_lmax,
velocidad_bomba_sp_local_lmin, ... presion_pic2391_lmax   (18 en total)
```

El núcleo exige `lmin` y `lmax` por cada PV del contrato. Con 11 PV hacen falta 22
límites y planta entregó 6.

**Esto es un dato de planta, no configuración.** Hay dos salidas:

1. Pedir a planta los 18 tags de límite faltantes.
2. Bajar a **CRUDA** las PV que no tengan sus dos límites. Se siguen leyendo y
   alimentan permisivos y derivadas, pero no se fuzzifican. Es la regla práctica de
   la sección 4: *una PV necesita sus dos tags de límite*.

Atajo posible sin pedir tags: `evaluar_fuzzys` solo usa **ambos** límites cuando el
modelo es `norm`. Un modelo `low` usa solo `lmin` y uno `high` solo `lmax`. El
requisito actual es más estricto que lo que la lógica difusa necesita.

### Bloqueador 2 — El motor en vivo nunca lee `defuzzy.json`

`SEEngine._init_state()` carga de disco las reglas, los permisivos, los filtros y los
modelos difusos, **pero no las tablas de defuzzificación**. El motor usa el
`DEFUZZY_POR_FAMILIA` hardcodeado de `processes/espesador`
(`sp_floculante`, `sp_tonelaje`, `sp_vel_bomba`).

Comprobado: `defuzzy.json` está vacío en disco y el motor igual reporta tres familias
de la plantilla del espesador.

Es exactamente el mismo defecto que ya se corrigió con `fuzzy.json` —*"con el default
hardcodeado, cualquier contrato que no fuera el del espesador reventaba"*— y que quedó
a medias. **Consecuencia: lo que se edite en la página Defuzzificación no llega al
motor en vivo.**

### Bloqueador 3 — Doble nomenclatura de setpoint

| Origen | Nombre |
|---|---|
| `contrato.json` (`SETPOINT_KEYS`, `limites_sp`) | `sp_vel_bomba` |
| Identificador derivado del tag SP | `velocidad_salida_del_se` |

Son **dos nombres para el mismo setpoint**. La página Defuzzificación crea las
familias con el segundo; el motor busca los setpoints con el primero. Al aplicarse
una acción:

```
KeyError: Familia 'velocidad_salida_del_se' no está en setpoints
          (claves: ['sp_vel_bomba'])
```

Se ve también en la página: cada tarjeta muestra en rojo *"sin límites en
contrato.json — se escribiría sin tope"*, porque la búsqueda de límites falla por el
mismo motivo.

El mismo desajuste existe entre el validador de **fuzzy** (acepta variables según los
tags PV) y el de **reglas** (usa las del contrato): se puede definir el fuzzy de una
variable y después no poder escribir la regla que la usa.

### Falta la lógica de control

Aunque se resolviera todo lo anterior, hoy están vacíos:

| Archivo | Estado |
|---|---|
| `reglas.json` | `[]` — ninguna regla |
| `defuzzy.json` | `{}` — ninguna tabla |
| `permisivos.json` | `{}` — **ninguna protección** |
| `estados.json` | `{}` |
| `waits.json` | `[]` |

El motor leería, filtraría y fuzzificaría las 11 variables correctamente, y después
no dispararía nada ni movería ningún setpoint. **Esa es la ingeniería de control que
falta definir en planta**, y es el trabajo más grande que queda.

### Antes de escribir a un DCS real

Estos no impiden probar, pero sí operar sobre proceso real:

- **No hay interlock de habilitación.** El DCS ya provisionó el handshake completo
  (`PU009_Exp_Enable_Ext`, `Enable_FBK`, `Exp_HB`, `LIC_Auto/Manual`), pero el motor
  no lo consulta. Debería ser **fail-closed**: si el enable no se puede leer o tiene
  calidad mala, no se escribe.
- **La calidad de dato es inventada.** `connectors/kepserver.py` usa `get_value()`,
  que descarta el StatusCode y el SourceTimestamp de OPC-UA; el campo `quality` que
  se muestra lo pone el código. Con `get_data_value()` habría calidad real y
  detección de valores congelados.
- **Un PV caído aborta el tick entero.** Falta retener el último valor bueno con
  timeout e inhibir solo las reglas que dependen de la variable degradada.
- **`_save_tags` no tiene lock.** Read-modify-write sin sincronizar, llamado desde el
  generador, el heartbeat y la API. Ya corrompió datos una vez.
- **Sin límites de SP no hay clipeo.** `apply_actions_tabla` solo recorre las familias
  presentes en `limites_sp`: un SP que no esté ahí **se escribe sin tope**.

### Qué sí está listo

- Lectura OPC-UA, filtrado, fuzzificación y trazabilidad completa del pipeline.
- El motor **se niega a arrancar** ante configuración incompleta, en vez de correr
  sobre datos inventados, y dice exactamente qué falta.
- Ciclo libre con reloj real, escritura solo por cambio y persistencia acotada.
- Toda la configuración es editable desde la web y sobrevive a los reinicios.
- 54 tests de regresión sobre el cableado (`python -m pytest tests/ -q`).

### Orden sugerido para dejarlo operativo

```
1. Resolver los límites: pedir los 18 tags o bajar esas PV a CRUDA   [planta]
2. Unificar la nomenclatura de SP (contrato vs identificador de tag) [código]
3. Hacer que SEEngine cargue defuzzy.json                            [código]
4. Escribir permisivos (las protecciones), reglas y tablas defuzzy   [ingeniería]
5. Interlock de habilitación fail-closed + calidad OPC-UA real       [código]
6. Marcha en vacío: SE corriendo con la escritura al DCS deshabilitada,
   verificando en la Traza que decide lo que un operador decidiría
7. Recién ahí, habilitar la escritura
```

Los pasos 2 y 3 son acotados y de código. El paso 1 depende de planta. El paso 4 es
el grueso del trabajo y no lo resuelve el software: es ingeniería de proceso.
