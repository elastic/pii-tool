"""App Defaults"""

import typing as t
from voluptuous import All, Any, Boolean, Coerce, Optional, Range, Required, Schema
from es_wait.defaults import EXISTS, HEALTH, ILM, RELOCATE, RESTORE, SNAPSHOT, TASK

TRACKING_INDEX = 'redactions-tracker'

CLICK_DRYRUN = {
    'dry-run': {
        'help': 'Do not perform any changes.',
        'is_flag': True,
        'show_envvar': True,
        'envvar': 'PII_TOOL_DRY_RUN',
    }
}

CLICK_TRACKING = {
    'tracking-index': {
        'help': 'Name for the tracking index.',
        'default': TRACKING_INDEX,
        'show_default': True,
        'show_envvar': True,
        'envvar': 'PII_TOOL_TRACKING_INDEX',
    }
}

PHASES: t.Sequence = ['hot', 'warm', 'cold', 'frozen', 'delete']

PAUSE_DEFAULT: str = '9.0'
PAUSE_ENVVAR: str = 'PII_TOOL_PAUSE'
TIMEOUT_DEFAULT: str = '7200.0'
TIMEOUT_ENVVAR: str = 'PII_TOOL_TIMEOUT'

TIMINGS: dict = {
    'exists': {
        'pause': {
            'testing': 0.3,
            'default': EXISTS.get('pause'),
        },
        'timeout': {
            'testing': 10.0,
            'default': EXISTS.get('timeout'),
        },
    },
    'health': {
        'pause': {
            'testing': 0.3,
            'default': HEALTH.get('pause'),
        },
        'timeout': {
            'testing': 10.0,
            'default': HEALTH.get('timeout'),
        },
    },
    'ilm': {
        'pause': {
            'testing': 1.0,
            'default': ILM.get('pause'),
        },
        'timeout': {
            'testing': 30.0,
            'default': ILM.get('timeout'),
        },
    },
    'relocate': {
        'pause': {
            'testing': 0.5,
            'default': RELOCATE.get('pause'),
        },
        'timeout': {
            'testing': 30.0,
            'default': RELOCATE.get('timeout'),
        },
    },
    'restore': {
        'pause': {
            'testing': 0.5,
            'default': RESTORE.get('pause'),
        },
        'timeout': {
            'testing': 30.0,
            'default': RESTORE.get('timeout'),
        },
    },
    'snapshot': {
        'pause': {
            'testing': 0.5,
            'default': SNAPSHOT.get('pause'),
        },
        'timeout': {
            'testing': 30.0,
            'default': SNAPSHOT.get('timeout'),
        },
    },
    'task': {
        'pause': {
            'testing': 0.3,
            'default': TASK.get('pause'),
        },
        'timeout': {
            'testing': 30.0,
            'default': TASK.get('timeout'),
        },
    },
}


def forcemerge_schema() -> t.Dict[Optional, t.Union[All, Any, Coerce, Range, Required]]:
    """Define the forcemerge schema"""
    return {
        Optional('max_num_segments', default=1): All(
            Coerce(int), Range(min=1, max=32768)
        ),
        # The Boolean() here is a capitalized function, not a class. This code passes
        # without the need for the passed value because of how voluptuous Schema
        # validation works.
        # pylint: disable=no-value-for-parameter
        Optional('only_expunge_deletes', default=False): Any(
            bool, All(Any(str), Boolean())
        ),
    }


def redactions_schema() -> t.Dict[
    Optional,
    t.Dict[
        t.Union[Required, Optional],
        t.Union[All, Any, t.Dict, t.Sequence[Any], Optional],
    ],
]:
    """An index pattern to search and redact data from"""
    merge = forcemerge_schema()
    return {
        Optional(Any(str)): {
            Required('pattern'): Any(str),
            Required('query'): {Any(str): dict},
            Required('fields'): [Any(str)],
            Required('message', default='REDACTED'): Any(str),
            # The Boolean() here is a capitalized function, not a class. This code
            # passes without the need for the passed value because of how voluptuous
            # Schema validation works.
            # pylint: disable=no-value-for-parameter
            Optional('delete', default=True): Any(bool, All(Any(str), Boolean())),
            Required('expected_docs'): All(Coerce(int), Range(min=1, max=32768)),
            Optional('restore_settings', default=None): Any(dict, None),
            Optional('forcemerge'): merge,
        }
    }


def index_settings() -> t.Dict:
    """The Elasticsearch index settings for the progress/status tracking index"""
    return {
        'index': {
            'number_of_shards': '1',
            'auto_expand_replicas': '0-1',
        }
    }


def status_mappings() -> t.Dict:
    """The Elasticsearch index mappings for the progress/status tracking index"""
    return {
        'properties': {
            'job': {'type': 'keyword'},
            'task': {'type': 'keyword'},
            'step': {'type': 'keyword'},
            'join_field': {'type': 'join', 'relations': {'job': 'task'}},
            'cleanup': {'type': 'keyword'},
            'completed': {'type': 'boolean'},
            'end_time': {'type': 'date'},
            'errors': {'type': 'boolean'},
            'dry_run': {'type': 'boolean'},
            'index': {'type': 'keyword'},
            'logs': {'type': 'text'},
            'start_time': {'type': 'date'},
        },
        'dynamic_templates': [
            {
                'configuration': {
                    'path_match': 'config.*',
                    'mapping': {'type': 'keyword', 'index': False},
                }
            }
        ],
    }


def redaction_schema() -> Schema:
    """The full voluptuous Schema for a redaction file"""
    return Schema({Required('redactions'): [redactions_schema()]})
