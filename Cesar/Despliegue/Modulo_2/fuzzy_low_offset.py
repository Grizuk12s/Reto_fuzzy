import seaborn as sns
import pandas as pd
import numpy as np
import time
import skfuzzy as fuzz
import matplotlib.pyplot as plt

def crear_clase_fuzzy_Low(nombre_clase, offset, **conjuntos):
    """
    Crea y retorna dinámicamente una clase difusa nueva basada en los vectores dados.
    """

    class FuzzyTemplate:
        def __init__(self):
            self.offset = np.array(offset, dtype=float)

            # Guardar los conjuntos difusos
            for nombre, valores in conjuntos.items():
                setattr(self, nombre, np.array(valores, dtype=float))

            # Crear funciones de pertenencia dinámicamente
            for nombre in conjuntos.keys():
                vector = getattr(self, nombre)
                setattr(
                    self,
                    f"mf_{nombre}",
                    lambda x, v=vector: np.interp(
                        x, self.offset, v, left=v[0], right=v[-1]
                    )
                )

        def calcular_offset(self, pv, lmin):
            return float(pv) - float(lmin)

        def evaluar(self, pv, lmin):
            offset = self.calcular_offset(pv, lmin)

            pertenencias = {}
            for nombre in conjuntos.keys():
                mf = getattr(self, f"mf_{nombre}")
                pertenencias[nombre] = float(np.clip(mf(offset), 0, 1))

            dominante = max(pertenencias, key=pertenencias.get)
            valor_dominante = pertenencias[dominante]
            return dominante,valor_dominante, offset, pertenencias

        def graficar(self):
            """Grafica los conjuntos difusos definidos."""
            plt.figure(figsize=(10,5))

            for nombre in conjuntos.keys():
                vector = getattr(self, nombre)
                plt.plot(self.offset, vector, label=nombre, linewidth=2)

            plt.title("Conjuntos difusos generados")
            plt.xlabel("Offset")
            plt.ylabel("Pertenencia")
            plt.grid()
            plt.legend()
            plt.show()

    FuzzyTemplate.__name__ = nombre_clase
    return FuzzyTemplate


def crear_clase_fuzzy_high(nombre_clase, offset, **conjuntos):
    """
    Crea y retorna dinámicamente una clase difusa nueva basada en los vectores dados.
    """

    class FuzzyTemplate:
        def __init__(self):
            self.offset = np.array(offset, dtype=float)

            # Guardar los conjuntos difusos
            for nombre, valores in conjuntos.items():
                setattr(self, nombre, np.array(valores, dtype=float))

            # Crear funciones de pertenencia dinámicamente
            for nombre in conjuntos.keys():
                vector = getattr(self, nombre)
                setattr(
                    self,
                    f"mf_{nombre}",
                    lambda x, v=vector: np.interp(
                        x, self.offset, v, left=v[0], right=v[-1]
                    )
                )

        def calcular_offset(self, pv, lmax):
            return float(lmax) - float(pv)

        def evaluar(self, pv, lmax):
            offset = self.calcular_offset(pv, lmax)

            pertenencias = {}
            for nombre in conjuntos.keys():
                mf = getattr(self, f"mf_{nombre}")
                pertenencias[nombre] = float(np.clip(mf(offset), 0, 1))

            dominante = max(pertenencias, key=pertenencias.get)
            valor_dominante = pertenencias[dominante]
            return dominante,valor_dominante, offset, pertenencias

        def graficar(self):
            """Grafica los conjuntos difusos definidos."""
            plt.figure(figsize=(10,5))

            for nombre in conjuntos.keys():
                vector = getattr(self, nombre)
                plt.plot(self.offset, vector, label=nombre, linewidth=2)

            plt.title("Conjuntos difusos generados")
            plt.xlabel("Offset")
            plt.ylabel("Pertenencia")
            plt.grid()
            plt.legend()
            plt.show()

    FuzzyTemplate.__name__ = nombre_clase
    return FuzzyTemplate

def crear_clase_fuzzy_norm(nombre_clase, offset, **conjuntos):
    """
    Crea y retorna dinámicamente una clase difusa nueva basada en los vectores dados.
    """

    class FuzzyTemplate:
        def __init__(self):
            self.offset = np.array(offset, dtype=float)

            # Guardar los conjuntos difusos
            for nombre, valores in conjuntos.items():
                setattr(self, nombre, np.array(valores, dtype=float))

            # Crear funciones de pertenencia dinámicamente
            for nombre in conjuntos.keys():
                vector = getattr(self, nombre)
                setattr(
                    self,
                    f"mf_{nombre}",
                    lambda x, v=vector: np.interp(
                        x, self.offset, v, left=v[0], right=v[-1]
                    )
                )

        def calcular_offset(self, pv, lmin, lmax):
            return (float(pv) - float(lmin)) / (float(lmax) - float(lmin))

        def evaluar(self, pv, lmin, lmax):
            offset = self.calcular_offset(pv, lmin, lmax)

            pertenencias = {}
            for nombre in conjuntos.keys():
                mf = getattr(self, f"mf_{nombre}")
                pertenencias[nombre] = float(np.clip(mf(offset), 0, 1))

            dominante = max(pertenencias, key=pertenencias.get)
            valor_dominante = pertenencias[dominante]
            return dominante,valor_dominante, offset, pertenencias

        def graficar(self):
            """Grafica los conjuntos difusos definidos."""
            plt.figure(figsize=(10,5))

            for nombre in conjuntos.keys():
                vector = getattr(self, nombre)
                plt.plot(self.offset, vector, label=nombre, linewidth=2)

            plt.title("Conjuntos difusos generados")
            plt.xlabel("Offset")
            plt.ylabel("Pertenencia")
            plt.grid()
            plt.legend()
            plt.show()

    FuzzyTemplate.__name__ = nombre_clase
    return FuzzyTemplate

def crear_clase_fuzzy_pendiente(nombre_clase, x, **conjuntos):
    """
    Template dinámico para crear clases de pendiente difusa.

    Parámetros:
        nombre_clase (str): Nombre de la clase a generar.
        x (list/array): Dominio de la pendiente.
        conjuntos: Diccionario de vectores difusos (subiendo, estable, bajando, etc.)
    """

    class FuzzyPendienteTemplate:
        def __init__(self):
            self.x = np.array(x, dtype=float)

            # Guardar dinámicamente los conjuntos difusos
            for nombre, valores in conjuntos.items():
                setattr(self, nombre, np.array(valores, dtype=float))

            # Crear funciones de pertenencia dinámicamente
            for nombre in conjuntos.keys():
                vector = getattr(self, nombre)
                setattr(
                    self,
                    f"mf_{nombre}",
                    lambda p, v=vector: np.interp(
                        p, self.x, v, left=v[0], right=v[-1]
                    )
                )

        def evaluar(self, pendiente):
            """
            Evalúa una pendiente real y devuelve:
            (conjunto dominante, valor, pertenencias, inferencia)
            """
            p = float(pendiente)
            pertenencias = {}

            # Calcular todas las pertenencias
            for nombre in conjuntos.keys():
                mf = getattr(self, f"mf_{nombre}")
                pertenencias[nombre] = float(np.clip(mf(p), 0, 1))

            # Conjunto dominante
            dominante = max(pertenencias, key=pertenencias.get)

            # Valor de inferencia = grado del dominante
            inferencia = pertenencias[dominante]

            return dominante, p, pertenencias, inferencia

    FuzzyPendienteTemplate.__name__ = nombre_clase
    return FuzzyPendienteTemplate


PotMolBolas = crear_clase_fuzzy_Low(
    "pot_mol_bolas",
    offset=[-10, 0, 10, 20, 40, 60, 70, 80],
    bajo=[0, 0, 0, 0.5, 1, 1, 1, 1],
    OK=[0, 0.5, 1, 0.5, 0, 0, 0, 0],
    alto=[1, 0.5, 0, 0, 0, 0, 0, 0]
)
PotSag1 = crear_clase_fuzzy_high(
    "pot_sag1",
    offset=[-10, 0, 10, 20, 40, 60, 70, 80],
    bajo=[0, 0, 0, 0.5, 1, 1, 1, 1],
    OK=[0, 0.5, 1, 0.5, 0, 0, 0, 0],
    alto=[1, 0.5, 0, 0, 0, 0, 0, 0]
)

FuzzyNivel1 = crear_clase_fuzzy_norm(
    "fuzzy_nivel1",
    offset=[-1.1, -1, 0, 0.5, 0.7, 0.8, 1.0, 1.1],
    bajo=[0, 0, 0, 0.5, 1, 1, 1, 1],
    OK=[0, 0.5, 1, 0.5, 0, 0, 0, 0],
    alto=[1, 0.5, 0, 0, 0, 0, 0, 0]
)

PendienteGeneral = crear_clase_fuzzy_pendiente(
    "PendienteGeneral",
    x=[-20, -10, -5, 0, 5, 10, 20],
    subiendo=[1, 1, 0.5, 0, 0, 0, 0],
    estable=[0, 0, 0.5, 1, 0.5, 0, 0],
    bajando=[0, 0, 0, 0, 0.5, 1, 1]
)

P80Model = crear_clase_fuzzy_high(
    "p80_model",
    offset=[-20, 0, 20, 40, 60, 80, 100, 120],
    bajo=[1, 0.5, 0, 0, 0, 0, 0, 0],      # P80 muy bajo = sobre molienda
    OK=[0, 0.5, 1, 0.5, 0, 0, 0, 0],       # objetivo
    alto=[0, 0, 0, 0.5, 1, 1, 1, 1]        # P80 alto = problema
)

PresionModel = crear_clase_fuzzy_high(
    "presion_model",
    offset=[-5, 0, 5, 10, 20, 30, 40, 50],
    bajo=[1, 0.5, 0, 0, 0, 0, 0, 0],
    OK=[0, 0.5, 1, 0.5, 0, 0, 0, 0],
    alto=[0, 0, 0, 0.5, 1, 1, 1, 1]
)

DensidadModel = crear_clase_fuzzy_high(
    "densidad_model",
    offset=[-10, 0, 10, 20, 30, 40, 50, 60],
    bajo=[1, 0.5, 0, 0, 0, 0, 0, 0],
    OK=[0, 0.5, 1, 0.5, 0, 0, 0, 0],
    alto=[0, 0, 0, 0.5, 1, 1, 1, 1]
)