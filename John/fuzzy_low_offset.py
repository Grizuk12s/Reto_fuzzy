import seaborn as sns
import pandas as pd
import numpy as np
import time
import skfuzzy as fuzz
import matplotlib.pyplot as plt
from collections import deque

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

def crear_clase_defuzzy(nombre_clase, belief, **columnas):
    """
    Template dinámico para desfuzificación basada en columnas tipo 'inc', 'dec', etc.
    
    Parámetros:
        nombre_clase (str): Nombre de la clase generada.
        belief (list/array): Vector de niveles (ej: [1,0.5,0]).
        columnas (kwargs): Cada conjunto con sus valores (inc, dec, etc.)
    """

    class DefuzzyTemplate:
        def __init__(self):
            self.belief = np.array(belief, dtype=float)

            # Guardar columnas dinámicamente
            for nombre, valores in columnas.items():
                setattr(self, nombre, np.array(valores, dtype=float))

            # Crear funciones de interpolación
            for nombre in columnas.keys():
                vector = getattr(self, nombre)
                setattr(
                    self,
                    f"mf_{nombre}",
                    lambda mu, v=vector: np.interp(
                        mu, self.belief, v
                    )
                )

        #def evaluar(self, inferencia):
        #    """
        #    Evalúa la desfuzificación y devuelve:
        #    - conjunto dominante
        #    - step (valor crisp)
        #    """
        #    mu = float(inferencia)
        #    resultados = {}

            # Interpolar en cada columna
        #    for nombre in columnas.keys():
        #        f = getattr(self, f"mf_{nombre}")
        #        resultados[nombre] = float(f(mu))

            # Conjunto dominante = columna con mayor valor absoluto
        #    dominante = max(resultados, key=lambda k: abs(resultados[k]))

        #    step = resultados[dominante]

        #    return dominante, step, resultados

        def evaluar_manual(self, inferencia, conjunto):
            """
            Permite elegir manualmente el conjunto sobre el cual interpolar.
            Retorna el step correspondiente.
            """
            if conjunto not in columnas:
                raise ValueError(
                    f"Conjunto '{conjunto}' no existe. "
                    f"Conjuntos disponibles: {list(columnas.keys())}"
                )

            mu = float(inferencia)
            fn = getattr(self, f"mf_{conjunto}")
            step = float(fn(mu))

            return step


    DefuzzyTemplate.__name__ = nombre_clase
    return DefuzzyTemplate

def fuzzy_or(a, b):
    """Compuerta OR difusa: devuelve el máximo."""
    return max(float(a), float(b))


def fuzzy_and(a, b):
    """Compuerta AND difusa: devuelve el mínimo."""
    return min(float(a), float(b))


def fuzzy_not(a):
    """Compuerta NOT difusa: complemento respecto a 1."""
    return 1.0 - float(a)

def fuzzy_range(mu, minimo, maximo):
    """
    Devuelve 1 si la inferencia mu está dentro del rango [minimo, maximo],
    de lo contrario devuelve 0.
    """
    mu = float(mu)
    minimo = float(minimo)
    maximo = float(maximo)

    if minimo <= mu <= maximo:
        return 1.0
    else:
        return 0.0


# ===============================================================
#   MOTOR DE REGLAS DIFUSAS COMPLEJAS
# ===============================================================

def motor_fuzzy_complejo(
    conj1, mu1, 
    conj2, mu2, 
    conj3, mu3, 
    conj_pdt, mu_pdt
):

    # -----------------------------------------------------------
    # Diccionario con reglas fuzzy tipo:
    # (C1, C2, C3, Pendiente) : Resultado
    # -----------------------------------------------------------

    reglas = {
        ("alto", "alto", "alto", "subiendo"): "inc_rap",
        ("alto", "OK",   "OK",   "subiendo"): "inc",
        ("OK",   "OK",   "OK",   "estable"):  "inc",
        ("bajo", "bajo", "bajo", "bajando"):  "dec_rap",
        ("bajo", "OK",   "OK",   "estable"):  "dec",
        ("OK",   "bajo", "bajo", "bajando"):  "dec",
        ("alto", "alto", "OK",   "estable"):  "inc",
        ("OK",   "alto", "alto", "subiendo"): "inc_rap",
        ("OK",   "OK",   "bajo", "estable"):  "dec",
    }

    # Si una regla no existe → resultado por defecto
    resultado_defecto = "inc"

    # -----------------------------------------------------------
    # Buscar coincidencia de reglas y calcular su μ
    # -----------------------------------------------------------
    resultado_grados = {}

    for (r1, r2, r3, r4), salida in reglas.items():

        if (r1 == conj1) and (r2 == conj2) and (r3 == conj3) and (r4 == conj_pdt):

            # μ = AND de todos los grados
            mu = fuzzy_and(mu1, mu2)
            mu = fuzzy_and(mu, mu3)
            mu = fuzzy_and(mu, mu_pdt)

            resultado_grados[salida] = mu

    # -----------------------------------------------------------
    # Si no se encontró ninguna regla exacta
    # -----------------------------------------------------------
    if not resultado_grados:
        return resultado_defecto, 0.1

    # -----------------------------------------------------------
    # Seleccionar el conjunto con mayor μ
    # -----------------------------------------------------------
    conjunto_final = max(resultado_grados, key=resultado_grados.get)
    mu_final = resultado_grados[conjunto_final]

    return conjunto_final, mu_final


def crear_filtro_pendiente(max_points=20, max_seconds=5):
    """
    Devuelve un filtro de pendiente tipo función que recuerda su propio historial.
    """
    buffer = deque()

    def calcular_pendiente(t, value):
        nonlocal buffer

        buffer.append((t, float(value)))

        # Limitar por número de puntos
        while len(buffer) > max_points:
            buffer.popleft()

        # Limitar por tiempo
        t_min = t - max_seconds
        while len(buffer) > 2 and buffer[0][0] < t_min:
            buffer.popleft()

        if len(buffer) < 2:
            return 0.0

        # Regresión lineal
        times = np.array([x[0] for x in buffer])
        values = np.array([x[1] for x in buffer])

        t_mean = times.mean()
        v_mean = values.mean()

        dt = times - t_mean
        dv = values - v_mean

        denom = np.dot(dt, dt)
        if denom == 0:
            return 0.0

        slope = np.dot(dt, dv) / denom
        return float(slope)

    return calcular_pendiente