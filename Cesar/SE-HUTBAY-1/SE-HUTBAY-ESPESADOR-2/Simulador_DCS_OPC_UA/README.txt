================================================================================
  Simulador_DCS_OPC_UA  --  DCS simulado como servidor OPC UA
  Proyecto: SE-HUTBAY-ESPESADOR-1
================================================================================

PARA QUE SIRVE
--------------
Permite probar toda la cadena de integracion sin depender de la planta:

   [dcs_opcua_server.py]        <-- este simulador (hace de DCS / AS301)
          |  OPC UA  (opc.tcp://<pc>:4840/dcs/)
          v
   [KEPserver EX]               <-- canal con driver "OPC UA Client"
          |  OPC UA  (opc.tcp://192.168.137.1:49320)
          v
   [SE-Espesador en VM Linux]   <-- connectors/kepserver.py

Expone las 22 filas de la matriz de tags (26 nodos, porque nivel de hopper y de
TK-004 tienen transmisor A y B) con dinamica coherente y, sobre todo, con el
HANDSHAKE COMPLETO del lazo experto.


CONTENIDO
---------
  tags_planta.json      Catalogo de senales (FUENTE DE VERDAD del simulador)
  dcs_opcua_server.py   Servidor OPC UA + modelo de proceso + handshake
  test_client.py        Cliente de verificacion (lee todo, prueba handshake)
  requirements.txt      asyncua>=1.1.0
  README.txt            Este archivo


COMO EJECUTARLO
---------------
  cd Simulador_DCS_OPC_UA
  pip install -r requirements.txt

  # Servidor (dejar la ventana abierta)
  python dcs_opcua_server.py --host 0.0.0.0 --port 4840 --periodo 1.0

  # Verificacion en otra consola
  python test_client.py                        # lectura de los 26 nodos
  python test_client.py --handshake            # toma de lazo + fail-safe

Endpoint:   opc.tcp://<ip-del-pc>:4840/dcs/
Seguridad:  None  |  Usuario: Anonymous
Namespace:  http://reto.hutbay/dcs-sim   (indice 2)

Si el simulador corre en el PC dev y KEPserver en el Windows destino, abrir el
puerto 4840 TCP en el firewall del PC donde corre el simulador.


================================================================================
  HANDSHAKE SIMULADO  (filas 15 a 22)
================================================================================
El simulador NO entrega el lazo al experto por el solo hecho de escribir
SPEED_EXPERTO. Reproduce la logica que deberia tener el AS301:

   ENABLE_FBK =  ENABLE_EXT                 el SE pide el lazo
             AND SELECTOR_LAZO_EXPERTO      el operador lo habilito en el DCS
             AND LIC_AUTO                   el lazo de nivel esta en automatico
             AND heartbeat vivo             HEART_INT / HEART_BIT cambiaron en
                                            los ultimos 10 s (configurable en
                                            tags_planta.json)

Mientras ENABLE_FBK = 0, el SP del PID ignora a SPEED_EXPERTO y hace control
local de nivel del hopper contra 60 %. Cuando ENABLE_FBK = 1, el SP sigue a
SPEED_EXPERTO recortado entre PU009_SPEED_MIN y PU009_SPEED_MAX.

Esto permite probar los dos casos criticos:
  - Toma de lazo:  ENABLE_EXT=1 + SELECTOR=1 + heartbeat -> FBK=1 y el SP se mueve.
  - Fail-safe:     dejar de escribir el heartbeat -> a los 10 s FBK cae a 0 y el
                   DCS simulado retoma el control solo. El SE debe detectarlo.

Verificado con `python test_client.py --handshake`:
  FASE 1  ENABLE_EXT=1, SELECTOR=0  -> FBK=0   (el DCS no entrega el lazo)
  FASE 2  + SELECTOR=1 + heartbeat  -> FBK=1   (SP_PID 55 -> 78)
  FASE 3  se corta el heartbeat     -> FBK=0 a los ~10 s, control local

IMPORTANTE: esta logica es una SUPOSICION razonable. Hay que pedirle a Control
la logica real del bloque PU009_EXPERTO (ver seccion de preguntas, punto D).


NODEIDS
-------
Cada senal tiene NodeId string igual al tag del DCS:

   ns=2;s=OS_SERVER01::3251LIC1480A/PID.PV_Out#Value
   ns=2;s=AS301/PU009_EXPERTO.SPEED_EXPERTO

Ademas queda navegable por arbol, para "Browse" desde KEPserver o UaExpert:

   Objects > DCS > OS_SERVER01 > 3251LIC1480A > PID > PV_Out#Value
   Objects > DCS > AS301 > PU009_EXPERTO > SPEED_EXPERTO
   Objects > DCS > SIM.Heartbeat            (contador del simulador)


================================================================================
  CONECTAR KEPserver EX A ESTE SIMULADOR
================================================================================
KEPserver actua aqui como CLIENTE OPC UA (al reves de como lo usa el SE).
Eso lo hace el driver "OPC UA Client", que es un driver con licencia aparte:
verificar en KEPserver > Help > Support Information que este habilitado.

  1. KEPserver > New Channel > Driver: "OPC UA Client"
  2. UA Server:  Endpoint URL = opc.tcp://<ip-del-simulador>:4840/dcs/
                 Security Policy = None
                 Message Mode    = None
  3. Authentication: Anonymous
  4. New Device dentro del canal (p.ej. "DCS_SIM")
  5. Import/Browse de tags: el driver navega el arbol y los crea solo. Si se
     crean a mano, usar el NodeId string:
        Namespace = 2 , Identifier Type = String , Identifier = <tag completo>
  6. Verificar con el OPC Quick Client de KEPserver: todos los items en
     calidad Good y cambiando de valor.
  7. Recien ahi, apuntar el SE al endpoint de KEPserver (49320) y cargar los
     nombres de tag de KEPserver en config/espesador/tags.json.

NOTA sobre los nombres: dentro de KEPserver los tags NO se llaman igual que en
el DCS. Quedan como  <Canal>.<Dispositivo>.<Tag>, p.ej. "DCS_SIM.PU009.PV_Out".
Ese nombre de KEPserver es el que va en el JSON de tags del SE, no el tag
original del DCS. Definir la convencion de nombres ANTES de crear los tags.


================================================================================
  PREGUNTAS A CONTROL / INSTRUMENTACION ANTES DE CONECTAR
================================================================================

A) POR CADA SERVIDOR (son TRES origenes distintos)
   Hacer la pregunta por separado para OS_SERVER01, OS_SERVER02 y AS301.
   Ya se sabe que no todos hablan el mismo protocolo.

   - Que protocolo expone: OPC DA, OPC UA, o ambos.
     La sintaxis "SERVIDOR::objeto/bloque.atributo#Value" es tipica de OPC DA
     de ABB 800xA. Si es DA, KEPserver necesita el driver "OPC DA Client" y
     hay que configurar DCOM. Es la decision tecnica mas importante del
     proyecto y define el cronograma.
   - Si es OPC UA:  Endpoint URL completo, puerto, Security Policy exigida
     (None / Basic256Sha256), modo de autenticacion (anonimo / usuario /
     certificado), y quien aprueba el certificado del cliente KEPserver.
   - Si es OPC DA:  ProgID exacto del servidor, version de OPC DA, y si aceptan
     configurar DCOM para un cliente remoto.
   - Version exacta del sistema (800xA 6.x, Freelance, etc.).
   - Limite de sesiones/clientes concurrentes y de items por suscripcion.
   - Existe ya OTRO cliente OPC conectado (PI, historiador, otro APC)?
     >>> Esta es la pregunta de mayor valor: si existe, pedir su configuracion
         exacta y copiarla. Ahorra semanas.

B) RED Y MAQUINAS
   - Cuantas maquinas fisicas son: OS_SERVER01 y OS_SERVER02 pueden estar en
     el mismo equipo o en dos. AS301 es un controlador, probablemente una
     tercera direccion.
   - Por cada una: IP, hostname, FQDN, VLAN/subred, mascara y gateway.
   - Hay ruteo/firewall entre la red donde esta el Windows con KEPserver y
     esas IPs? Quien administra ese firewall y cual es el SLA para abrir
     puertos.
   - Puertos a abrir: 4840 o el que usen (UA); 135 + rango dinamico RPC
     (49152-65535) para DA/DCOM.
   - Confirmar alcance real con ping y con `Test-NetConnection <ip> -Port <p>`
     desde el Windows destino ANTES de tocar KEPserver.

C) DOMINIO Y CUENTAS   (critico solo si algun servidor es OPC DA)
   - A que dominio AD pertenece cada servidor OPC.
   - El Windows que tiene KEPserver, esta en ese mismo dominio? Si no lo esta,
     para OPC DA hay que crear cuentas locales espejo (mismo usuario y misma
     password en ambas maquinas) o unirlo al dominio. No hay tercera opcion
     comoda.
   - Con que cuenta corre el servicio de KEPserver. Por defecto corre como
     SYSTEM, que NO sirve para DCOM remoto: hay que cambiarla a una cuenta de
     dominio con permisos DCOM Launch / Activation / Access en el servidor OPC.
   - Quien crea esa cuenta de servicio y quien otorga los permisos DCOM.
   - Politicas de GPO que puedan revertir la configuracion DCOM.
   - Para OPC UA: usuario/password o certificado, y donde se aprueba
     (trust list del servidor).

D) EL BLOQUE PU009_EXPERTO  (filas 15-22, lo mas delicado)
   - Tipo de dato exacto de cada uno: ENABLE_EXT, ENABLE_FBK, HEART_INT,
     HEART_BIT, LIC_MANUAL, LIC_AUTO, SELECTOR_LAZO_LIC, SELECTOR_LAZO_EXPERTO.
     Bool? Int16? Int32? Real? (en el simulador estan SUPUESTOS).
   - Cual es la logica real de ENABLE_FBK: que condiciones tiene que cumplir
     el SE para que el DCS le entregue el lazo, y en que orden.
   - El heartbeat: se usa HEART_INT, HEART_BIT o los dos? Cual es el periodo
     esperado de escritura y cual es el timeout del watchdog en el AS301?
     Que hace el DCS al vencer el timeout: baja ENABLE_FBK, congela el SP,
     pasa a manual?
   - HEART_INT: hasta que valor cuenta antes de dar la vuelta (rollover)?
   - SELECTOR_LAZO_LIC y SELECTOR_LAZO_EXPERTO: son excluyentes? Quien los
     mueve, el operador desde el grafico o el SE?
   - LIC_MANUAL / LIC_AUTO: son dos bits independientes o complementarios?
     Puede darse el caso de que los dos esten en 0.
   - Bumpless transfer: al entrar y al salir del modo experto, quien iguala el
     SP para que no haya salto en la bomba.
   - Rate limit: cuanto puede mover el SE el SP por ciclo y con que frecuencia
     se le permite escribir.
   - Los limites *_MAX / *_MIN: los escribe el SE o son de solo lectura para
     el? Quien recorta si el SE pide algo fuera de rango, el DCS o el SE?
   - Confirmacion formal de que el bloque acepta escritura desde un cliente
     OPC externo, y con que credenciales.

E) SEMANTICA DE LAS ANALOGICAS
   - Unidad y rango de instrumento de cada PV (los que faltan en la matriz:
     filas 2,3,4-SP_experto,5,6,10,11 no traen unidad).
   - Vienen escaladas a unidades de ingenieria o crudas (0-4095, 4-20 mA)?
   - Periodo real de actualizacion del dato en el DCS. El SE evalua cada 60 s;
     un dato que refresca cada 5 min no sirve.
   - Que hace el tag cuando el instrumento falla: se congela, va a 0, marca
     calidad Bad. El SE necesita distinguir "0 real" de "sin dato".
   - Hay tags de calidad/estado asociados? Pedirlos, valen tanto como el PV.
   - Transmisores A/B (1480A/1480B, L2352A/L2352B): cual manda, hay tag de
     seleccion, o hay que promediar/votar en el SE?
   - Estado de los equipos: PU-026 parece stand-by. Hay tag de "bomba
     corriendo / parada / en local"? El SE no debe recomendar sobre una bomba
     parada.

F) SENALES QUE FALTAN PARA EL SE DE ESPESADORES
   El config/espesador/tags.json del SE usa variables que NO estan en la
   matriz. Pedirlas explicitamente o declararlas fuera de alcance:
     - Torque de la rastra del espesador  (RETO.PV.torque)
     - Nivel de cama / bed mass           (RETO.PV.bed_level, bed_mass)
     - Densidad del underflow             (RETO.PV.densidad)
     - Presion diferencial                (RETO.PV.presion_diferencial)
     - Dosificacion de floculante (SP y PV)
     - Tonelaje / flujo de alimentacion al espesador
     - Turbidez o solidos del overflow

G) ADMINISTRATIVO
   - Licencias de KEPserver: driver OPC UA Client y/o OPC DA Client, y limite
     de tags de la licencia actual.
   - Quien autoriza formalmente el permiso de escritura sobre el AS301.
   - Ventana de prueba acordada con Operaciones para el primer lazo cerrado.
   - Contacto tecnico de guardia durante la prueba.


================================================================================
  PLANTILLA PARA PEDIR LOS TAGS
================================================================================
Enviar la matriz con estas columnas agregadas y pedir que la completen:

  Tag DCS | Servidor/Nodo | Protocolo (DA/UA) | IP:Puerto | Tipo dato |
  Unidad | Rango min | Rango max | Periodo act. | R/W | Tag de calidad |
  Tag de estado equipo | Observaciones

================================================================================
