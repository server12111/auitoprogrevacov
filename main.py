"""Точка входа для хостингов, которые запускают `python main.py` из корня.

Эквивалентно `python -m app.main`.
"""
import runpy

if __name__ == "__main__":
    runpy.run_module("app.main", run_name="__main__")
