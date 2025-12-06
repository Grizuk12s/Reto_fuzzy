# db_connection.py
import psycopg2
from psycopg2 import sql
import random

def crear_conexion():
    conn = psycopg2.connect(
        dbname="RETO",
        user="user_reto",
        password="reto1234",
        host="localhost",
        port=5432
    )
    conn.autocommit = False
    return conn


def leer_entradas(conn):
    with conn.cursor() as cur:
        query = """
            SELECT id_entradas, n1, n2
            FROM reto_fuzzy.entradas
            ORDER BY id_entradas;
        """
        cur.execute(query)
        filas = cur.fetchall()
    return filas


def leer_salidas(conn):
    with conn.cursor() as cur:
        query = """
            SELECT id_salidas, n1, n2
            FROM reto_fuzzy.salidas
            ORDER BY id_salidas;
        """
        cur.execute(query)
        filas = cur.fetchall()
    return filas


def insertar_salida_random(conn):
    n1 = random.randint(0, 12)
    n2 = random.randint(6, 50)

    with conn.cursor() as cur:
        query = """
            INSERT INTO reto_fuzzy.salidas (n1, n2)
            VALUES (%s, %s);
        """
        cur.execute(query, (n1, n2))

    conn.commit()
    return n1, n2

