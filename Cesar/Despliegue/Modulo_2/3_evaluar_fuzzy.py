from fuzzy_low_offset import *

# ==============================================================
# 2) CREAR LOS MODELOS DIFUSOS QUE USARÁ EL SISTEMA EXPERTO
# ==============================================================

fuzzy_pot_bolas = PotMolBolas()
fuzzy_pot_sag1 = PotSag1()
fuzzy_nivel1 = FuzzyNivel1()
fuzzy_pend = PendienteGeneral()
fuzzy_p80 = P80Model()
fuzzy_presion = PresionModel()
fuzzy_densidad = DensidadModel()



# ==============================================================
# 3) FUNCIÓN ÚNICA PARA LLAMAR A TODOS LOS MODELOS
# ==============================================================

def evaluar_fuzzy(
    pv_bolas, lmin_bolas,
    pv_sag, lmax_sag,
    pv_nivel, lmin_nivel, lmax_nivel,
    pv_p80, lmax_p80,
    pv_pres, lmax_pres,
    pv_dens, lmax_dens,
):

    # ------------------------
    #  Fuzzy principales
    # ------------------------
    dom_bolas, val_bolas, off_bolas, pert_bolas = fuzzy_pot_bolas.evaluar(pv_bolas, lmin_bolas)
    dom_sag, val_sag, off_sag, pert_sag = fuzzy_pot_sag1.evaluar(pv_sag, lmax_sag)
    dom_nivel, val_nivel, off_nivel, pert_nivel = fuzzy_nivel1.evaluar(pv_nivel, lmin_nivel, lmax_nivel)

    dom_p80, val_p80, off_p80, pert_p80 = fuzzy_p80.evaluar(pv_p80, lmax_p80)
    dom_pres, val_pres, off_pres, pert_pres = fuzzy_presion.evaluar(pv_pres, lmax_pres)
    dom_dens, val_dens, off_dens, pert_dens = fuzzy_densidad.evaluar(pv_dens, lmax_dens)

    # ------------------------
    #  Retorno estructurado
    # ------------------------
    return {
        "pot_bolas": {
            "dom": dom_bolas, "val": val_bolas, "offset": off_bolas, "pert": pert_bolas},

        "pot_sag": {
            "dom": dom_sag, "val": val_sag, "offset": off_sag, "pert": pert_sag
                   },

        "nivel": {
            "dom": dom_nivel, "val": val_nivel, "offset": off_nivel, "pert": pert_nivel},

        "p80": {
            "dom": dom_p80, "val": val_p80, "offset": off_p80, "pert": pert_p80,
        },

        "presion": {
            "dom": dom_pres, "val": val_pres, "offset": off_pres, "pert": pert_pres},

        "densidad": {
            "dom": dom_dens, "val": val_dens, "offset": off_dens, "pert": pert_dens,}
    }
