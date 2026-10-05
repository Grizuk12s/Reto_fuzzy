# Motor por SAG: cada reinicio con su SAG (2026-10-02)

## Como funciona (verificado con los dos motores corriendo)
Cada SAG es su propio proceso, con su propio motor y su propio vigilante de
"Auto-aplicar" (`AutoAplicador`, web/state.py). El vigilante compara la huella
de la carpeta de ESE SAG (config/sags/<id>) con la que su motor cargo.

| Cambio                                         | Se reinicia      |
|------------------------------------------------|------------------|
| Crear un wait en SAG 2                         | solo SAG 2       |
| Borrar un wait en SAG 1                        | solo SAG 1       |
| Crear un tag en SAG 1                          | solo SAG 1       |
| Pasar ese tag a Compartido (se agrega a SAG 2) | solo SAG 2       |
| Guardar PostgreSQL en SAG 1 (se copia a SAG 2) | ninguno          |
| Piso del lazo en SAG 2 (en vivo)               | ninguno          |
| Renombrar un SAG (gestion)                     | ninguno          |

## Cambios
- **Pagina Motor**, en cada tarjeta:
  - estado de la config de ESE SAG: "Config al dia" / "Aplicando cambios..." /
    "Cambios por aplicar" / "Cambios sin aplicar" (auto-aplicar apagado) / "No se pudo aplicar";
  - boton **Aplicar cambios** (reinicio en caliente SOLO de ese motor) cuando hay cambios sin aplicar;
  - hora del ultimo reinicio automatico (y sus avisos al pasar el mouse);
  - el interruptor Auto-aplicar se refresca con lo que dice cada SAG.
- **Avisos del registro** del reinicio automatico llevan el nombre del SAG ("SAG 2: Configuracion aplicada (automatico)").
- **Export / Import**: el aviso de "Importacion aplicada" va al registro de cada SAG donde se importo,
  no al del SAG de la pagina.

Respaldo: `_to_delete/respaldo_motor_20261002/`.
