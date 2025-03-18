"""Load environment variables for tests."""

import typing as t
from os import path
import logging
import subprocess
import pytest
from dotenv import find_dotenv, load_dotenv

# pylint: disable=C0116

logger = logging.getLogger(__name__)

cli_params = {
    'docker_create': 'False',
    'docker_destroy': 'False',
    'es_version': '8.17.2',
}


# This conftest.py is the one that is seen first. The ones in the unit and
# integration subdirectories are seen later.
# This is why we have the docker environment setup and teardown here.


def boolify(value: str) -> t.Union[bool, str]:
    """If value is bool-able, make it so."""
    if value.lower() == 'true':
        return True
    if value.lower() == 'false':
        return False
    return value


def pytest_addoption(parser):
    for key, value in cli_params.items():
        parser.addoption(f"--{key}", action="store", default=value)


@pytest.hookimpl()
def pytest_sessionstart(session):
    docker_create = boolify(session.config.getoption('--docker_create'))
    ver = session.config.getoption('--es_version')
    project_root = path.abspath(path.join(path.dirname(__file__), '..'))
    if docker_create:
        exepath = path.join(project_root, 'docker_test', 'create.sh')
        envpath = path.join(project_root, '.env')
        try:
            msg = f'Running: "{exepath} {ver} frozen_node"'
            subprocess.run(['echo', msg], check=False)
            subprocess.run([exepath, ver, 'frozen_node'], check=True)
            load_dotenv(dotenv_path=envpath)
        except subprocess.CalledProcessError as exc:
            logger.critical('Unable to execute docker_test/create.sh: %s', exc)
            raise exc


@pytest.hookimpl()
def pytest_sessionfinish(session, exitstatus):
    docker_destroy = boolify(session.config.getoption('--docker_destroy'))
    if docker_destroy and exitstatus == 0:
        relpath = path.join(path.dirname(__file__), '..', 'docker_test', 'destroy.sh')
        try:
            subprocess.run(
                [path.abspath(relpath)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            msg = '\n\n -- docker_test environment destroyed.'
            subprocess.run(['echo', msg], check=False)
        except subprocess.CalledProcessError as exc:
            logger.critical(
                'Unable to complete docker_test/destroy.sh execution: %s', exc
            )
            raise exc
    elif docker_destroy and exitstatus != 0:
        msg = (
            f'\n\nPytest session exit status: {exitstatus}. Unable to destroy '
            f'docker_test environment automatically. Please inspect and delete '
            f'manually using docker_test/destroy.sh '
        )
        subprocess.run(['echo', msg], check=False)


@pytest.fixture(scope='session', autouse=True)
def load_env():
    env_file = find_dotenv('.env')
    load_dotenv(env_file)
