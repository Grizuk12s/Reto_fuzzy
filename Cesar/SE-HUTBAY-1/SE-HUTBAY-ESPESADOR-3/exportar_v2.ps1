<#
.SYNOPSIS
    Genera el paquete offline del SE para llevarlo por pendrive a la VM Linux.

.DESCRIPCION
    Reemplaza el procedimiento manual de EXPORTAR_Y_DESPLEGAR.txt (partes A y B).
    Construye la imagen, la exporta a .tar, copia config/ y escribe el
    docker-compose.yml y el LEEME.txt de destino.

    Se ejecuta en la Windows de desarrollo, con Docker Desktop CORRIENDO,
    desde la carpeta del proyecto y en PowerShell (no cmd).

.EJEMPLO
    .\exportar_v2.ps1
    .\exportar_v2.ps1 -Version 2.1 -KepHost 192.168.137.1
    .\exportar_v2.ps1 -Usb E:\se-demo
#>
[CmdletBinding()]
param(
    [string] $Version = "2.0",
    [string] $Dest    = "C:\Users\cvall\OneDrive\Desktop\SE-Conexion-Valida",

    # Host y puerto del KEPserver TAL COMO LO VE LA VM LINUX.
    # OJO: config/espesador/kepserver.json MANDA sobre la variable de entorno
    # KEPSERVER_URL (_load_config hace {**defaults, **json}), asi que el valor
    # del JSON exportado es el que realmente se usa. Por eso se reescribe aqui.
    [string] $KepHost = "192.168.137.1",
    [int]    $KepPort = 49320,

    # Si se indica, ademas copia el paquete al pendrive. Ej: -Usb E:\se-demo
    [string] $Usb = ""
)

$ErrorActionPreference = "Stop"
$proyecto = $PSScriptRoot
$tag      = "se-espesador:$Version"
$tarName  = "se-espesador-$Version.tar"

function Paso($n, $texto) { Write-Host "`n[$n] $texto" -ForegroundColor Cyan }

# ---------------------------------------------------------------- 1. Verificar
Paso 1 "Verificando Docker"
docker version --format '{{.Server.Version}}' | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Docker no responde. Abre Docker Desktop y espera a que el icono quede activo."
}
Write-Host "    Docker OK"

if (-not (Test-Path (Join-Path $proyecto "Dockerfile"))) {
    throw "No encuentro el Dockerfile. Ejecuta el script desde la carpeta del proyecto."
}

# Avisar si la imagen ya existe: dos builds con el mismo tag se pisan y en la VM
# quedaria un .tar que no corresponde a lo que crees.
$existe = docker images --format "{{.Repository}}:{{.Tag}}" | Select-String -SimpleMatch $tag
if ($existe) {
    Write-Host "    AVISO: ya existe la imagen $tag y se va a sobrescribir." -ForegroundColor Yellow
    Write-Host "           Si es un despliegue distinto, usa -Version 2.1" -ForegroundColor Yellow
}

# ---------------------------------------------------------------- 2. Construir
Paso 2 "Construyendo $tag"
docker build -t $tag $proyecto
if ($LASTEXITCODE -ne 0) { throw "El build fallo." }

# ---------------------------------------------------------------- 3. Destino
Paso 3 "Preparando $Dest"
New-Item -ItemType Directory -Force -Path $Dest | Out-Null

# ---------------------------------------------------------------- 4. Exportar
Paso 4 "Exportando la imagen a $tarName"
$tarPath = Join-Path $Dest $tarName
docker save -o $tarPath $tag
if ($LASTEXITCODE -ne 0) { throw "El docker save fallo." }
$mb = [math]::Round((Get-Item $tarPath).Length / 1MB, 1)
Write-Host "    $tarName  ($mb MB)"

# ---------------------------------------------------------------- 5. config/
Paso 5 "Copiando config/ (sin backups)"
$cfgDest = Join-Path $Dest "config"
if (Test-Path $cfgDest) { Remove-Item -Recurse -Force $cfgDest }
Copy-Item -Recurse -Force (Join-Path $proyecto "config") $cfgDest

# Los *.bak que dejan los scripts de mantenimiento no tienen por que viajar.
Get-ChildItem $cfgDest -Recurse -Include *.bak, *.bak2, *.bak3 | Remove-Item -Force
$n = (Get-ChildItem $cfgDest -Recurse -File).Count
Write-Host "    $n archivos"

# ---------------------------------------------------------------- 6. KEPserver
Paso 6 "Apuntando kepserver.json a ${KepHost}:${KepPort}"
# En desarrollo el JSON dice host.docker.internal, que en la VM Linux NO resuelve
# (no hay extra_hosts en el compose de despliegue). Sin esto, el SE arranca pero
# no ve el KEPserver, y el sintoma es confuso porque el compose SI trae la IP
# correcta en KEPSERVER_URL... que el JSON ignora.
$kepJson = Join-Path $cfgDest "espesador\kepserver.json"
if (Test-Path $kepJson) {
    $kep = Get-Content $kepJson -Raw | ConvertFrom-Json
    $kep.host = $KepHost
    $kep.port = $KepPort
    $kep.last_status  = "unconfigured"
    $kep.last_message = "Pendiente de verificar en el servidor de destino."
    $kep | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 $kepJson
    Write-Host "    kepserver.json actualizado"
} else {
    Write-Host "    AVISO: no existe kepserver.json, se creara en el destino" -ForegroundColor Yellow
}

# ---------------------------------------------------------------- 7. compose
Paso 7 "Escribiendo docker-compose.yml"
$compose = @"
name: se-hutbay-espesador-3

services:
  se-espesador:
    image: $tag
    container_name: se-hutbay-espesador-3
    ports:
      - "5000:5000"
    volumes:
      - ./config:/app/config
    environment:
      # Solo aplica si config/espesador/kepserver.json falta o esta corrupto:
      # ese JSON tiene prioridad sobre esta variable.
      - KEPSERVER_URL=opc.tcp://${KepHost}:${KepPort}
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=3).status==200 else 1)"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 20s
"@
Set-Content -Encoding UTF8 -Path (Join-Path $Dest "docker-compose.yml") -Value $compose

# ---------------------------------------------------------------- 8. LEEME
Paso 8 "Escribiendo LEEME.txt"
$leeme = @"
================================================================================
  SE-HUTBAY ESPESADOR -- Paquete offline v$Version
================================================================================

CONTENIDO
---------
  $tarName          Imagen Docker autocontenida (no necesita internet)
  docker-compose.yml            Orquestacion
  config/                       Configuracion actual (contrato, tags, licencia)
  LEEME.txt                     Este archivo


DESPLIEGUE EN LA VM LINUX
-------------------------
  1. Copiar todo a /root/se-demo/
  2. cd /root/se-demo
  3. docker load -i $tarName          -> "Loaded image: $tag"
  4. docker compose up -d
  5. curl http://127.0.0.1:5000/health
  6. Desde Windows: http://<ip-de-la-vm>:5000/

  Si ya habia una version anterior corriendo:
     docker compose down
     docker load -i $tarName
     docker compose up -d
  La carpeta config/ NO se toca, asi que la configuracion se preserva.


CONEXION
--------
  KEPserver ya viene apuntado a ${KepHost}:${KepPort} en
  config/espesador/kepserver.json. Verificar en la UI, panel
  "Conexion KEPserver" -> Probar conexion.

  Postgres se configura desde la vista Postgres de la UI.


ESTADO DE ESTA VERSION -- LEER ANTES DE PROBAR
----------------------------------------------
  El SE **todavia no arranca**, y es el comportamiento correcto: se niega a
  correr con el mapeo incompleto en vez de fuzzificar sobre limites en cero.

  Al pulsar "Iniciar Sistema" va a responder:

     Mapeo de tags incompleto. LIM sin mapear: nivel_hopper_a_lmin, ...

  Faltan 18 tags de limite. El contrato tiene 11 variables de proceso y el
  nucleo exige _lmin y _lmax por cada una: 22 limites, y planta entrego 6.
  Es un dato de planta pendiente, no un problema de configuracion.

  Ademas reglas.json, defuzzy.json, permisivos.json, estados.json y waits.json
  estan vacios: aunque se completen los limites, el motor va a leer, filtrar y
  fuzzificar correctamente y despues no disparar ninguna regla ni mover ningun
  setpoint. Esa logica es lo que falta definir en planta.

  O sea: este paquete sirve para validar conexion, lectura de tags y trazas.
  No para control.

  Detalle completo en CLAUDE.md del proyecto.


PERSISTENCIA
------------
  config/ del host es la fuente de verdad (bind mount). Todo lo que se edite
  en la UI queda ahi y sobrevive a reinicios y a cambios de version.

  Backup antes de una prueba invasiva:
     tar -czf config-backup-`$(date +%Y%m%d-%H%M).tar.gz config/
"@
Set-Content -Encoding UTF8 -Path (Join-Path $Dest "LEEME.txt") -Value $leeme

# ---------------------------------------------------------------- 9. USB
if ($Usb) {
    Paso 9 "Copiando a $Usb"
    New-Item -ItemType Directory -Force -Path $Usb | Out-Null
    Copy-Item -Recurse -Force "$Dest\*" $Usb
    Write-Host "    Copiado. Expulsa el pendrive de forma segura desde el explorador."
}

# ---------------------------------------------------------------- Resumen
Write-Host "`n=== Paquete listo en $Dest ===" -ForegroundColor Green
Get-ChildItem $Dest | ForEach-Object {
    $size = if ($_.PSIsContainer) {
        "{0,8} archivos" -f (Get-ChildItem $_.FullName -Recurse -File).Count
    } else {
        "{0,8:N1} MB" -f ($_.Length / 1MB)
    }
    "{0,-32} {1}" -f $_.Name, $size
}

Write-Host "`nSiguiente paso: copiar la carpeta al pendrive" -ForegroundColor Cyan
Write-Host "  (o volver a correr este script con -Usb E:\se-demo)"
