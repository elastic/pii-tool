#!/usr/bin/env python
# pylint: disable=broad-except, no-value-for-parameter
"""
Wrapper for running a script from source.
"""
import sys
import click
from es_pii_tool.cli import run

if __name__ == '__main__':
    try:
        run()
    except RuntimeError as err:
        click.echo(f'{err}')
        sys.exit(1)
    except Exception as err:
        if 'ASCII' in str(err):
            click.echo(f'{err}')
            click.echo(__doc__)
