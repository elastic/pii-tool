#!/usr/bin/env python3
"""Post Docker 'build' script"""
import shutil
from platform import machine, system, python_version

MAJOR, MINOR = tuple(python_version().split('.')[:-1])
SYSTEM = system().lower()
# cx_Freeze builds go into a sophisticated subpath based on these values:
BUILD = f'build/exe.{SYSTEM}-{machine()}-{MAJOR}.{MINOR}'
TARGET = 'executable_build'

# Rename the path of BUILD to be generic enough (TARGET) for Dockerfile to copy
shutil.move(BUILD, TARGET)
