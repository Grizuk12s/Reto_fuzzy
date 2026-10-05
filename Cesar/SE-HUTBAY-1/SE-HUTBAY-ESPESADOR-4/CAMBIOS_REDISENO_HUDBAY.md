# Rediseño visual — identidad Hudbay (2026-09-22)

Cambio **solo de interfaz**. No se tocó ni una línea de Python: motor, blueprints,
endpoints y configuración quedan exactamente igual.

## Paleta

| Rol | Antes | Ahora |
|---|---|---|
| Fondo de página | `#0f172a` slate | `#101a20` |
| Barra lateral / nav | `#0b1322` | `#0a1216` |
| Tarjetas y tablas | `#1e293b` | `#152128` **(carbón Hudbay)** |
| Bordes | `#334155` / `#475569` | `#23323b` / `#35474f` |
| Texto | `#e2e8f0` / `#94a3b8` | `#e8eef1` / `#a2b1b9` |
| Acento de dato (azul) | `#38bdf8` | `#4fb3d9` |
| Acción primaria / marca | `#3b82f6` azul | `#ee3524` **(rojo Hudbay)** |

Los colores **con significado de proceso no se tocaron**: verde OK `#22c55e`,
rojo alarma `#ef4444`, ámbar aviso `#f59e0b`, las pastillas de dinámica
(ACELERANDO / DESACELERANDO / ESTABLE) y las de signo siguen igual. Era
deliberado: en una sala de control el color de estado es información, no
decoración.

Por eso mismo el **botón destructivo pasó a contorno** (`.btn-danger`): con la
acción primaria ahora en rojo de marca, un "Guardar" rojo sólido y un "Eliminar"
rojo sólido se confundían. Ahora Guardar es rojo relleno y Eliminar es rojo
contorneado.

## Cómo está montado

- **`static/hudbay.css`** — capa de marca. Se carga con un `<link>` inyectado
  antes de `</head>` en las 11 plantillas, es decir **después** del `<style>`
  propio de cada página, así que manda sobre él. Solo toca "cromo": marca,
  navegación activa, títulos, acción primaria, foco.
- El resto del remapeo se aplicó **directo sobre los hex** de cada plantilla
  (no había variables CSS; los colores estaban escritos a mano ~15.000 líneas).
- **`static/hudbay-logo.png`** — wordmark en blanco con el punto rojo, para
  fondo oscuro. En la barra lateral de `index.html` y en la nav superior de
  entrada, gráficos, diagrama, postgres y export/import.
- **`static/favicon.png`** — cápsula roja sobre carbón, derivada del icono.

## Respaldo

`_backup_rediseno_20260922/` tiene las 11 plantillas y `static/` tal como
estaban. Para revertir: copiar esas carpetas de vuelta y borrar `hudbay.css`,
`hudbay-logo.png` y `favicon.png`.

## Pendiente / a revisar en marcha

- Las pastillas moradas (`#a855f7`) del Explorador de Series y el badge
  "PRUEBA DEMO" quedaron como estaban. Funcionan, pero son el color que más
  se sale de la carta Hudbay si se quiere una segunda pasada.
- Las capturas de verificación se tomaron sirviendo las plantillas estáticas
  sin backend, así que las zonas que se llenan por API salían vacías. Conviene
  una mirada con el SE corriendo, sobre todo a los colores de series de
  Chart.js con datos reales.

---

## Segunda pasada — logos (2026-09-22)

### El logo ReTO, de JPEG a PNG con fondo transparente

El original venía en JPEG con fondo `#F7F7F7`. Recortar por umbral deja halo
claro alrededor de cada trazo cuando el logo va sobre fondo oscuro, así que se
hizo bien: se calcula el alfa por canal (`a = (fondo - min(R,G,B)) / (fondo - K)`)
y se **despremultiplica** el color contra el fondo original
(`F = (C - fondo·(1-a)) / a`). Resultado: bordes antialiaseados limpios sobre
cualquier fondo, sin halo y sin dientes de sierra.

| Archivo en `static/` | Qué es |
|---|---|
| `reto-digital.png` | Logo fiel, fondo transparente, 900 px. Para fondo claro. |
| `reto-digital-dark.png` | Variante para UI oscura: los trazos gris carbón pasan a gris claro; el naranja se mantiene. Es la que usa la interfaz. |
| `reto-digital-original.png` | Resolución completa (1526x721), fondo transparente, sin tocar colores. Para imprenta o reutilización. |

El gris original `#4A4E57` sobre carbón `#152128` quedaba casi ilegible: por eso
la variante oscura, no por gusto.

### Dónde va cada logo, y por qué

**Hudbay = dónde se opera. ReTO = quién lo hace.**

- **Portada (`/`, `bienvenida.html`)** — es la portada del producto: el logo
  **ReTO** manda, grande y centrado, en lugar del título con degradado
  morado/azul que tenía. Arriba, una franja de cliente discreta con el
  wordmark **Hudbay** y la etiqueta "Planta".
- **Páginas internas** — manda **Hudbay**: barra lateral del editor y nav
  superior del resto. El operador pasa el turno ahí y lo que necesita saber de
  un vistazo es en qué planta está. Dos logos compitiendo en la nav roban el
  espacio horizontal que ya usan seis enlaces.
- **Firma ReTO** — pequeña y al 45% de opacidad al pie de la barra lateral,
  enlazando a la portada. Marca autoría sin competir. En barra colapsada se
  oculta: no hay ancho para el wordmark y cualquier recorte queda ilegible.

### Otros ajustes de la portada

- El hover de las tarjetas pasó de cyan a rojo Hudbay.
- El badge "Demo" dejó el morado por ámbar.
- El centrado vertical pasó de `justify-content:center` a `flex-start` +
  `margin:auto`: con el logo, el contenido desbordaba y el centrado flex
  **recorta por arriba sin dejar scroll**. El pie dejó de ser `position:fixed`
  porque pisaba la tarjeta de Flotación.
- `.sidebar>*{flex-shrink:0}`: al agregar la firma, los hijos del flex en
  columna se comprimían y el logo de Hudbay salía cortado por abajo.
- Barra colapsada: el wordmark de Hudbay se cambia por el icono (`favicon.png`).

### Pendiente

El favicon es el icono de Hudbay en todas las páginas, portada incluida. Si se
prefiere que la portada lleve el diamante de ReTO, hay que generar ese icono
aparte: recortarlo del logo corta las líneas naranjas, así que conviene dibujarlo.

---

## Tercera pasada — barras de scroll (2026-09-22)

Venían nativas del sistema (blancas en Windows) y partían el tema oscuro por la
mitad, sobre todo la de la barra lateral. Ahora se pintan en la carta Hudbay,
en `static/hudbay.css`:

- Pulgar `#2c3d46`, aclara a `#41565f` al pasar el mouse y se pone **rojo Hudbay
  mientras se arrastra** — así el scroll se lee como parte de la interfaz.
- Riel transparente: hereda el fondo de lo que esté debajo, así funciona igual
  en la página, en los modales y en las tablas con scroll propio.
- La barra lateral lleva un pulgar más apagado (`#33454e`) y más delgado (8 px,
  5 px si está colapsada): sobre un fondo más oscuro, un pulgar con el mismo
  contraste que el resto se leería como un elemento más del menú.
- Declarado con `::-webkit-scrollbar` (Chrome / Edge) y `scrollbar-color`
  (Firefox).

---

## Cuarta pasada — tres ajustes finales (2026-09-22)

### 1. "Entrada de Datos" ya no va en rojo

Ese enlace traía un `style="color:#4fb3d9;font-weight:600"` **en línea** desde
antes del rediseño, y en la primera pasada se remapeó a rojo junto con el resto
del cromo. Mal criterio: en esta interfaz el rojo pasó a significar "ítem
activo", así que un enlace permanentemente rojo competía con el que sí lo está.
Se le quitó el `style` entero y ahora se ve como los demás enlaces externos.

### 2. Versión en el pie de la portada

`SE-HUTBAY v2` → `SE-HUTBAY v1:A0.44`.

### 3. La barra de Últimas Ejecuciones deja de tapar el contenido

Es `position:fixed` para quedar anclada abajo mientras la página hace scroll, y
eso está bien; el problema era que al expandirse (260 px) se comía el final del
contenido. En vez de sacarla del `fixed` — lo que la haría desaparecer al hacer
scroll — **`.main` reserva abajo la altura de la barra**, igual que las barras
laterales reservan su ancho:

- `toggleActivityBar()` (nueva, en `index.html`) pone `.act-expanded` en el
  `body`; el CSS hace el resto: `.main` pasa de `padding-bottom:54px` a `278px`,
  con transición.
- El estado se guarda en `sessionStorage` y se restaura al recargar. La
  restauración va en el script que está **después** de la barra en el DOM: el
  bloque de arranque de más arriba corre antes de que la barra exista, y ahí
  `getElementById('activityBar')` todavía devuelve `null`.
- Se restaura **antes** de `_renderActivityBar()`, para que un fallo del render
  no deje la barra en un estado distinto del que el usuario dejó.
- `toggleSidebar()` ahora también marca `.sb-collapsed` en el `body`, y la barra
  inferior alinea su borde izquierdo con eso (`left:220px` → `52px`). Antes
  quedaba un hueco de 168 px cuando se colapsaba el menú.

Verificado en navegador: `padding-bottom` 54 → 278 → 54 al abrir y cerrar,
`left` 52 con el menú colapsado, y el estado sobrevive a la recarga.
