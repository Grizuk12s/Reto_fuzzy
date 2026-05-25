# Sistema Experto Difuso — Prototipo 2 (Standalone)

## Propósito

Sistema experto basado en lógica difusa para el **control automático de un circuito de molienda de minerales**. Este prototipo es **independiente** de la carpeta `core/` original y añade una **interfaz web Flask** para edición en vivo de reglas.

## Contexto Operacional

- **Planta objetivo**: Circuito de molienda (calibrado para Cuajone)
- **Dominio**: Control de proceso minero / molienda SAG
- **Frecuencia de evaluación**: Cada muestra temporal (típicamente cada 5-10 segundos)
- **Modo de operación**: Sugiere ajustes de setpoint; no actúa directamente sobre actuadores

## Estructura del Código

```
Prototipo_2/                       ← Proyecto independiente
├── requirements.txt               ← numpy, pandas, flask
├── README.txt                     ← Documentación completa
├── proyecto_contexto.md           ← Este archivo (contexto para IA)
│
│   NÚCLEO (sin dependencia de core/)
├── config.py                      ← Contrato de datos: roles, columnas, límites
├── fuzzys_templates.py            ← Fábricas de clases fuzzy (Low/High/Norm/Pendiente)
├── fuzzys_models_1A.py            ← Modelos fuzzy concretos (importa fuzzys_templates directamente)
├── fuzzys_eval.py                 ← Evaluación fuzzy, pendientes, etiquetas derivadas
├── motor.py                       ← Motor de reglas con cooldowns por familia
├── reglas_estrategia_correcta.py  ← 24 reglas por defecto (Python, fallback)
├── defuzzy_actions.py             ← Traduce acciones a deltas de setpoint
├── runner.py                      ← Orquestador: itera DataFrame, soporta reglas.json en vivo
│
│   REGLAS EN VIVO
├── reglas.json                    ← Reglas editables en tiempo real (fuente principal)
│
│   INTERFAZ WEB
├── app.py                         ← Flask: API REST + UI para editar reglas y simular
│
│   SIMULACIÓN
└── simulacion.py                  ← Genera datos aleatorios y ejecuta el experto
```

## Diferencias con core/ original

| Aspecto | core/ | Prototipo_2/ |
|---------|-------|--------------|
| Dependencias | Paquete Python (imports relativos) | Archivos sueltos (imports directos) |
| Reglas | Hardcodeadas en .py | Editables en reglas.json vía API/UI |
| Interfaz | Solo API programática | Flask web + API REST |
| Ejecución | Requiere capa integradora externa | Standalone con simulacion.py |
| Modificación en vivo | No soportada | Sí, cambios se aplican por iteración |

## Variables de Proceso

| Variable  | Descripción                | Tipo Fuzzy    | Etiquetas       |
|-----------|----------------------------|---------------|-----------------|
| potencia  | Potencia del molino (kW)   | Low offset    | LOW / OK / HIGH |
| nivel     | Nivel de cajón/sump (%)    | Normalizado   | LOW / OK / HIGH |
| presion   | Presión de ciclones        | Normalizado   | LOW / OK / HIGH |
| p80       | Granulometría P80 (µm)     | High offset   | LOW / OK / HIGH |
| densidad  | Densidad de pulpa          | Normalizado   | LOW / OK / HIGH |

Cada variable tiene un modelo de **pendiente** (DEC / STABLE / INC) por regresión lineal en ventana de 60 s.

## Setpoints Controlados

| Clave  | Descripción           | Familia cooldown    |
|--------|-----------------------|---------------------|
| sp_ton | Tonelaje (ton/h)      | sp_tonelaje         |
| sp_am  | Agua al molino (m³/h) | sp_agua_molino      |
| sp_ac  | Agua al cajón (m³/h)  | sp_agua_cajon       |
| sp_rpm | RPM de bomba          | sp_rpm_bomba        |

## Magnitudes de Acción

Ajustes como fracción del rango [LL, HL]:
- **MUY_FUERTE**: 8% · **FUERTE**: 5% · **STD**: 3% · **SUAVE**: 1.5%

## Edición en Vivo de Reglas

### Formato reglas.json
```json
{
  "id": "1.1",
  "if": [["potencia", "LOW"], ["pend_potencia", "DEC"]],
  "then": ["DISMINUIR_TONELAJE_MUY_FUERTE"],
  "weight": 1.0,
  "priority": 100.0,
  "cooldown_s": 90
}
```

### API REST (Flask en puerto 5000)
| Método | Endpoint | Descripción |
|--------|----------|-------------|
| GET | `/api/reglas` | Listar todas las reglas |
| GET | `/api/reglas/<id>` | Obtener una regla |
| PUT | `/api/reglas/<id>` | Actualizar una regla |
| POST | `/api/reglas` | Crear regla nueva |
| DELETE | `/api/reglas/<id>` | Eliminar una regla |
| POST | `/api/simulacion` | Ejecutar simulación con reglas actuales |
| GET | `/api/meta` | Variables, etiquetas y acciones válidas |

### Interfaz Web
- Abrir `http://127.0.0.1:5000` en el navegador
- Tabla con todas las reglas, condiciones y acciones
- Botones para editar, crear y eliminar reglas
- Botón para ejecutar simulación y ver resultados al instante

## Cómo se ejecuta

```bash
# Instalar dependencias
pip install -r requirements.txt

# Opción A: Simulación por consola
python simulacion.py

# Opción B: Interfaz web
python app.py
# → Abrir http://127.0.0.1:5000
```

## Flujo de Ejecución (por muestra)

1. Extraer valores de proceso y límites fuzzy
2. Evaluar membresías fuzzy (LOW/OK/HIGH)
3. Evaluar pendiente fuzzy (DEC/STABLE/INC)
4. Expandir etiquetas derivadas (NO-HIGH, NO-LOW, etc.)
5. Cargar reglas desde `reglas.json` (edición en vivo)
6. Evaluar reglas ordenadas por prioridad (AND = min membresías)
7. Filtrar por cooldown de familia
8. Traducir acciones a deltas de setpoint + clip

## Dependencias

- Python 3.10+
- `numpy` >= 1.24
- `pandas` >= 2.0
- `flask` >= 3.0

## Convenciones

- Imports directos (no relativos): `import motor`, `from config import ...`
- Reglas en minúsculas para variables, MAYÚSCULAS para etiquetas y acciones
- `reglas.json` es la fuente de verdad para reglas en vivo
- `reglas_estrategia_correcta.py` es el fallback si no existe reglas.json
- Cooldown por **familia de acción**, no por regla individual
