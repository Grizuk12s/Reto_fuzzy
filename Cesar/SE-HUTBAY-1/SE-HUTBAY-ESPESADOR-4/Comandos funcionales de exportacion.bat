REM ===== 0.41 (Licencia: sin licencia no escribe SP ni genera datos, suelta ENABLE_EXT, HB sigue, alerta constante) =====
cd /d "C:\Users\cvall\OneDrive\Desktop\RETO\SE-HUTBAY\Reto_fuzzy\Cesar\SE-HUTBAY-1\SE-HUTBAY-ESPESADOR-3"
set DEST=C:\Users\cvall\OneDrive\Desktop\SE 0.41
mkdir "%DEST%"
docker build -t se-espesador:0.41 .
docker save -o "%DEST%\se-espesador-0.41.tar" se-espesador:0.41
xcopy /E /I /Y config "%DEST%\config"
del /S /Q "%DEST%\config\*.bak"
copy /Y deploy\docker-compose-0.41.yml "%DEST%\docker-compose.yml"
copy /Y deploy\kepserver.json "%DEST%\config\espesador\kepserver.json"
copy /Y DESPLIEGUE.txt "%DEST%\LEEME.txt"
dir "%DEST%"


REM ===== 0.4 (Aceleracion: signo POSITIVA/NEGATIVA/NULA + ESTABLE exige rate y 2a en banda) =====
cd /d "C:\Users\cvall\OneDrive\Desktop\RETO\SE-HUTBAY\Reto_fuzzy\Cesar\SE-HUTBAY-1\SE-HUTBAY-ESPESADOR-3"
set DEST=C:\Users\cvall\OneDrive\Desktop\SE 0.4
mkdir "%DEST%"
docker build -t se-espesador:0.4 .
docker save -o "%DEST%\se-espesador-0.4.tar" se-espesador:0.4
xcopy /E /I /Y config "%DEST%\config"
del /S /Q "%DEST%\config\*.bak"
copy /Y deploy\docker-compose-0.4.yml "%DEST%\docker-compose.yml"
copy /Y deploy\kepserver.json "%DEST%\config\espesador\kepserver.json"
copy /Y DESPLIEGUE.txt "%DEST%\LEEME.txt"
dir "%DEST%"


REM ===== 0.39 (Aceleracion + bumpless: el SP del SE se pega al del DCS con el FBK en 0) =====
cd /d "C:\Users\cvall\OneDrive\Desktop\RETO\SE-HUTBAY\Reto_fuzzy\Cesar\SE-HUTBAY-1\SE-HUTBAY-ESPESADOR-3"
set DEST=C:\Users\cvall\OneDrive\Desktop\SE 0.39
mkdir "%DEST%"
docker build -t se-espesador:0.39 .
docker save -o "%DEST%\se-espesador-0.39.tar" se-espesador:0.39
xcopy /E /I /Y config "%DEST%\config"
del /S /Q "%DEST%\config\*.bak"
copy /Y deploy\docker-compose-0.39.yml "%DEST%\docker-compose.yml"
copy /Y deploy\kepserver.json "%DEST%\config\espesador\kepserver.json"
copy /Y DESPLIEGUE.txt "%DEST%\LEEME.txt"
dir "%DEST%"


REM ===== 0.38 (bandas de handshake en el grafico + panel de limites por serie) =====
cd /d "C:\Users\cvall\OneDrive\Desktop\RETO\SE-HUTBAY\Reto_fuzzy\Cesar\SE-HUTBAY-1\SE-HUTBAY-ESPESADOR-3"
set DEST=C:\Users\cvall\OneDrive\Desktop\SE 0.38
mkdir "%DEST%"
docker build -t se-espesador:0.38 .
docker save -o "%DEST%\se-espesador-0.38.tar" se-espesador:0.38
xcopy /E /I /Y config "%DEST%\config"
del /S /Q "%DEST%\config\*.bak"
copy /Y deploy\docker-compose-0.38.yml "%DEST%\docker-compose.yml"
copy /Y deploy\kepserver.json "%DEST%\config\espesador\kepserver.json"
copy /Y DESPLIEGUE.txt "%DEST%\LEEME.txt"
dir "%DEST%"


cd /d "C:\Users\cvall\OneDrive\Desktop\RETO\SE-HUTBAY\Reto_fuzzy\Cesar\SE-HUTBAY-1\SE-HUTBAY-ESPESADOR-3"
set DEST=C:\Users\cvall\OneDrive\Desktop\SE 0.35
mkdir "%DEST%"
docker build -t se-espesador:0.35 .
docker save -o "%DEST%\se-espesador-0.35.tar" se-espesador:0.35
xcopy /E /I /Y config "%DEST%\config"
del /S /Q "%DEST%\config\*.bak"
copy /Y deploy\docker-compose-0.35.yml "%DEST%\docker-compose.yml"
copy /Y deploy\kepserver.json "%DEST%\config\espesador\kepserver.json"
copy /Y DESPLIEGUE.txt "%DEST%\LEEME.txt"
dir "%DEST%"


cd /d "C:\Users\cvall\OneDrive\Desktop\RETO\SE-HUTBAY\Reto_fuzzy\Cesar\SE-HUTBAY-1\SE-HUTBAY-ESPESADOR-3"
powershell -NoProfile -ExecutionPolicy Bypass -File ".\exportar_v2.ps1" -Version 0.31 -Dest "C:\Users\cvall\OneDrive\Desktop\SE 0.31" -KepHost 192.168.137.1


cd /d "C:\Users\cvall\OneDrive\Desktop\RETO\SE-HUTBAY\Reto_fuzzy\Cesar\SE-HUTBAY-1\SE-HUTBAY-ESPESADOR-3"
set DEST=C:\Users\cvall\OneDrive\Desktop\SE 0.31

docker build -t se-espesador:0.31 .
docker save -o "%DEST%\se-espesador-0.31.tar" se-espesador:0.31

xcopy /E /I /Y config "%DEST%\config"
del /S /Q "%DEST%\config\*.bak"

copy /Y deploy\docker-compose-0.31.yml "%DEST%\docker-compose.yml"
copy /Y deploy\kepserver-0.31.json "%DEST%\config\espesador\kepserver.json"
copy /Y DESPLIEGUE_0.31.txt "%DEST%\LEEME.txt"

dir "%DEST%"