================================================================================
  SISTEMA EXPERTO DIFUSO PARA CONTROL DE MOLIENDA MINERAL
  Prototipo 2 — Proyecto Independiente con Interfaz de Edición en Vivo
================================================================================

1. DESCRIPCION GENERAL
----------------------
Este proyecto implementa un sistema experto basado en logica difusa (fuzzy)
para el control automatico de un circuito de molienda de minerales.

Recibe datos de proceso, evalua el estado de las variables con funciones de
membresia fuzzy, calcula pendientes temporales, aplica 24 reglas priorizadas
con mecanismo de cooldown, y produce ajustes incrementales sobre 4 setpoints.

NOVEDAD: Las reglas se almacenan en 'reglas.json' y pueden editarse en vivo
a traves de una interfaz web Flask. Los cambios se aplican inmediatamente en
la siguiente ejecucion de simulacion.


2. ESTRUCTURA DE ARCHIVOS
--------------------------

  Prototipo_2/
  ├── requirements.txt              ← Dependencias Python
  ├── README.txt                    ← Este archivo
  ├── proyecto_contexto.md          ← Contexto del proyecto para IA
  │
  │   NUCLEO (independiente de core/)
  ├── config.py                     ← Contrato de datos de entrada
  ├── fuzzys_templates.py           ← Fabricas de clases fuzzy
  ├── fuzzys_models_1A.py           ← Modelos fuzzy concretos
  ├── fuzzys_eval.py                ← Evaluacion fuzzy + pendientes
  ├── motor.py                      ← Motor de reglas con cooldown
  ├── reglas_estrategia_correcta.py ← Reglas por defecto (Python)
  ├── defuzzy_actions.py            ← Traduccion acciones -> cambios SP
  ├── runner.py                     ← Orquestador (soporta reglas.json)
  │
  │   REGLAS EN VIVO
  ├── reglas.json                   ← Reglas editables (fuente en vivo)
  │
  │   INTERFAZ
  ├── app.py                        ← Servidor Flask (API + UI web)
  │
  │   SIMULACION
  └── simulacion.py                 ← Simulacion standalone con datos random


3. INSTALACION Y USO
---------------------

  a) Instalar dependencias:
     pip install -r requirements.txt

  b) Ejecutar simulacion por consola (sin interfaz web):
     python simulacion.py

  c) Ejecutar interfaz web para editar reglas:
     python app.py
     → Abrir http://127.0.0.1:5000 en el navegador

     Desde la interfaz se puede:
     - Ver todas las reglas con sus condiciones, acciones y prioridades
     - Editar cualquier regla (condiciones, acciones, weight, cooldown)
     - Crear nuevas reglas
     - Eliminar reglas existentes
     - Ejecutar la simulacion y ver resultados en tiempo real


4. VARIABLES DE PROCESO MONITOREADAS
-------------------------------------

  Variable   | Descripcion                       | Tipo Fuzzy
  -----------|-----------------------------------|--------------------
  potencia   | Potencia del molino (kW)          | Low offset (pv-lmin)
  nivel      | Nivel del cajon/sump (%)          | Normalizado (0-1)
  presion    | Presion de ciclones               | Normalizado (0-1)
  p80        | Granulometria P80 (micrones)      | High offset (lmax-pv)
  densidad   | Densidad de la pulpa              | Normalizado (0-1)

  Cada variable tiene:
  - Un modelo fuzzy de estado (LOW / OK / HIGH) con limites dinamicos
  - Un modelo fuzzy de pendiente (DEC / STABLE / INC)


5. SETPOINTS MANIPULADOS
--------------------------

  Clave   | Descripcion            | Familia de cooldown
  --------|------------------------|---------------------
  sp_ton  | Tonelaje (ton/h)       | sp_tonelaje
  sp_am   | Agua al molino (m3/h)  | sp_agua_molino
  sp_ac   | Agua al cajon (m3/h)   | sp_agua_cajon
  sp_rpm  | RPM de la bomba        | sp_rpm_bomba


6. MAGNITUDES DE ACCION
--------------------------

  Magnitud     | Fraccion del rango [LL, HL]
  -------------|----------------------------
  MUY_FUERTE   | 8%
  FUERTE       | 5%
  STD (normal) | 3%
  SUAVE        | 1.5%


7. MECANISMO DE REGLAS
-----------------------

  - 24 reglas base (editables via reglas.json)
  - Prioridad descendente: la de mayor prioridad se evalua primero
  - Condiciones fuzzy evaluadas con AND (min de membresias)
  - Cooldown por familia de accion evita saturacion
  - Reglas compuestas: una condicion puede disparar multiples acciones
  - Etiquetas derivadas: NO-HIGH, NO-LOW, NO-DEC, NO-INC, CERCA_BAJO


8. EDICION EN VIVO DE REGLAS
------------------------------

  Las reglas se almacenan en reglas.json con este formato:

  {
    "id": "1.1",
    "if": [["potencia", "LOW"], ["pend_potencia", "DEC"]],
    "then": ["DISMINUIR_TONELAJE_MUY_FUERTE"],
    "weight": 1.0,
    "priority": 100.0,
    "cooldown_s": 90
  }

  Campos:
  - id:         Identificador unico de la regla
  - if:         Lista de condiciones [variable, etiqueta]
  - then:       Lista de acciones a ejecutar
  - weight:     Peso multiplicativo (0.0 a 1.0)
  - priority:   Prioridad (mayor = se evalua primero)
  - cooldown_s: Segundos entre activaciones (numero o dict por familia)

  API REST disponible:
  - GET    /api/reglas         → Listar todas
  - GET    /api/reglas/<id>    → Obtener una
  - PUT    /api/reglas/<id>    → Actualizar
  - POST   /api/reglas         → Crear nueva
  - DELETE /api/reglas/<id>    → Eliminar
  - POST   /api/simulacion     → Ejecutar simulacion
  - GET    /api/meta           → Variables, etiquetas y acciones validas


9. DATOS DE ENTRADA DEL DATAFRAME
-----------------------------------

  Columna          | Descripcion
  -----------------|--------------------------------------------
  t_s              | Tiempo en segundos
  potencia         | Valor de potencia
  nivel            | Valor de nivel
  presion          | Valor de presion
  p80              | Valor de granulometria
  densidad         | Valor de densidad
  sp_ton           | Setpoint actual de tonelaje
  sp_am            | Setpoint actual de agua molino
  sp_ac            | Setpoint actual de agua cajon
  sp_rpm           | Setpoint actual de RPM bomba
  {var}_lmin       | Limite inferior fuzzy (por variable)
  {var}_lmax       | Limite superior fuzzy (por variable)


10. DEPENDENCIAS
-----------------

  - Python 3.10+
  - numpy >= 1.24
  - pandas >= 2.0
  - flask >= 3.0


================================================================================
  Autor: Sistema Experto v2.0 — Prototipo 2
  Fecha: Abril 2026
================================================================================
