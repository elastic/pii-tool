"""Helper Functions"""

import typing as t
import logging
from os import environ
import json
from inspect import stack
from datetime import datetime, timezone
import re
from es_client.exceptions import ConfigurationError as esc_ConfigError
from es_client.helpers.schemacheck import SchemaCheck
from es_client.helpers.utils import get_yaml
from es_wait.exceptions import EsWaitFatal, EsWaitTimeout, IlmWaitError
import es_pii_tool.exceptions as e
from es_pii_tool.defaults import (
    PHASES,
    PAUSE_DEFAULT,
    TIMEOUT_DEFAULT,
    TIMINGS,
    redaction_schema,
)

if t.TYPE_CHECKING:
    from dotmap import DotMap  # type: ignore
    from voluptuous import Schema
    from elasticsearch8 import Elasticsearch
    from es_pii_tool.job import Job
    from es_pii_tool.trackables import Task

logger = logging.getLogger(__name__)


def build_script(message: str, fields: t.Sequence[str]) -> t.Dict[str, str]:
    """
    Build a painless script for redacting fields by way of an update_by_query operation

    :param message: The text to put in place of whatever is in a field
    :param fields: The list of field names to act on

    :type message: str
    :type fields: list

    :rtype: dict
    :returns: A dictionary of ``{"source": (assembled message), "lang": "painless"}``
    """
    msg = ""
    for field in fields:
        msg += f"ctx._source.{field} = '{message}'; "
    script = {"source": msg, "lang": "painless"}
    logger.debug('script = %s', script)
    return script


def check_dotted_fields(result: t.Dict, field: str, message: str) -> bool:
    """Iterate through dotted fields to ensure success

    :param result: The search result object
    :param field: The field with dotted notation

    :type result: dict
    :type field: str

    :returns: Success (``True``) or Failure (``False``)
    :rtype: bool
    """
    success = False
    logger.debug('Dotted field detected: (%s) ...', field)
    fielder = result['hits']['hits'][0]['_source']
    iterations = len(field.split('.'))
    counter = 1
    for key in field.split('.'):
        # This should recursively look for each subkey
        if key in fielder:
            fielder = fielder[key]
        else:
            break
        if counter == iterations:
            if fielder == message:
                success = True
        counter += 1
    return success


def check_fields(result: t.Dict, job_config: t.Dict) -> bool:
    """Check document fields in result to ensure success

    :param result: The search result object
    :param job_config: The configuration settings for this job

    :type result: dict
    :type job_config: dict

    :returns: Success (``True``) or Failure (``False``)
    :rtype: bool
    """
    complete = True
    hit = result['hits']['hits'][0]['_source']
    for field in job_config['fields']:
        success = False
        if len(field.split('.')) > 1:
            success = check_dotted_fields(result, field, job_config['message'])

        elif field in hit:
            if hit[field] == job_config['message']:
                success = True

        else:
            logger.warning("Field %s not present in document", field)
            # Don't need to report the expected fail 2x, so we break the loop here
            break

        if success:
            logger.info("Field %s is redacted correctly", field)
        else:
            # A single failure is enough to make it a complete failure.
            complete = False
            logger.error("Field %s is not redacted correctly", field)
    return complete


def chunk_index_list(indices: t.Sequence[str]) -> t.Sequence[t.Sequence[str]]:
    """
    This utility chunks very large index lists into 3KB chunks.
    It measures the size as a csv string, then converts back into a list for the return
    value.

    :param indices: The list of indices

    :type indices: list

    :returns: A list of lists (each a piece of the original ``indices``)
    :rtype: list
    """
    chunks = []
    chunk = ""
    for index in indices:
        if len(chunk) < 3072:
            if not chunk:
                chunk = index
            else:
                chunk += "," + index
        else:
            chunks.append(chunk.split(','))
            chunk = index
    chunks.append(chunk.split(','))
    return chunks


def configure_ilm_policy(task: 'Task', data: 'DotMap') -> None:
    """
    Prune phases we've already passed.

    If only_expunge_deletes is True in the job config, set any force_merge_index
    actions to False.
    """
    # Copy the existing policy to a new spot
    data.new.ilmpolicy = data.ilm.lifecycle.policy

    # Prune phases from existing ILM policy we've already surpassed
    for phase in list(data.new.ilmpolicy.phases.toDict().keys()):
        if PHASES.index(data.ilm.explain.phase) > PHASES.index(phase):
            del data.new.ilmpolicy.phases[phase]

    # Figure out if we're doing force merge
    fmerge = True
    if 'forcemerge' in task.job.config:
        fmkwargs = task.job.config['forcemerge']
        if 'only_expunge_deletes' in fmkwargs and fmkwargs['only_expunge_deletes']:
            fmerge = False
    else:
        fmerge = False

    # Loop through the remaining phases and set 'force_merge_index': False
    # to the cold or frozen actions.

    for phase in data.new.ilmpolicy.phases:
        if phase not in ['cold', 'frozen']:
            continue
        if 'searchable_snapshot' in data.new.ilmpolicy.phases[phase].actions:
            data.new.ilmpolicy.phases[
                phase
            ].actions.searchable_snapshot.force_merge_index = fmerge


def end_it(obj: t.Union['Job', 'Task'], success: bool) -> None:
    """Close out the object here to avoid code repetition"""
    # Record task success or fail here for THIS task_id
    # Each index in per_index has its own status tracker
    if not success:
        err = True
        log = 'Check application logs for detailed report'
    else:
        err = False
        log = 'DONE'
    obj.end(completed=success, errors=err, logmsg=log)


def exception_msgmaker(exc: t.Union[e.MissingIndex, e.BadClientResult]) -> str:
    """Most of the messages here are similar enough to warrant a single function"""
    msg = ''
    upstream = (
        f'The upstream exception type was {type(exc.upstream).__name__}, '
        f'with error message: {exc.upstream.args[0]}'
    )
    if isinstance(exc, e.MissingIndex):
        msg = (
            f'Exception raised because index {exc.missing} was not found. '
            f'{upstream}'
        )
    elif isinstance(exc, e.BadClientResult):
        msg = (
            f'Exception raised because of a bad or unexpected response or result '
            f'from the Elasticsearch cluster. {upstream}'
        )
    return msg


def get_alias_actions(oldidx: str, newidx: str, aliases: t.Dict) -> t.Sequence:
    """
    :param oldidx: The old index name
    :param newidx: The new index name
    :param aliases: The aliases

    :type oldidx: str
    :type newidx: str
    :type aliases: dict

    :returns: A list of actions suitable for
        :py:meth:`~.elasticsearch.client.IndicesClient.update_aliases` ``actions``
        kwarg.
    :rtype: list
    """
    actions = []
    for alias in aliases.keys():
        actions.append({'remove': {'index': oldidx, 'alias': alias}})
        actions.append({'add': {'index': newidx, 'alias': alias}})
    return actions


def get_field_matches(config: t.Dict, result: t.Dict) -> int:
    """Count docs which have the expected fields

    :param config: The config from the YAML file
    :param result: The query result dict

    :type config: dict
    :type result: dict

    :returns: The count of docs in ``result`` which have the identified fields
    :rtype: int
    """

    logger.debug('Extracting doc hit count from result')
    doc_count = result['hits']['total']['value']
    for element in range(0, result['hits']['total']['value']):
        for field in config['fields']:
            if len(field.split('.')) > 1:
                logger.debug('Dotted field "%s" detected...', field)
                fielder = result['hits']['hits'][element]['_source']
                for key in field.split('.'):
                    # This should recursively look for each subkey
                    if key in fielder:
                        fielder = fielder[key]
                    else:
                        doc_count -= 1
                        break
            elif field not in list(result['hits']['hits'][element]['_source'].keys()):
                logger.debug('Fieldname "%s" NOT detected...', field)
                doc_count -= 1
            else:
                logger.debug('Root-level fieldname "%s" detected...', field)
    return doc_count


def get_fname() -> str:
    """Return the name of the calling function"""
    return stack()[1].function


def get_inc_version(name: str) -> int:
    """Extract the incrementing version value from the end of name

    :param name: The name

    :type name: str

    :returns: The integer value of the current index revision, or 0 if no version
    :rtype: int
    """
    # Anchor the end as 3 dashes, a v, and 3 digits, e.g. ---v001
    match = re.search(r'^.*---v(\d{3})$', name)
    if match:
        return int(match.group(1))
    return 0


def get_redactions(file: str = '', data: t.Union[t.Dict, None] = None) -> 'Schema':
    """
    Return valid dictionary of redactions from either ``file`` or from ``data``
    after checking Schema

    :param file: YAML file with redactions to check
    :param data: Configuration data in dictinoary format

    :type file: str
    :type data: dict

    :rtype: dict
    :returns: Redactions configuration data
    """
    if data is None:
        data = {}
    logger.debug('Getting redactions data...')
    if file:
        try:
            config = get_yaml(file)
        except esc_ConfigError as exc:
            msg = f'Unable to read and/or parse YAML REDACTIONS_FILE: {file} Exiting.'
            logger.critical(msg)
            raise e.ConfigError(msg, exc)
    elif data:
        config = data
    else:
        raise e.FatalError('No configuration file or dictionary provided.', Exception())
    logger.debug('Performing redaction schema check...')
    try:
        return SchemaCheck(
            config, redaction_schema(), 'Redaction Configuration', 'redactions'
        ).result()
    except Exception as exc:
        msg = f'Redaction configuration schema check failed: {exc} -- Exiting.'
        logger.critical(msg)
        raise exc


def now_iso8601() -> str:
    """
    :returns: An ISO8601 timestamp based on datetime.now
    """
    # Because Python 3.12 now requires non-naive timezone declarations, we must change.
    #
    # ## Example:
    # ## The new way:
    # ##     datetime.now(timezone.utc).isoformat()
    # ##     Result: 2024-04-16T16:00:00+00:00
    # ## End Example
    #
    # Note that the +00:00 is appended now where we affirmatively declare the UTC
    # timezone
    #
    # As a result, we will use this function to prune away the timezone if it is +00:00
    # and replace it with Z, which is shorter Zulu notation for UTC (per Elasticsearch)
    #
    # We are MANUALLY, FORCEFULLY declaring timezone.utc, so it should ALWAYS be +00:00,
    # but could in theory sometime show up as a Z, so we test for that.

    parts = datetime.now(timezone.utc).isoformat().split('+')
    if len(parts) == 1:
        if parts[0][-1] == 'Z':
            return parts[0]  # Our ISO8601 already ends with a Z for Zulu/UTC time
        return f'{parts[0]}Z'  # It doesn't end with a Z so we put one there
    if parts[1] == '00:00':
        return f'{parts[0]}Z'  # It doesn't end with a Z so we put one there
    return f'{parts[0]}+{parts[1]}'  # Fallback publishes the +TZ, whatever that was


def config_fieldmap(
    rw_val: t.Literal['read', 'write'],
    key: t.Literal[
        'pattern',
        'query',
        'fields',
        'message',
        'expected_docs',
        'restore_settings',
        'delete',
    ],
) -> t.Union[str, int, object]:
    """
    Return the function from this function/key map
    """
    which = {
        'read': {
            'pattern': json.loads,
            'query': json.loads,
            'fields': json.loads,
            'message': str,
            'expected_docs': int,
            'restore_settings': json.loads,
            'delete': str,
        },
        'write': {
            'pattern': json.dumps,
            'query': json.dumps,
            'fields': json.dumps,
            'message': str,
            'expected_docs': int,
            'restore_settings': json.dumps,
            'delete': str,
        },
    }
    return which[rw_val][key]


def parse_job_config(config: t.Dict, behavior: t.Literal['read', 'write']) -> t.Dict:
    """Parse raw config from the index.

    Several fields are JSON escaped, so we need to fix it to put it in a dict.

    :param config: The raw config data
    :param behavior: ``read`` or ``write``

    :type config: dict
    :type behavior: str

    :rtype: dict

    :returns: JSON-(de)sanitized configuration dict
    """
    fields = [
        'pattern',
        'query',
        'fields',
        'message',
        'expected_docs',
        'restore_settings',
        'delete',
    ]
    doc = {}
    for field in fields:
        if field in config:
            func = config_fieldmap(behavior, field)  # type: ignore
            doc[field] = func(config[field])  # type: ignore
    return doc


def strip_ilm_name(name: str) -> str:
    """
    Strip leading ``pii-tool-``, and trailing ``---v000`` from ``name``

    :param name: The ILM lifecycle name

    :type name: str

    :returns: The "cleaned up" and stripped ILM name
    :rtype: str
    """
    retval = name.replace('pii-tool-', '')
    # Anchor the end as 3 dashes, a v, and 3 digits, e.g. ---v001
    match = re.search(r'^(.*)---v\d{3}$', retval)
    if match:
        retval = match.group(1)
    return retval


def strip_index_name(name: str) -> str:
    """
    Strip ``partial-``, ``restored-``, ``redacted-``, and trailing ``---v000`` from
    ``name``

    :param name: The index name

    :type name: str

    :returns: The "cleaned up" and stripped index name
    :rtype: str
    """
    retval = name.replace('partial-', '')
    retval = retval.replace('restored-', '')
    retval = retval.replace('redacted-', '')
    # Anchor the end as 3 dashes, a v, and 3 digits, e.g. ---v001
    match = re.search(r'^(.*)---v\d{3}$', retval)
    if match:
        retval = match.group(1)
    return retval


def es_waiter(client: 'Elasticsearch', cls, **kwargs) -> None:
    """Wait for ILM Phase & Step to be reached"""
    try:
        waiter = cls(client, **kwargs)
        waiter.wait()
    except (
        IlmWaitError,
        EsWaitFatal,
        EsWaitTimeout,
    ) as wait_err:
        msg = f'{cls.__name__}: wait for completion failed: {kwargs}'
        logger.error(f'{msg}. Exception(s): - {wait_err}')
        raise e.BadClientResult(msg, wait_err)


def timing(kind: str) -> t.Tuple:
    """
    Return a tuple of two floats: the pause value and the timeout value

    :param kind: The kind of timing to do

    :type kind: str

    :returns: A tuple of two floats
    :rtype: tuple
    """
    is_test = environ.get('PII_TOOL_TESTING', 'False') == 'True'
    pause = 1.0 if is_test else PAUSE_DEFAULT  # Default values to be overridden
    timeout = 30.0 if is_test else TIMEOUT_DEFAULT  # Default values to be overridden
    testkey = 'testing' if is_test else 'default'
    pause = TIMINGS[kind]['pause'][testkey]
    timeout = TIMINGS[kind]['timeout'][testkey]
    # logger.debug(
    #     f'kind = {kind}, TESTING = {testing}, PAUSE = {pause}, TIMEOUT = {timeout}'
    # )
    return pause, timeout
