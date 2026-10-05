# Import / Export con varios SAG — revision (2026-10-02)

## Lo que ya funcionaba (verificado)
- Exportar desde SAG 1 o SAG 2: el archivo lleva la config de ESE SAG (`origen_sag`, nombre de archivo con el id).
- Importar en un SAG: solo cambia ese SAG; los tags nuevos quedan de ese SAG (o compartidos si ya estaban en otro).
- Importar en todos: los tags quedan COMPARTIDOS.

## Lo que se corrigio
1. **"Todos" ahora elige quien escribe al DCS** (nuevo selector "Escribe al DCS", por defecto el SAG de la pagina).
   Antes se aplicaba primero al SAG de la pagina y, si otro SAG ya tenia el SP con rol, la escritura terminaba en el otro.
   Ahora los demas SAG se procesan primero y CEDEN (SP del archivo sin rol, handshake/heartbeat del archivo no se toman);
   el que escribe va al final. Parametro `escritor` en `POST /api/export-import/apply`.
2. **Handshake/heartbeat duplicados se vacian.** Si el que ya tenia un SAG choca con otro SAG, sus tags quedan vacios
   (`dcs_liberados`) en vez de quedar dos SAG con el mismo handshake.
3. **Respaldos que se pisaban.** Dos imports en el mismo segundo usaban la misma carpeta `import_<fecha>`;
   el segundo borraba el respaldo del primero. Ahora `import_<fecha>_2`, `_3`...
4. **Historial de todos los SAG.** La pagina ya no tiene selector de SAG, y el historial solo mostraba el SAG de la pagina.
   Ahora junta el de todos, con columna SAG, y "Restaurar" va al SAG correcto.
5. **Respaldo solo de este SAG.** Restaurar resuelve la carpeta por nombre dentro de `.backup` del propio SAG:
   no se pueden copiar JSON de otra ruta, y los historiales con ruta `/app/...` siguen funcionando.
6. **Deshacer no reabre conflictos.** Al restaurar tags.json se vuelve a aplicar "un solo SAG escribe".
7. **Aviso de motor por destino.** Muestra que SAG de destino tiene el motor corriendo.

## Ojo: estado actual de la config
SAG 1 y SAG 2 tienen hoy los MISMOS SP con rol (PU009_Exp_Speed_Exp, PU009_Velocidad_SP) y el MISMO handshake
(PU009_Exp_Enable_FBK/Ext). Viene de los imports del 2026-10-01 (antes de la regla de un solo escritor).
Se corrige importando ese archivo en "Todos los SAG" con "Escribe al DCS" = el SAG que corresponda.
