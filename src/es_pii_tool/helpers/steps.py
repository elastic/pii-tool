"""Each function is a single step in PII redaction"""

from os import getenv
import typing as t
import logging
from dotmap import DotMap  # type: ignore
from es_wait import IlmPhase, IlmStep
from es_pii_tool.defaults import (
    PAUSE_DEFAULT,
    PAUSE_ENVVAR,
    TIMEOUT_DEFAULT,
    TIMEOUT_ENVVAR,
)
from es_pii_tool.exceptions import (
    BadClientResult,
    FatalError,
    MissingArgument,
    MissingError,
    MissingIndex,
    ValueMismatch,
)
from es_pii_tool.helpers import elastic_api as api
from es_pii_tool.helpers.utils import (
    configure_ilm_policy,
    get_alias_actions,
    strip_ilm_name,
    es_waiter,
)

if t.TYPE_CHECKING:
    from es_pii_tool.task import Task

PAUSE_VALUE = float(getenv(PAUSE_ENVVAR, default=PAUSE_DEFAULT))
TIMEOUT_VALUE = float(getenv(TIMEOUT_ENVVAR, default=TIMEOUT_DEFAULT))

logger = logging.getLogger(__name__)


def log_step(task, stepname: str, kind: str):
    """Function to avoid repetition of code"""
    msgmap = {
        'start': 'starting...',
        'end': 'completed.',
        'dry-run': 'DRY-RUN. No change will take place',
    }
    msg = f'{stepname} {msgmap[kind]}'
    logger.info(msg)
    task.add_log(msg)


def failed_step(task: 'Task', stepname: str, exc):
    """Function to avoid repetition of code if a step fails"""
    # MissingIndex, BadClientResult are the only ones inbound
    upstream = (
        f'The upstream exception type was {exc.upstream.__name__}, '
        f'with error message: {exc.upstream.args[0]}'
    )
    if isinstance(exc, MissingIndex):
        msg = f'Step failed because index {exc.missing} was not found. {upstream}'
    elif isinstance(exc, BadClientResult):
        msg = (
            f'Step failed because of a bad or unexpected response or result from '
            f'the Elasticsearch cluster. {upstream}'
        )
    else:
        msg = f'Step failed for an unexpected reason: {exc}'
    logger.critical(msg)
    task.end(False, errors=True, logmsg=f'Failed {stepname}: {msg}')
    raise FatalError(msg, exc)


def metastep(task: 'Task', stepname: str, func, *args, **kwargs) -> None:
    """The reusable step"""
    log_step(task, stepname, 'start')
    if not task.job.dry_run:
        try:
            func(*args, **kwargs)
        except (MissingIndex, BadClientResult) as exc:
            failed_step(task, stepname, exc)
    else:
        logger.debug('%s: Dry-Run: No action taken', stepname)
        log_step(task, stepname, 'dry-run')
    log_step(task, stepname, 'end')


def missing_data(stepname, kwargs) -> None:
    """Avoid duplicated code for data check"""
    if 'data' not in kwargs:
        msg = f'"{stepname}" is missing keyword argument(s)'
        what = 'type: DotMap'
        names = ['data']
        raise MissingArgument(msg, what, names)


def fmwrapper(task: 'Task', stepname: str, var: DotMap) -> None:
    """Do some task logging around the forcemerge api call"""
    index = var.redaction_target
    msg = f'{stepname} Before forcemerge, {api.report_segment_count(var.client, index)}'
    logger.info(msg)
    task.add_log(msg)
    fmkwargs = {}
    if 'forcemerge' in task.job.config:
        fmkwargs = task.job.config['forcemerge']
    fmkwargs['index'] = index
    if 'only_expunge_deletes' in fmkwargs and fmkwargs['only_expunge_deletes']:
        msg = 'Forcemerge will only expunge deleted docs!'
        logger.info(msg)
        task.add_log(msg)
    else:
        mns = 1  # default value
        if 'max_num_segments' in fmkwargs and isinstance(
            fmkwargs['max_num_segments'], int
        ):
            mns = fmkwargs['max_num_segments']
        msg = f'Proceeding to forcemerge to {mns} segments per shard'
        logger.info(msg)
        task.add_log(msg)
    logger.debug('forcemerge kwargs = %s', fmkwargs)
    # Do the actual forcemerging
    api.forcemerge_index(var.client, **fmkwargs)
    msg = f'After forcemerge, {api.report_segment_count(var.client, index)}'
    logger.info(msg)
    task.add_log(msg)
    logger.info('Forcemerge completed.')


def resolve_index(task: 'Task', stepname: str, var: DotMap, **kwargs) -> None:
    """
    Resolve the index to see if it's part of a data stream
    """
    missing_data(stepname, kwargs)
    data = kwargs['data']
    log_step(task, stepname, 'start')
    result = api.resolve_index(var.client, var.index)
    logger.debug('resolve data: %s', result)
    try:
        data.data_stream = result['indices'][0]['data_stream']
    except KeyError:
        logger.debug('%s: Index %s is not part of a data_stream', stepname, var.index)
    log_step(task, stepname, 'end')


def pre_delete(task: 'Task', stepname: str, var: DotMap, **kwargs) -> None:
    """
    Pre-delete the redacted index to ensure no collisions. Ignore if not present
    """
    missing_data(stepname, kwargs)
    log_step(task, stepname, 'start')
    if not task.job.dry_run:
        try:
            api.delete_index(var.client, var.redaction_target)
        except MissingIndex:
            logger.debug(
                '%s: Pre-delete did not find index "%s"',
                stepname,
                var.redaction_target,
            )
            # No problem. This is expected.
    else:
        log_step(task, stepname, 'dry-run')
    log_step(task, stepname, 'end')


def restore_index(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """Restore index from snapshot"""
    missing_data(stepname, kwargs)
    metastep(
        task,
        stepname,
        api.restore_index,
        var.client,
        var.repository,
        var.ss_snap,
        var.ss_idx,
        var.redaction_target,
        index_settings=var.restore_settings.toDict(),
    )


def get_index_lifecycle_data(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """
    Populate data.index with index settings results referenced at
    INDEXNAME.settings.index.lifecycle
    """
    missing_data(stepname, kwargs)
    data = kwargs['data']
    log_step(task, stepname, 'start')
    data.index = DotMap()
    res = api.get_settings(var.client, var.index)
    # Set a default value in case we are dealing with non-ILM indices
    data.index.lifecycle = DotMap(
        {'name': None, 'rollover_alias': None, 'indexing_complete': True}
    )
    try:
        data.index.lifecycle = DotMap(res[var.index]['settings']['index']['lifecycle'])
    except KeyError as err:
        logger.debug(
            '%s: Index %s missing one or more lifecycle keys: %s',
            stepname,
            var.index,
            err,
        )
    if data.index.lifecycle.name:
        logger.debug('%s: Index lifecycle settings: %s', stepname, data.index.lifecycle)
    else:
        logger.debug('%s: Index %s has no ILM lifecycle', stepname, var.index)
    log_step(task, stepname, 'end')


def get_ilm_explain_data(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """
    Populate data.ilm.explain with ilm_explain data
    """
    missing_data(stepname, kwargs)
    data = kwargs['data']
    log_step(task, stepname, 'start')
    if data.index.lifecycle.name:
        data.ilm = DotMap()
        try:
            res = api.get_ilm(var.client, var.index)
            data.ilm.explain = DotMap(res['indices'][var.index])
            logger.debug('%s: ILM explain settings: %s', stepname, data.ilm.explain)
        except MissingIndex as exc:
            failed_step(task, stepname, exc)
    else:
        logger.debug('%s: Index %s has no ILM explain data', stepname, var.index)
    log_step(task, stepname, 'end')


def get_ilm_lifecycle_data(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """
    Populate data.ilm.explain with ilm_explain data
    """
    missing_data(stepname, kwargs)
    data = kwargs['data']
    log_step(task, stepname, 'start')
    if data.index.lifecycle.name:
        res = api.get_ilm_lifecycle(var.client, data.index.lifecycle.name)
        if not res:
            msg = f'No such ILM policy: {data.index.lifecycle.name}'
            failed_step(
                task,
                stepname,
                BadClientResult(msg, Exception()),
            )
        data.ilm.lifecycle = DotMap(res[data.index.lifecycle.name])
        logger.debug('%s: ILM lifecycle settings: %s', stepname, data.ilm.lifecycle)

    else:
        logger.debug('%s: Index %s has no ILM lifecycle data', stepname, var.index)
    log_step(task, stepname, 'end')


def clone_ilm_policy(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """
    If this index has an ILM policy, we need to clone it so we can attach
    the new index to it.
    """
    missing_data(stepname, kwargs)
    data = kwargs['data']
    log_step(task, stepname, 'start')
    if data.index.lifecycle.name is None or not data.ilm.lifecycle.policy:
        logger.debug(
            '%s: Index %s has no ILM lifecycle or policy data', stepname, var.index
        )
        log_step(task, stepname, 'end')
        return
    data.new = DotMap()

    # From here, we check for matching named cloned policy

    configure_ilm_policy(task, data)

    # New ILM policy naming: pii-tool-POLICYNAME---v###
    stub = f'pii-tool-{strip_ilm_name(data.index.lifecycle.name)}'
    policy = data.new.ilmpolicy.toDict()  # For comparison
    resp = {'dummy': 'startval'}  # So the while loop can start with something
    policyver = 0  # Our version number starting point.
    policymatch = False
    while resp:
        data.new.ilmname = f'{stub}---v{policyver + 1:03}'
        resp = api.get_ilm_lifecycle(var.client, data.new.ilmname)  # type: ignore
        if resp:  # We have data, so the name matches
            # Compare the new policy to the one just returned
            if policy == resp[data.new.ilmname]['policy']:  # type: ignore
                logger.debug('New policy data matches: %s', data.new.ilmname)
                policymatch = True
                break  # We can drop out of the loop here.
        # Implied else: resp has no value, so the while loop will end.
        policyver += 1
    logger.debug('New ILM policy name (may already exist): %s', data.new.ilmname)
    if not task.job.dry_run:  # Don't create if dry_run
        if not policymatch:
            # Create the cloned ILM policy
            try:
                gkw = {'name': data.new.ilmname, 'policy': policy}
                api.generic_get(var.client.ilm.put_lifecycle, **gkw)
            except (MissingError, BadClientResult) as exc:
                logger.error('Unable to put new ILM policy: %s', exc)
                failed_step(task, stepname, exc)
        # Implied else: We've arrived at the expected new ILM name
        # and it does match an existing policy in name and content
        # so we don't need to create a new one.
    else:
        logger.debug(
            '%s: Dry-Run: ILM policy not created: %s', stepname, data.new.ilmname
        )
        log_step(task, stepname, 'dry-run')
    log_step(task, stepname, 'end')


def un_ilm_the_restored_index(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """Remove the lifecycle data from the settings of the restored index"""
    missing_data(stepname, kwargs)
    metastep(task, stepname, api.remove_ilm_policy, var.client, var.redaction_target)


def redact_from_index(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """Run update by query on new restored index"""
    missing_data(stepname, kwargs)
    metastep(
        task,
        stepname,
        api.redact_from_index,
        var.client,
        var.redaction_target,
        task.job.config,
    )


def forcemerge_index(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """Force merge redacted index"""
    missing_data(stepname, kwargs)
    metastep(task, stepname, fmwrapper, task, stepname, var)


def clear_cache(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """Clear cache of redacted index"""
    missing_data(stepname, kwargs)
    metastep(task, stepname, api.clear_cache, var.client, var.redaction_target)


def confirm_redaction(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """Check update by query did its job"""
    missing_data(stepname, kwargs)
    metastep(
        task,
        stepname,
        api.check_index,
        var.client,
        var.redaction_target,
        task.job.config,
    )


def snapshot_index(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """Create a new snapshot for mounting our redacted index"""
    missing_data(stepname, kwargs)
    metastep(
        task,
        stepname,
        api.take_snapshot,
        var.client,
        var.repository,
        var.new_snap_name,
        var.redaction_target,
    )


def mount_snapshot(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """
    Mount the index as a searchable snapshot to make the redacted index available
    """
    missing_data(stepname, kwargs)
    metastep(task, stepname, api.mount_index, var)


def apply_ilm_policy(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """
    If the index was associated with an ILM policy, associate it with the
    new, cloned ILM policy.
    """
    missing_data(stepname, kwargs)
    data = kwargs['data']
    if data.new.ilmname:
        settings = {'index': {}}  # type: ignore
        # Add all of the original lifecycle settings
        settings['index']['lifecycle'] = data.index.lifecycle.toDict()
        # Replace the name with the new ILM policy name
        settings['index']['lifecycle']['name'] = data.new.ilmname
        metastep(task, stepname, api.put_settings, var.client, var.mount_name, settings)


def confirm_ilm_phase(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """
    Confirm the mounted index is in the expected ILM phase
    This is done by using move_to_step. If it's already in the step, no problem.
    If it's in step ``new``, this will advance the index to the expected step.
    """
    missing_data(stepname, kwargs)
    log_step(task, stepname, 'start')
    # Wait for phase to be "new"
    waitkw = {'pause': PAUSE_VALUE, 'timeout': TIMEOUT_VALUE}
    try:
        es_waiter(var.client, IlmPhase, name=var.mount_name, phase='new', **waitkw)
        es_waiter(var.client, IlmStep, name=var.mount_name, **waitkw)
    except BadClientResult as exc:
        failed_step(task, stepname, exc)

    try:
        _ = api.generic_get(var.client.ilm.explain_lifecycle, index=var.mount_name)
    except MissingError as exc:
        logger.error('Cannot confirm %s is in phase %s', var.mount_name, var.phase)
        failed_step(task, stepname, exc)
    expl = _['indices'][var.mount_name]
    if not expl['managed']:
        msg = f'Index {var.mount_name} is not managed by ILM'
        raise ValueMismatch(msg, expl['managed'], '{"managed": True}')
    currstep = {'phase': expl['phase'], 'action': expl['action'], 'name': expl['step']}
    nextstep = {'phase': var.phase, 'action': 'complete', 'name': 'complete'}
    if not task.job.dry_run:  # Don't actually move_to_step if dry_run
        logger.debug('currstep: %s', currstep)
        logger.debug('nextstep: %s', nextstep)
        logger.debug('PHASE: %s', var.phase)
        try:
            api.ilm_move(var.client, var.mount_name, currstep, nextstep)
        except BadClientResult as exc:
            failed_step(task, stepname, exc)
        try:
            es_waiter(
                var.client, IlmPhase, name=var.mount_name, phase=var.phase, **waitkw
            )
            es_waiter(var.client, IlmStep, name=var.mount_name, **waitkw)
        except BadClientResult as phase_err:
            msg = f'Unable to wait for ILM step to complete: ERROR :{phase_err}'
            logger.error(msg)
            failed_step(task, stepname, phase_err)
    else:
        msg = (
            f'{stepname}: Dry-Run: {var.mount_name} not moved/confirmed to ILM '
            f'phase {var.phase}'
        )
        logger.debug(msg)
        log_step(task, stepname, 'dry-run')
    log_step(task, stepname, 'end')


def delete_redaction_target(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """
    Now that it's mounted (with a new name), we should delete the redaction_target
    index
    """
    missing_data(stepname, kwargs)
    metastep(task, stepname, api.delete_index, var.client, var.redaction_target)


def fixalias_builder(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """This is makes the real fixalias a one liner"""
    data = kwargs['data']
    if data.data_stream:
        msg = f'{stepname} Cannot apply aliases to indices in data_stream'
        logger.debug(msg)
        task.add_log(msg)
        return
    alias_names = var.aliases.toDict().keys()
    if not alias_names:
        msg = f'{stepname} No aliases associated with index {var.index}'
        task.add_log(msg)
        logger.warning(msg)
    else:
        msg = f'{stepname} Transferring aliases to new index ' f'{var.mount_name}'
        task.add_log(msg)
        logger.debug(msg)
        var.client.indices.update_aliases(
            actions=get_alias_actions(var.index, var.mount_name, var.aliases.toDict())
        )
        verify = var.client.indices.get(index=var.mount_name)[var.mount_name][
            'aliases'
        ].keys()
        if alias_names != verify:
            msg = f'Alias names do not match! {alias_names} does not match: {verify}'
            msg2 = f'Failed {stepname}: {msg}'
            logger.critical(msg2)
            task.add_log(msg2)
            raise ValueMismatch(msg, 'alias names mismatch', alias_names)


def fix_aliases(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """Using the aliases collected from var.index, update mount_name and verify"""
    missing_data(stepname, kwargs)
    metastep(task, stepname, fixalias_builder, task, stepname, var, **kwargs)


def un_ilm_the_original_index(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """
    Remove the lifecycle data from the settings of the original index

    This is chiefly done as a safety measure.
    """
    missing_data(stepname, kwargs)
    metastep(task, stepname, api.remove_ilm_policy, var.client, var.index)


def close_old_index(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """Close old mounted snapshot"""
    missing_data(stepname, kwargs)
    metastep(task, stepname, api.close_index, var.client, var.index)


def delete_old_index_builder(task: 'Task', stepname, var: DotMap) -> None:
    """This makes delete_old_index work with metastep"""
    if task.job.config['delete']:
        msg = f'Deleting original mounted index: {var.index}'
        task.add_log(msg)
        logger.info(msg)
        try:
            api.delete_index(var.client, var.index)
        except MissingIndex as exc:
            failed_step(task, stepname, exc)
    else:
        msg = (
            f'delete set to False — not deleting original mounted index: '
            f'{var.index}'
        )
        task.add_log(msg)
        logger.warning(msg)


def delete_old_index(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """Delete old mounted snapshot, if configured to do so"""
    missing_data(stepname, kwargs)
    metastep(task, stepname, delete_old_index_builder, task, stepname, var)


def assign_aliases(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """Put the starting index name on new mounted index as alias"""
    missing_data(stepname, kwargs)
    data = kwargs['data']
    if data.data_stream:
        log_step(task, stepname, 'start')
        msg = f'{stepname}: Cannot apply aliases to indices in data_stream'
        logger.debug(msg)
        log_step(task, stepname, 'end')
        return
    metastep(task, stepname, api.assign_alias, var.client, var.mount_name, var.index)


def reassociate_index_with_ds(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """
    If the index was associated with a data_stream, reassociate it with the
    data_stream again.
    """
    missing_data(stepname, kwargs)
    data = kwargs['data']
    acts = [{'add_backing_index': {'index': var.mount_name}}]
    if data.data_stream:
        acts[0]['add_backing_index']['data_stream'] = data.data_stream
        logger.debug('%s: Modify data_stream actions: %s', stepname, acts)
        metastep(task, stepname, api.modify_data_stream, var.client, acts)


def record_it(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """Record the now-deletable snapshot in the job's tracking index."""
    missing_data(stepname, kwargs)
    log_step(task, stepname, 'start')
    task.job.cleanup.append(var.ss_snap)
    log_step(task, stepname, 'end')
