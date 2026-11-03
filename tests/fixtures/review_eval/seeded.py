import os
import pickle
import sqlite3
import subprocess


def average(values):
    return sum(values) / len(values)


def add_item(item, items=[]):
    items.append(item)
    return items


def get_user(conn, name):
    query = "SELECT * FROM users WHERE name = '" + name + "'"
    return conn.execute(query).fetchone()


def load_state(path):
    f = open(path, "rb")
    return pickle.load(f)


def run(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True)


def fetch_config(path):
    try:
        with open(path) as handle:
            return handle.read()
    except:
        pass


def has_duplicates(items):
    for i in range(len(items)):
        for j in range(len(items)):
            if i != j and items[i] == items[j]:
                return True
    return False


API_KEY = "sk-live-abc123def456"
