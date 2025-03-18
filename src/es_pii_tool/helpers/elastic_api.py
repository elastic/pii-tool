"""Functions making Elasticsearch API calls"""

import typing as t
import time
import logging
from elasticsearch8.exceptions import (
    ApiError,
    NotFoundError,
    TransportError,
    BadRequestError,
)
from es_wait import Health, Restore, Snapshot, Task
from es_pii_tool.exceptions import (
    BadClientResult,
    FatalError,
    MissingDocument,
    MissingError,
    MissingIndex,
    ValueMismatch,
)
from es_pii_tool.helpers.utils import build_script, check_fields, es_waiter, timing

if t.TYPE_CHECKING:
    from dotmap import DotMap  # type: ignore
    from elasticsearch8 import Elasticsearch
    from elastic_transport import HeadApiResponse


logger = logging.getLogger(__name__)

# pylint: disable=R0913,R0917,W0707


def assign_alias(client: 'Elasticsearch', index_name: str, alias_name: str) -> None:
    """Assign index to alias(es)"""
    try:
        response = client.indices.put_alias(index=index_name, name=alias_name)
        logger.info(
            "Index '%s' was successfully added to alias '%s'", index_name, alias_name
        )
        logger.debug(response)
    except (ApiError, NotFoundError, TransportError, BadRequestError) as err:
        msg = f'Attempt to assign index "{index_name}" to alias "{alias_name}" failed'
        logger.critical(msg)
        raise BadClientResult(msg, err)


def check_index(client: 'Elasticsearch', index_name: str, job_config: t.Dict) -> None:
    """Check the index"""
    logger.info('Making a quick check on redacted index docs...')
    result = do_search(client, index_name, job_config['query'])
    if result['hits']['total']['value'] == 0:
        logger.warning(
            'Query returned no results, assuming it only returns docs '
            'to be redacted and not already redacted...'
        )
        return
    success = check_fields(result, job_config)
    if not success:
        msg = 'One or more fields were not redacted. Check the logs'
        logger.error(msg)
        raise ValueMismatch(msg, 'count of fields matching query is not 0', '0')


def clear_cache(client: 'Elasticsearch', index_name: str) -> None:
    """Clear the cache for named index

    :param client: A client connection object
    :param index_name: The index name

    :type client: :py:class:`~.elasticsearch.Elasticsearch`
    :type index_name: str

    :returns: No return value
    :rtype: None
    """
    response = {}
    logger.info('Clearing cache data for %s...', index_name)
    try:
        response = dict(
            client.indices.clear_cache(
                index=index_name, expand_wildcards=['open', 'hidden']
            )
        )
        logger.debug(response)
    except (ApiError, NotFoundError, TransportError, BadRequestError) as err:
        logger.error('clear_cache API call resulted in an error: %s', err)


def close_index(client: 'Elasticsearch', name: str) -> None:
    """Close an index

    :param name: The index name to close

    :type name: str
    """
    try:
        response = client.indices.close(index=name, expand_wildcards=['open', 'hidden'])
        logger.debug(response)
    except (ApiError, NotFoundError, TransportError, BadRequestError) as err:
        logger.error("Index: '%s' not found. Error: %s", name, err)
        raise MissingIndex(f'Index "{name}" not found', err, name)


def create_index(
    client: 'Elasticsearch',
    name: str,
    mappings: t.Union[t.Dict, None] = None,
    settings: t.Union[t.Dict, None] = None,
) -> None:
    """Create an Elasticsearch index with associated mappings and settings

    :param name: The index name
    :param mappings: The index mappings
    :param settings: The index settings

    :type name: str
    :type mappings: dict
    :type settings: dict
    """
    if index_exists(client, name):
        logger.info('Index %s already exists', name)
        return
    try:
        response = client.indices.create(
            index=name, settings=settings, mappings=mappings
        )
        logger.debug(response)
    except BadRequestError as err:
        logger.error("Index: '%s' already exists. Error: %s", name, err)
        raise BadClientResult(f'Index "{name}" already exists', err)
    except (ApiError, TransportError) as err:
        logger.error("Unknown error trying to create index: '%s'. Error: %s", name, err)
        raise BadClientResult(f'Unknown error trying to create index: {name}', err)


def delete_index(client: 'Elasticsearch', name: str) -> None:
    """Delete an index

    :param client: A client connection object
    :param name: The index name to delete

    :type name: str
    """
    try:
        response = client.indices.delete(
            index=name, expand_wildcards=['open', 'hidden']
        )
        logger.debug(response)
    except (ApiError, NotFoundError, TransportError, BadRequestError) as err:
        # logger.error("Index: '%s' not found. Error: %s", name, err)
        raise MissingIndex(f'Index "{name}" not found', err, name)


def do_search(
    client: 'Elasticsearch', index_pattern: str, query: t.Dict, size: int = 10
) -> t.Dict:
    """Return search result of ``query`` against ``index_pattern``

    :param client: A client connection object
    :param index_pattern: A single index name, a csv list of indices, or other pattern
    :param query: An Elasticsearch DSL search query
    :param size: Maximum number of results to return

    :type client: :py:class:`~.elasticsearch.Elasticsearch`
    :type index_pattern: str
    :type query: dict
    :type size: int
    """
    kwargs = {
        'index': index_pattern,
        'query': query,
        'size': size,
        'expand_wildcards': ['open', 'hidden'],
    }
    logger.debug('Search kwargs = %s', kwargs)
    try:
        response = dict(client.search(**kwargs))  # type: ignore
        logger.debug(response)
    except (ApiError, NotFoundError, TransportError, BadRequestError) as err:
        msg = f'Attempt to collect search results yielded an exception: {err}'
        logger.critical(msg)
        raise BadClientResult(msg, err)
    return response


def forcemerge_index(
    client: 'Elasticsearch',
    index: t.Union[str, None] = None,
    max_num_segments: int = 1,
    only_expunge_deletes: bool = False,
) -> None:
    """
    Force Merge an index

    :param client: A client connection object
    :param index: A single index name
    :param max_num_segments: The maximum number of segments per shard after a
        force merge
    :param only_expunge_deletes: Only expunge deleted docs during force merging.
        If True, ignores max_num_segments.

    :type client: :py:class:`~.elasticsearch.Elasticsearch`
    :type index: str
    :type max_num_segments: int
    :type only_expunge_deletes: bool
    """
    kwargs = {'index': index, 'wait_for_completion': False}
    if only_expunge_deletes:
        kwargs.update({'only_expunge_deletes': only_expunge_deletes})
    else:
        kwargs.update({'max_num_segments': max_num_segments})  # type: ignore
    try:
        response = dict(client.indices.forcemerge(**kwargs))  # type: ignore
        logger.debug(response)
    except (ApiError, NotFoundError, TransportError, BadRequestError) as err:
        logger.error("Index: '%s' not found. Error: %s", index, err)
        raise MissingIndex(f'Index "{index}" not found', err, index)  # type: ignore
    logger.info('Waiting for forcemerge to complete...')
    pause, timeout = timing('task')
    logger.debug(f'ENV pause = {pause}, timeout = {timeout}')
    try:
        es_waiter(
            client,
            Task,
            action='forcemerge',
            task_id=response['task'],
            pause=pause,
            timeout=timeout,
        )
    except BadClientResult as exc:
        logger.error('Exception: %s', exc)
        raise FatalError('Failed to forcemerge', exc)
    logger.info('Forcemerge completed.')


def generic_get(func: t.Callable, **kwargs) -> t.Dict:
    """Generic, reusable client request getter"""
    try:
        response = dict(func(**kwargs))
        logger.debug(response)
    except NotFoundError as nferr:
        raise MissingError('Generic Get MissingError', nferr, nferr.info)
    except (ApiError, TransportError, BadRequestError) as err:
        raise BadClientResult('Generic Get BadClientResult Failure', err)
    return response


def get_hits(client: 'Elasticsearch', index: str, query: t.Dict) -> int:
    """Return the number of hits matching the query

    :param client: A client connection object
    :param index: The index or pattern to search
    :param query: The query to execute

    :type client: :py:class:`~.elasticsearch.Elasticsearch`
    :type index: str
    :type query: dict

    :returns: The number of hits matching the query
    """
    result = do_search(client, index, query)
    return result['hits']['total']['value']


def get_ilm(client: 'Elasticsearch', index: str) -> t.Dict:
    """Get the ILM lifecycle settings for an index

    :param client: A client connection object
    :param index: The index to check

    :type client: :py:class:`~.elasticsearch.Elasticsearch`
    :type index: str

    :returns: The ILM settings object for the named index
    """
    try:
        response = dict(client.ilm.explain_lifecycle(index=index))
        logger.debug(response)
    except NotFoundError as err:
        logger.error("Index: '%s' not found. Error: %s", index, err)
        raise MissingIndex(f'Index "{index}" not found', err, index)
    return response


def get_ilm_lifecycle(client: 'Elasticsearch', policyname: str) -> t.Dict:
    """Get the ILM lifecycle settings for an policyname

    :param client: A client connection object
    :param policyname: The ILM policy name to check

    :type client: :py:class:`~.elasticsearch.Elasticsearch`
    :type policyname: str

    :returns: The ILM settings object for the named policy, or None
    """
    retval = {}
    try:
        retval = dict(client.ilm.get_lifecycle(name=policyname))
    except NotFoundError:
        logger.debug("ILM policy '%s' not found.", policyname)
    return retval


def get_index(client: 'Elasticsearch', index: str) -> t.Dict:
    """Get the info about an index

    :param client: A client connection object
    :param index: The index, csv indices, or index pattern to get

    :type client: :py:class:`~.elasticsearch.Elasticsearch`
    :type index: str

    :returns: The index information object for the named index
    """
    try:
        response = dict(
            client.indices.get(index=index, expand_wildcards=['open', 'hidden'])
        )
        logger.debug('Found indices: %s', list(response.keys()))
    except (ApiError, NotFoundError, TransportError, BadRequestError) as err:
        logger.error("Index: '%s' not found. Error: %s", index, err)
        raise MissingIndex(f'Index "{index}" not found', err, index)
    except Exception as exc:
        logger.error(f'Unanticipated Exception: {exc}')
        raise exc
    return response


def get_phase(client: 'Elasticsearch', index: str) -> t.Union[str, None]:
    """Get the index's ILM phase

    :param client: A client connection object
    :param index: The index name

    :type client: :py:class:`~.elasticsearch.Elasticsearch`
    :type index: str

    :returns: The ILM phase of ``index``
    """
    phase = None
    ilm = get_ilm(client, index)
    try:
        phase = ilm['indices'][index]['phase']
    except KeyError:  # Perhaps in cold/frozen but not ILM affiliated
        settings = get_settings(client, index)[index]['settings']['index']
        if "store" in settings:
            # Checking if it's a mounted searchable snapshot
            if settings["store"]["type"] == "snapshot":
                phase = get_phase_from_tier_pref(settings)
        else:
            phase = None
    return phase


def get_phase_from_tier_pref(
    idx_settings: t.Dict,
) -> t.Union[t.Literal['frozen', 'cold'], None]:
    """
    Check the index's ``_tier_preference`` as an indicator which phase the index is in

    :param idx_settings: The results from a
        get_settings(index=idx)[idx]['settings']['index'] call

    :returns: The ILM phase based on the index settings, or None
    """
    try:
        tiers = idx_settings['routing']['allocation']['include']['_tier_preference']
    except KeyError:
        tiers = ''
    if tiers == 'data_frozen':
        return 'frozen'
    if 'data_cold' in tiers.split(','):
        return 'cold'
    return None


def ilm_move(
    client: 'Elasticsearch', name: str, current_step: t.Dict, next_step: t.Dict
) -> None:
    """Move index 'name' from the current step to the next step"""
    try:
        client.ilm.move_to_step(
            index=name, current_step=current_step, next_step=next_step
        )
    except Exception as err:
        msg = (
            f'Unable to move index {name} to ILM next step: {next_step}. '
            f'Error: {err}'
        )
        logger.critical(msg)
        raise BadClientResult(msg, err)


def modify_data_stream(
    client: 'Elasticsearch', actions: t.Sequence[t.Mapping[str, t.Any]]
) -> None:
    """Modify a data_stream using the contents of actions

    :param client: A client connection object
    :param actions: The actions to take

    :type client: :py:class:`~.elasticsearch.Elasticsearch`
    :type actions: dict
    """
    try:
        client.indices.modify_data_stream(actions=actions)
    except BadRequestError as exc:
        logger.error(
            "Unable to modify data_stream using actions='%s'. ERROR: %s", actions, exc
        )
        raise MissingIndex(
            'Missing either data_stream or index', exc, f'actions: {actions}'
        )


def report_segment_count(client: 'Elasticsearch', index: str) -> str:
    """
    Report the count of segments from index

    :param client: A client connection object
    :param index: The index to check

    :type client: :py:class:`~.elasticsearch.Elasticsearch`
    :type index: str

    :returns: Formatted message describing shard count and segment count for index
    """
    shardcount = 0
    segmentcount = 0
    try:
        output = client.cat.shards(
            index=index, format='json', h=['index', 'shard', 'prirep', 'sc']
        )
    except Exception as exc:
        logger.error('Exception: %s', exc)
        raise BadClientResult('Unable to get cat shards output', exc)
    for shard in output:
        if shard['prirep'] == 'r':  # type: ignore
            # Skip replica shards
            continue
        if index != shard['index']:  # type: ignore
            logger.warning(
                'Index name %s does not match what was returned by the _cat API: %s',
                index,
                shard['index'],  # type: ignore
            )
        shardcount += 1
        segmentcount += int(shard['sc'])  # type: ignore
        logger.debug(
            'Index %s, shard %s has %s segments',
            index,
            shard["shard"],  # type: ignore
            shard["sc"],  # type: ignore
        )

    return (
        f'index {index} has {shardcount} shards and a total of {segmentcount} '
        f'segments, averaging {float(segmentcount/shardcount)} segments per shard'
    )


def get_settings(client: 'Elasticsearch', index: str) -> t.Dict:
    """Get the settings for an index

    :param client: A client connection object
    :param index: The index to check

    :type client: :py:class:`~.elasticsearch.Elasticsearch`
    :type index: str

    :returns: The settings object for the named index
    """
    logger.debug('Getting settings for index: %s', index)
    try:
        response = dict(
            client.indices.get_settings(
                index=index, expand_wildcards=['open', 'hidden']
            )
        )
        logger.debug(response)
    except (ApiError, NotFoundError, TransportError, BadRequestError) as err:
        logger.error("Index: '%s' not found. Error: %s", index, err)
        raise MissingIndex(f'Index "{index}" not found', err, index)
    logger.debug('Index settings collected.')
    return response


def put_settings(client: 'Elasticsearch', index: str, settings: dict) -> None:
    """Modify a data_stream using the contents of actions

    :param client: A client connection object
    :param settings: The index settings to apply

    :type client: :py:class:`~.elasticsearch.Elasticsearch`
    :type settings: dict
    """
    try:
        client.indices.put_settings(index=index, settings=settings)
    except NotFoundError as exc:
        logger.error("Index '%s' not found: %s", index, exc)
        raise MissingIndex('Index not found', exc, index)
    except BadRequestError as exc:
        logger.error("Bad settings: %s. ERROR: %s", settings, exc)
        raise BadClientResult(f'Invalid settings: {settings}', exc)


def get_progress_doc(
    client: 'Elasticsearch',
    index_name: str,
    job_id: str,
    task_id: str,
    stepname: str = '',
) -> t.Dict:
    """Get a task tracking doc

    :param client: A client connection object
    :param index_name: The index name
    :param job_id: The job name string for the present redaction run
    :param task_id: The task_id string of the task we are searching for
    :param stepname: [Optional] The step name string of the step we are searching for

    :type client: :py:class:`~.elasticsearch.Elasticsearch`
    :type index_name: str
    :type job_id: str
    :type task_id: str
    :type stepname: str

    :returns: The progress tracking document from the progress/status tracking index
        for the task or step
    """
    # Base value for stub (task)
    stub = f'Task: {task_id} of Job: {job_id}'
    # The proto query
    query = {
        "bool": {
            "must": {"parent_id": {"type": "task", "id": job_id}},
            "filter": [],
        }
    }
    # The base value of the bool filter (task)
    filters = [
        {"term": {"task": task_id}},
        {"term": {"job": job_id}},
    ]
    if not stepname:
        logger.info('Tracking progress for %s', stub)
        # For Tasks progress docs, we must not match docs with a step field
        query['bool']['must_not'] = {"exists": {"field": "step"}}
    else:
        # Update stub to be for a step
        stub = f'Step: {stepname} of Task: {task_id} of Job: {job_id}'
        logger.info('Tracking progress for %s', stub)
        # Update filters to include step
        filters.append({"term": {"step": stepname}})
    # Add the filters to the query
    query['bool']['filter'] = filters  # type: ignore
    try:
        result = do_search(client, index_pattern=index_name, query=query)
    except NotFoundError as err:
        msg = f'Tracking index {index_name} is missing'
        logger.critical(msg)
        raise MissingIndex(msg, err, index_name)
    # First get the edge case of multiple hits out of the way
    if result['hits']['total']['value'] > 1:
        msg = f'Tracking document for {stub} is not unique. This should never happen.'
        logger.critical(msg)
        raise FatalError(msg, ValueError())
    # After the > 1 test, if we don't have exactly 1 hit, we have zero hits
    if result['hits']['total']['value'] != 1:
        msg = f'Tracking document for {stub} does not exist'
        missing = f'A document with step: {stepname}, task: {task_id}, job: {job_id}'
        logger.debug(msg)
        raise MissingDocument(msg, Exception(), missing)
    # There can be only one...
    return result['hits']['hits'][0]


def get_tracking_doc(client: 'Elasticsearch', index_name: str, job_id: str) -> t.Dict:
    """Get the progress/status tracking doc for the provided job_id

    :param client: A client connection object
    :param index_name: The index name
    :param job_id: The job_id string for the present redaction run

    :type client: :py:class:`~.elasticsearch.Elasticsearch`
    :type index_name: str
    :type job_id: str

    :returns: The tracking document from the progress/status tracking index
    """
    if not index_exists(client, index_name):
        msg = f'Tracking index {index_name} is missing'
        logger.critical(msg)
        raise MissingIndex(msg, Exception(), index_name)
    try:
        doc = dict(client.get(index=index_name, id=job_id))
        # logger.debug('TRACKING DOC = %s', doc)
    except NotFoundError as exc:
        msg = f'Tracking document for job_id {job_id} does not exist'
        logger.debug(msg)
        raise MissingDocument(msg, exc, job_id)
    return doc['_source']


def index_exists(client: 'Elasticsearch', index_name: str) -> 'HeadApiResponse':
    """Test whether index ``index_name`` exists

    :param client: A client connection object
    :param index_name: The index name

    :type client: :py:class:`~.elasticsearch.Elasticsearch`
    :type index_name: str

    :returns: ``HeadApiResponse(True)`` if ``index_name`` exists, otherwise
        ``HeadApiResponse(False)``
    """
    return client.indices.exists(index=index_name, expand_wildcards=['open', 'hidden'])


def job_exists(
    client: 'Elasticsearch', index_name: str, job_id: str
) -> 'HeadApiResponse':
    """Test whether a document exists for the present job_id

    :param client: A client connection object
    :param index_name: The index name
    :param job_id: The job_id string for the present redaction run

    :type client: :py:class:`~.elasticsearch.Elasticsearch`
    :type index_name: str
    :type job_id: str

    :returns: ``HeadApiResponse(True)`` if a document exists with the present
        ``job_id`` exists in ``index_name``, otherwise ``HeadApiResponse(False)``
    """
    return client.exists(index=index_name, id=job_id)


def mount_index(var: 'DotMap') -> None:
    """Mount index as a searchable snapshot

    :param var: A collection of variables from
        :py:attr:`~.es_pii_tool.redacters.snapshot.RedactSnapshot.var`

    :type var: DotMap
    """
    response = {}
    msg = (
        f'Mounting {var.redaction_target} renamed as {var.mount_name} '
        f'from repository: {var.repository}, snapshot: {var.new_snap_name} '
        f'with storage={var.storage}'
    )
    logger.debug(msg)
    while index_exists(var.client, var.mount_name):
        logger.warning('Index %s exists. Deleting before remounting', var.mount_name)
        delete_index(var.client, var.mount_name)
        time.sleep(3.0)
    try:
        response = dict(
            var.client.searchable_snapshots.mount(
                repository=var.repository,
                snapshot=var.new_snap_name,
                index=var.redaction_target,
                renamed_index=var.mount_name,
                storage=var.storage,
            )
        )
    except (ApiError, NotFoundError, TransportError, BadRequestError) as err:
        logger.error("Attempt to mount index '%s' failed: %s", var.mount_name, err)
        logger.debug(response)
        raise BadClientResult('Error when mount index attempted', err)
    logger.info('Ensuring searchable snapshot mount is in "green" health state...')
    pause, timeout = timing('health')
    logger.debug(f'ENV pause = {pause}, timeout = {timeout}')
    try:
        es_waiter(
            var.client,
            Health,
            check_type='status',
            indices=var.mount_name,
            pause=pause,
            timeout=timeout,
        )
    except BadClientResult as exc:
        logger.error('Exception: %s', exc)
        raise FatalError('Failed to mount index from snapshot', exc)
    logger.info("Index '%s' mounted from snapshot succesfully", var.mount_name)


def resolve_index(client: 'Elasticsearch', index: str) -> t.Dict:
    """Resolve an index

    :param client: A client connection object
    :param index: The index name

    :type client: :py:class:`~.elasticsearch.Elasticsearch`
    :type index: str

    :returns: The return value from
        :py:meth:`~.elasticsearch.Elasticsearch.IndicesClient.resolve_index`
    :rtype: dict
    """
    logger.debug('Resolving index: %s', index)
    try:
        response = dict(
            client.indices.resolve_index(
                name=index, expand_wildcards=['open', 'hidden']
            )
        )
        logger.debug(response)
    except (ApiError, NotFoundError, TransportError, BadRequestError) as err:
        logger.error("Index: '%s' not found. Error: %s", index, err)
        raise MissingIndex(f'Index "{index}" not found', err, index)
    logger.debug('Index resolved.')
    return response


def restore_index(
    client: 'Elasticsearch',
    repo_name: str,
    snap_name: str,
    index_name: str,
    replacement: str,
    re_pattern: str = '(.+)',
    index_settings: t.Union[str, None] = None,
) -> None:
    """Restore an index

    :param client: A client connection object
    :param repo_name: The repository name
    :param snap_name: The snapshot name
    :param index_name: The index name as it appears in the snapshot metadata
    :param replacement: The name or substitution string to use as the restored index
        name
    :param re_pattern: The optional rename pattern for use with ``replacement``
    :param index_settings: Any settings to apply to the restored index, such as
        _tier_preference

    :type client: :py:class:`~.elasticsearch.Elasticsearch`
    :type repo_name: str
    :type snap_name: str
    :type index_name: str
    :type replacement: str
    :type re_pattern: str
    :type index_settings: dict
    """
    msg = (
        f"repository={repo_name}, snapshot={snap_name}, indices={index_name},"
        f"include_aliases=False,"
        f"ignore_index_settings=["
        f"    'index.lifecycle.name', 'index.lifecycle.rollover_alias',"
        f"    'index.routing.allocation.include._tier_preference'],"
        f"index_settings={index_settings},"
        f"rename_pattern={re_pattern},"
        f"rename_replacement={replacement},"
        f"wait_for_completion=False"
    )
    logger.debug('RESTORE settings: %s', msg)
    try:
        response = client.snapshot.restore(
            repository=repo_name,
            snapshot=snap_name,
            indices=index_name,
            include_aliases=False,
            ignore_index_settings=[
                'index.lifecycle.name',
                'index.lifecycle.rollover_alias',
                'index.routing.allocation.include._tier_preference',
            ],
            index_settings=index_settings,  # type: ignore
            rename_pattern=re_pattern,
            rename_replacement=replacement,
            wait_for_completion=False,
        )
        logger.debug('Response = %s', response)
        logger.info('Checking if restoration completed...')
        pause, timeout = timing('restore')
        logger.debug(f'ENV pause = {pause}, timeout = {timeout}')
        try:
            es_waiter(
                client, Restore, index_list=[replacement], pause=pause, timeout=timeout
            )
        except BadClientResult as bad:
            logger.error('Exception: %s', bad)
            raise BadClientResult('Failed to restore index from snapshot', bad)
        msg = f'Restoration of index {index_name} as {replacement} complete'
        logger.info(msg)
    except (ApiError, NotFoundError, TransportError, BadRequestError) as err:
        msg = (
            f'Restoration of index {index_name} as {replacement} yielded an error: '
            f'{err}'
        )
        logger.error(msg)
        raise BadClientResult(msg, err)
    # verify index is green
    logger.info('Ensuring restored index is in "green" health state...')
    res = dict(client.cluster.health(index=replacement, filter_path='status'))
    logger.debug('res = %s', res)
    if res['status'] == 'red':
        msg = f'Restored index {replacement} is not in a healthy state'
        logger.error(msg)
        raise ValueMismatch(msg, 'index health is "red"', 'green or yellow')


def redact_from_index(client: 'Elasticsearch', index_name: str, config: t.Dict) -> None:
    """Redact data from an index using a painless script.

    Collect the task_id and wait for the reinding job to complete before returning

    :param client: A client connection object
    :param index_name: The index to act on
    :param config: The config block being iterated. Contains ``query``, ``message``,
        and ``fields``

    :type client: :py:class:`~.elasticsearch.Elasticsearch`
    :type index_name: str
    :type config: dict
    """
    logger.debug('Begin redaction...')
    logger.info('Before update by query, %s', report_segment_count(client, index_name))
    logger.debug('Updating and redacting data...')
    script = build_script(config['message'], config['fields'])
    response = {}
    try:
        response = dict(
            client.update_by_query(
                index=index_name,
                script=script,
                query=config['query'],
                wait_for_completion=False,
                expand_wildcards=['open', 'hidden'],
            )
        )
    except (ApiError, NotFoundError, TransportError, BadRequestError) as err:
        logger.critical('update_by_query yielded an error: %s', err)
        raise FatalError('update_by_query API call failed', err)
    logger.debug('Checking update by query status...')
    logger.debug('response = %s', response)
    pause, timeout = timing('task')
    logger.debug(f'ENV pause = {pause}, timeout = {timeout}')
    try:
        es_waiter(
            client,
            Task,
            action='update_by_query',
            task_id=response['task'],
            pause=pause,
            timeout=timeout,
        )
    except BadClientResult as exc:
        logger.error('Exception: %s', exc)
        raise FatalError('Failed to complete update by query', exc)
    logger.info('After update by query, %s', report_segment_count(client, index_name))
    logger.debug('Update by query completed.')


def remove_ilm_policy(client: 'Elasticsearch', index: str) -> t.Dict:
    """Remove any ILM policy associated with index

    :param client: A client connection object
    :param index: The index

    :type client: :py:class:`~.elasticsearch.Elasticsearch`
    :type index: str

    :returns: The response, e.g. ``{'has_failures': False, 'failed_indexes': []}``
    """
    try:
        response = dict(client.ilm.remove_policy(index=index))
        logger.debug(response)
    except (ApiError, NotFoundError, TransportError, BadRequestError) as err:
        logger.error("Index: '%s' not found. Error: %s", index, err)
        raise MissingIndex(f'Index "{index}" not found', err, index)
    return response


def take_snapshot(
    client: 'Elasticsearch', repo_name: str, snap_name: str, index_name: str
) -> None:
    """
    Take snapshot of index

    :param client: A client connection object
    :param repo_name: The repository name
    :param snap_name: The snapshot name
    :param index_name: The name of the index to snapshot

    :type client: :py:class:`~.elasticsearch.Elasticsearch`
    :type repo_name: str
    :type snap_name: str
    :type index_name: str
    """
    logger.info('Creating new snapshot...')
    response = {}
    try:
        response = dict(
            client.snapshot.create(
                repository=repo_name,
                snapshot=snap_name,
                indices=index_name,
                wait_for_completion=False,
            )
        )
        logger.debug('Snapshot response: %s', response)
    except (ApiError, NotFoundError, TransportError, BadRequestError, KeyError) as err:
        msg = f'Creation of snapshot "{snap_name}" resulted in an error: {err}'
        logger.critical(msg)
        raise BadClientResult(msg, err)
    logger.info('Checking on status of snapshot...')
    pause, timeout = timing('snapshot')
    logger.debug(f'ENV pause = {pause}, timeout = {timeout}')
    try:
        es_waiter(
            client,
            Snapshot,
            snapshot=snap_name,
            repository=repo_name,
            pause=pause,
            timeout=timeout,
        )
    except BadClientResult as exc:
        logger.error('Exception: %s', exc)
        raise FatalError('Failed to complete index snapshot', exc)
    msg = (
        f'{index_name}: Snapshot to repository {repo_name} in snapshot {snap_name} '
        f'succeeded.'
    )
    logger.info(msg)


def update_doc(
    client: 'Elasticsearch', index: str, doc_id: str, doc: t.Dict, routing: int = 0
) -> None:
    """Upsert a document in ``index`` at ``doc_id`` with the values of ``doc``

    :param client: A client connection object
    :param index: The index to write to
    :param doc_id: The document doc_id to update
    :param doc: The contents of the document
    :param routing: Because our tracking doc is using parent/child relationships, we
        need to route. We use an integer, but the API calls expect a string, so we
        manually cast this value in the API call as one.

    :type client: :py:class:`~.elasticsearch.Elasticsearch`
    :type index: str
    :type doc_id: str
    :type doc: dict
    :type routing: int
    """
    try:
        if doc_id:
            _ = client.update(
                index=index,
                id=doc_id,
                doc=doc,
                doc_as_upsert=True,
                routing=str(routing),
                refresh=True,
            )
        else:
            logger.debug('No value for document id. Creating new document.')
            _ = client.index(
                index=index, document=doc, routing=str(routing), refresh=True
            )
    except (ApiError, NotFoundError, TransportError, BadRequestError) as err:
        msg = f'Error updating document: {err.args[0]}'
        logger.error(msg)
        raise BadClientResult(msg, err)


def verify_index(client: 'Elasticsearch', index: str) -> bool:
    """Verify the index exists and is an index, not an alias

    :param client: A client connection object
    :param index: The index to check

    :type client: :py:class:`~.elasticsearch.Elasticsearch`
    :type index: str
    """
    logger.debug('Verifying index: %s', index)
    retval = True
    response = {}
    try:
        response = dict(
            client.indices.get_settings(
                index=index, expand_wildcards=['open', 'hidden']
            )
        )
    except (ApiError, NotFoundError, TransportError, BadRequestError) as err:
        logger.error("Index: '%s' not found. Error: %s", index, err)
        retval = False
    logger.debug(response)
    if len(list(response.keys())) > 1:
        # We have more than one key, that means we hit an alias
        logger.error('Index %s is one member of an alias.', index)
        retval = False
    elif list(response.keys())[0] != index:
        # There's a 1 to 1 alias, but it is not the index name
        logger.error('Index %s is an alias.', index)
        retval = False
    return retval
