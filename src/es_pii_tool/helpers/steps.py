"""Each function is a single step in PII redaction"""

import typing as t
import time
import logging
from dotmap import DotMap  # type: ignore
from es_wait import IlmPhase, IlmStep
from es_pii_tool.defaults import PAUSE_DEFAULT
from es_pii_tool.exceptions import (
    BadClientResult,
    FatalError,
    MissingArgument,
    MissingError,
    MissingIndex,
    ValueMismatch,
)
from es_pii_tool.trackables import Step
from es_pii_tool.helpers import elastic_api as api
from es_pii_tool.helpers.utils import (
    configure_ilm_policy,
    get_alias_actions,
    strip_ilm_name,
    es_waiter,
    timing,
)

if t.TYPE_CHECKING:
    from es_pii_tool.trackables import Task

logger = logging.getLogger(__name__)


def failed_step(task: 'Task', step: 'Step', exc):
    """Function to avoid repetition of code if a step fails"""
    # MissingIndex, BadClientResult are the only ones inbound
    if isinstance(exc, MissingIndex):
        msg = (
            f'Step failed because index {exc.missing} was not found. The upstream '
            f'exception type was MissingIndex, with error message: '
            f'{exc.upstream.args[0]}'
        )
    elif isinstance(exc, BadClientResult):
        msg = (
            f'Step failed because of a bad or unexpected response or result from '
            f'the Elasticsearch cluster. The upstream exception type was '
            f'BadClientResult, with error message: {exc.upstream.args[0]}'
        )
    else:
        msg = f'Step failed for an unexpected reason: {exc}'
    logger.critical(msg)
    step.end(False, errors=True, logmsg=f'{msg}')
    task.end(False, errors=True, logmsg=f'Failed {step.stepname}')
    raise FatalError(msg, exc)


def metastep(task: 'Task', stepname: str, func, *args, **kwargs) -> None:
    """The reusable step"""
    step = Step(task=task, stepname=stepname)
    if step.finished():
        logger.info('%s: already completed', step.stub)
        return
    step.begin()
    dry_run_safe = kwargs.pop('dry_run_safe', False)
    dry_run_msg = kwargs.pop('dry_run_msg', None)
    include_step = kwargs.pop('include_step', False)
    if include_step:
        kwargs['step'] = step
    if (dry_run_safe and task.job.dry_run) or not task.job.dry_run:
        try:
            response = func(*args, **kwargs)
        except (MissingIndex, BadClientResult, ValueMismatch) as exc:
            failed_step(task, step, exc)
        if response:
            step.add_log(f'{response}')
    else:
        if dry_run_msg is None:
            dry_run_msg = 'No action logged'
        msg = f'Dry-Run: No changes, but expected behavior: {dry_run_msg}'
        step.add_log(msg)
        logger.debug(msg)
    step.end(completed=True, errors=False, logmsg=f'{stepname} completed')


def missing_data(stepname, kwargs) -> None:
    """Avoid duplicated code for data check"""
    if 'data' not in kwargs:
        msg = f'"{stepname}" is missing keyword argument(s)'
        what = 'type: DotMap'
        names = ['data']
        raise MissingArgument(msg, what, names)


def _meta_resolve_index(var: DotMap, data: DotMap) -> str:
    """Make a metastep for resolve_index"""
    result = api.resolve_index(var.client, var.index)
    logger.debug('resolve data: %s', result)
    response = ''
    try:
        data.data_stream = result['indices'][0]['data_stream']
    except KeyError:
        response = f'Index {var.index} is not part of a data_stream'
        logger.debug(response)
    return response


def resolve_index(task: 'Task', stepname: str, var: DotMap, **kwargs) -> None:
    """
    Resolve the index to see if it's part of a data stream
    """
    missing_data(stepname, kwargs)
    data = kwargs['data']
    metastep(task, stepname, _meta_resolve_index, var, data, dry_run_safe=True)


def _meta_pre_delete(var: DotMap) -> str:
    """Make a metastep for pre_delete"""
    response = ''
    # The metastep will handle the "don't do this if dry_run" logic
    try:
        api.delete_index(var.client, var.redaction_target)
    except MissingIndex:
        # Not a problem. This is normal and expected.
        response = f'Pre-delete did not find index "{var.redaction_target}"'
        logger.debug(response)
    return response


def pre_delete(task: 'Task', stepname: str, var: DotMap, **kwargs) -> None:
    """
    Pre-delete the redacted index to ensure no collisions. Ignore if not present
    """
    missing_data(stepname, kwargs)
    drm = 'Delete index {var.redaction_target} (if it exists)'
    metastep(task, stepname, _meta_pre_delete, var, dry_run_msg=drm)


def _meta_restore_index(var: DotMap) -> str:
    """Make a metastep for restore_index"""
    response = f'Restored {var.ss_idx} to {var.redaction_target}'
    try:
        api.restore_index(
            var.client,
            var.repository,
            var.ss_snap,
            var.ss_idx,
            var.redaction_target,
            index_settings=var.restore_settings.toDict(),
        )
    except BadClientResult as bad:
        response = f'Unable to restore {var.ss_idx} to {var.redaction_target}: {bad}'
        logger.error(response)
    return response


def restore_index(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """Restore index from snapshot"""
    missing_data(stepname, kwargs)
    drm = f'Restore {var.ss_idx} to {var.redaction_target}'
    metastep(task, stepname, _meta_restore_index, var, dry_run_msg=drm)


def _meta_get_ilm_data(var: DotMap, data: DotMap) -> str:
    """Make a metastep for get_index_lifecycle_data"""
    res = api.get_settings(var.client, var.index)
    response = ''
    data.index = DotMap()
    data.index.lifecycle = DotMap(
        {'name': None, 'rollover_alias': None, 'indexing_complete': True}
    )
    try:
        data.index.lifecycle = DotMap(res[var.index]['settings']['index']['lifecycle'])
    except KeyError as err:
        response = f'Index {var.index} missing one or more lifecycle keys: {err}'
    if data.index.lifecycle.name:
        response = f'Index lifecycle settings: {data.index.lifecycle}'
    else:
        response = f'Index {var.index} has no ILM lifecycle'
    logger.debug(response)
    return response


def get_index_lifecycle_data(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """
    Populate data.index with index settings results referenced at
    INDEXNAME.settings.index.lifecycle
    """
    missing_data(stepname, kwargs)
    data = kwargs['data']
    metastep(task, stepname, _meta_get_ilm_data, var, data, dry_run_safe=True)


def _meta_get_ilm_explain_data(var: DotMap, data: DotMap) -> str:
    """Make a metastep for get_ilm_explain_data"""
    response = ''
    if data.index.lifecycle.name:
        data.ilm = DotMap()
        try:
            res = api.get_ilm(var.client, var.index)
            data.ilm.explain = DotMap(res['indices'][var.index])
            response = f'ILM explain settings: {data.ilm.explain}'
        except MissingIndex as exc:
            logger.error('Index %s not found in ILM explain data', var.index)
            raise exc
    else:
        response = f'Index {var.index} has no ILM explain data'
    logger.debug(response)
    return response


def get_ilm_explain_data(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """
    Populate data.ilm.explain with ilm_explain data
    """
    missing_data(stepname, kwargs)
    data = kwargs['data']
    metastep(task, stepname, _meta_get_ilm_explain_data, var, data, dry_run_safe=True)


def _meta_get_ilm_lifecycle_data(var: DotMap, data: DotMap) -> str:
    """Make a metastep for get_ilm_lifecycle_data"""
    response = ''
    if data.index.lifecycle.name:
        res = api.get_ilm_lifecycle(var.client, data.index.lifecycle.name)
        if not res:
            msg = f'No such ILM policy: {data.index.lifecycle.name}'
            raise BadClientResult(msg, Exception())
        data.ilm.lifecycle = DotMap(res[data.index.lifecycle.name])
        response = f'ILM lifecycle settings: {data.ilm.lifecycle}'
    else:
        response = f'Index {var.index} has no ILM lifecycle data'
    logger.debug(response)
    return response


def get_ilm_lifecycle_data(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """
    Populate data.ilm.explain with ilm_explain data
    """
    missing_data(stepname, kwargs)
    data = kwargs['data']
    metastep(task, stepname, _meta_get_ilm_lifecycle_data, var, data, dry_run_safe=True)


def clone_ilm_policy(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """
    If this index has an ILM policy, we need to clone it so we can attach
    the new index to it.
    """
    missing_data(stepname, kwargs)
    data = kwargs['data']
    step = Step(task=task, stepname=stepname)
    if step.finished():
        logger.info('%s: already completed', step.stub)
        return
    step.begin()
    if data.index.lifecycle.name is None or not data.ilm.lifecycle.policy:
        _ = f'{stepname}: Index {var.index} has no ILM lifecycle or policy data'
        logger.debug(_)
        step.add_log(_)
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
                msg = f'New policy data matches: {data.new.ilmname}'
                logger.debug(msg)
                step.add_log(msg)
                policymatch = True
                break  # We can drop out of the loop here.
        # Implied else: resp has no value, so the while loop will end.
        policyver += 1
    msg = f'New ILM policy name (may already exist): {data.new.ilmname}'
    logger.debug(msg)
    step.add_log(msg)
    if not task.job.dry_run:  # Don't create if dry_run
        if not policymatch:
            # Create the cloned ILM policy
            try:
                gkw = {'name': data.new.ilmname, 'policy': policy}
                api.generic_get(var.client.ilm.put_lifecycle, **gkw)
            except (MissingError, BadClientResult) as exc:
                _ = f'Unable to put new ILM policy: {exc}'
                logger.error(_)
                step.add_log(_)
                failed_step(task, step, exc)
        # Implied else: We've arrived at the expected new ILM name
        # and it does match an existing policy in name and content
        # so we don't need to create a new one.
    else:
        _ = (
            f'Dry-Run: No changes, but expected behavior: '
            f'ILM policy {data.new.ilmname} created'
        )
        logger.debug(_)
        step.add_log(_)
    step.end(completed=True, errors=False, logmsg=f'{stepname} completed')


def un_ilm_the_restored_index(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """Remove the lifecycle data from the settings of the restored index"""
    missing_data(stepname, kwargs)
    drm = f'Any existing ILM policy removed from {var.redaction_target}'
    metastep(
        task,
        stepname,
        api.remove_ilm_policy,
        var.client,
        var.redaction_target,
        dry_run_msg=drm,
    )


def redact_from_index(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """Run update by query on new restored index"""
    missing_data(stepname, kwargs)
    drm = (
        f'Redact index {var.redaction_target} replacing content of fields: '
        f'{task.job.config["fields"]} with message: {task.job.config["message"]}'
    )
    metastep(
        task,
        stepname,
        api.redact_from_index,
        var.client,
        var.redaction_target,
        task.job.config,
        dry_run_msg=drm,
    )


def _meta_forcemerge_index(task: 'Task', var: DotMap, **kwargs) -> str:
    """Do some task logging around the forcemerge api call"""
    step = kwargs.pop('step', None)
    if step is None:
        raise MissingArgument('_meta_forcemerge_index', 'keyword argument', 'step')
    index = var.redaction_target
    msg = f'Before forcemerge, {api.report_segment_count(var.client, index)}'
    logger.info(msg)
    step.add_log(msg)
    fmkwargs = {}
    if 'forcemerge' in task.job.config:
        fmkwargs = task.job.config['forcemerge']
    fmkwargs['index'] = index
    if 'only_expunge_deletes' in fmkwargs and fmkwargs['only_expunge_deletes']:
        msg = 'Forcemerge will only expunge deleted docs!'
        logger.info(msg)
        step.add_log(msg)
    else:
        mns = 1  # default value
        if 'max_num_segments' in fmkwargs and isinstance(
            fmkwargs['max_num_segments'], int
        ):
            mns = fmkwargs['max_num_segments']
        msg = f'Proceeding to forcemerge to {mns} segments per shard'
        logger.info(msg)
        step.add_log(msg)
    logger.debug('forcemerge kwargs = %s', fmkwargs)
    # Do the actual forcemerging
    api.forcemerge_index(var.client, **fmkwargs)
    msg = f'After forcemerge, {api.report_segment_count(var.client, index)}'
    logger.info(msg)
    step.add_log(msg)
    return msg


def forcemerge_index(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """Force merge redacted index"""
    missing_data(stepname, kwargs)
    msg = ''
    fmkwargs = {}
    if 'forcemerge' in task.job.config:
        fmkwargs = task.job.config['forcemerge']
    if 'only_expunge_deletes' in fmkwargs and fmkwargs['only_expunge_deletes']:
        msg = 'only expunging deleted docs'
    else:
        mns = 1  # default value
        if 'max_num_segments' in fmkwargs and isinstance(
            fmkwargs['max_num_segments'], int
        ):
            mns = fmkwargs['max_num_segments']
        msg = f'to {mns} segments per shard'
    drm = f'Forcemerge index {var.redaction_target} {msg}'
    metastep(
        task,
        stepname,
        _meta_forcemerge_index,
        task,
        var,
        include_step=True,
        dry_run_msg=drm,
    )


def clear_cache(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """Clear cache of redacted index"""
    missing_data(stepname, kwargs)
    drm = f'Clear cache of index {var.redaction_target}'
    metastep(
        task,
        stepname,
        api.clear_cache,
        var.client,
        var.redaction_target,
        dry_run_msg=drm,
    )


def confirm_redaction(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """Check update by query did its job"""
    missing_data(stepname, kwargs)
    drm = f'Confirm redaction of index {var.redaction_target}'
    metastep(
        task,
        stepname,
        api.check_index,
        var.client,
        var.redaction_target,
        task.job.config,
        dry_run_msg=drm,
    )


def snapshot_index(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """Create a new snapshot for mounting our redacted index"""
    missing_data(stepname, kwargs)
    drm = f'Snapshot index {var.redaction_target} to {var.new_snap_name}'
    metastep(
        task,
        stepname,
        api.take_snapshot,
        var.client,
        var.repository,
        var.new_snap_name,
        var.redaction_target,
        dry_run_msg=drm,
    )


def mount_snapshot(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """
    Mount the index as a searchable snapshot to make the redacted index available
    """
    missing_data(stepname, kwargs)
    drm = (
        f'Mount index {var.redaction_target} in snapshot '
        f'{var.new_snap_name} as {var.mount_name}'
    )
    metastep(task, stepname, api.mount_index, var, dry_run_msg=drm)


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
        drm = f'Apply new ILM policy {data.new.ilmname} to {var.mount_name}'
        metastep(
            task,
            stepname,
            api.put_settings,
            var.client,
            var.mount_name,
            settings,
            dry_run_msg=drm,
        )


def confirm_ilm_phase(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """
    Confirm the mounted index is in the expected ILM phase
    This is done by using move_to_step. If it's already in the step, no problem.
    If it's in step ``new``, this will advance the index to the expected step.
    """
    missing_data(stepname, kwargs)
    step = Step(task=task, stepname=stepname)
    if step.finished():
        logger.info('%s: already completed', step.stub)
        return
    step.begin()
    if task.job.dry_run:
        msg = f'Dry-Run: {var.mount_name} moved to ILM phase {var.phase}'
        logger.debug(msg)
        step.add_log(msg)
        step.end(completed=True, errors=False, logmsg=f'{stepname} completed')
        return
    # Wait for phase to be "new"
    pause, timeout = timing('ilm')
    logger.debug(f'ENV pause = {pause}, timeout = {timeout}')
    try:
        # Update in es_wait 0.9.2:
        # - If you send phase='new', it will wait for the phase to be 'new' or higher
        # - This is where a user was getting stuck. They were waiting for 'new' but
        # - the phase was already 'frozen', so it was endlessly checking for 'new'.
        es_waiter(
            var.client,
            IlmPhase,
            name=var.mount_name,
            phase='new',
            pause=pause,
            timeout=timeout,
        )
        # Wait for step to be "complete"
        es_waiter(
            var.client, IlmStep, name=var.mount_name, pause=pause, timeout=timeout
        )
    except BadClientResult as bad:
        _ = f'ILM step confirmation problem -- ERROR: {bad}'
        logger.error(_)
        step.add_log(_)
        failed_step(task, step, bad)

    def get_currstep():
        try:
            _ = api.generic_get(var.client.ilm.explain_lifecycle, index=var.mount_name)
        except MissingError as exc:
            _ = f'Unable to get ILM phase of {var.mount_name}'
            logger.error(_)
            step.add_log(_)
            failed_step(task, step, exc)
        try:
            expl = _['indices'][var.mount_name]
        except KeyError as err:
            msg = f'{var.mount_name} not found in ILM explain data: {err}'
            logger.error(msg)
            step.add_log(msg)
            failed_step(task, step, err)
        if 'managed' not in expl:
            msg = f'Index {var.mount_name} is not managed by ILM'
            step.add_log(msg)
            failed_step(
                task, step, ValueMismatch(msg, expl['managed'], '{"managed": True}')
            )
        return {'phase': expl['phase'], 'action': expl['action'], 'name': expl['step']}

    nextstep = {'phase': var.phase, 'action': 'complete', 'name': 'complete'}
    if task.job.dry_run:  # Don't actually move_to_step if dry_run
        msg = (
            f'{stepname}: Dry-Run: {var.mount_name} not moved/confirmed to ILM '
            f'phase {var.phase}'
        )
        logger.debug(msg)
        step.end(completed=True, errors=False, logmsg=f'{stepname} completed')
        return

    # We will try to move the index to the expected phase up to 3 times
    # before failing the step.
    attempts = 0
    success = False
    while attempts < 3 and not success:
        # Since we are now testing for 'new' or higher, we may not need to advance
        # ILM phases. If the current step is already where we expect to be, log
        # confirmation and move on.
        logger.debug('Attempt number: %s', attempts)
        currstep = get_currstep()
        if currstep == nextstep:
            msg = (
                f'{stepname}: {var.mount_name} is confirmed to be in ILM phase '
                f'{var.phase}'
            )
            logger.debug(msg)
            step.add_log(msg)
            # Set both while loop critera to values that will end the loop
            success = True
            attempts = 3
        else:
            # If we are not yet in the expected target phase, then proceed with the
            # ILM phase change.
            logger.debug('Current ILM Phase: %s', currstep)
            logger.debug('Target ILM Phase: %s', nextstep)
            logger.debug('PHASE: %s', var.phase)
            try:
                api.ilm_move(var.client, var.mount_name, currstep, nextstep)
                success = True
            except BadClientResult as bad:
                logger.debug('Attempt failed. Incrementing attempts.')
                attempts += 1
                if attempts == 3:
                    _ = 'Attempt limit reached. Failing step.'
                    logger.error(_)
                    step.add_log(_)
                    failed_step(task, step, bad)
                logger.debug('Waiting %s seconds before retrying...', PAUSE_DEFAULT)
                time.sleep(float(PAUSE_DEFAULT))
                logger.warning('ILM move failed: %s -- Retrying...', bad.message)
                continue
            pause, timeout = timing('ilm')
            logger.debug(f'ENV pause = {pause}, timeout = {timeout}')
            try:
                es_waiter(
                    var.client,
                    IlmPhase,
                    name=var.mount_name,
                    phase=var.phase,
                    pause=pause,
                    timeout=timeout,
                )
                es_waiter(
                    var.client,
                    IlmStep,
                    name=var.mount_name,
                    pause=pause,
                    timeout=timeout,
                )
            except BadClientResult as phase_err:
                msg = f'Unable to wait for ILM step to complete -- ERROR: {phase_err}'
                logger.error(msg)
                step.add_log(msg)
                failed_step(task, step, phase_err)
    # If we make it here, we have successfully moved the index to the expected phase
    step.end(completed=True, errors=False, logmsg=f'{stepname} completed')


def delete_redaction_target(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """
    Now that it's mounted (with a new name), we should delete the redaction_target
    index
    """
    missing_data(stepname, kwargs)
    drm = f'Delete redaction target index {var.redaction_target}'
    metastep(
        task,
        stepname,
        api.delete_index,
        var.client,
        var.redaction_target,
        dry_run_msg=drm,
    )


def fix_aliases(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """Using the aliases collected from var.index, update mount_name and verify"""
    missing_data(stepname, kwargs)
    data = kwargs['data']
    step = Step(task=task, stepname=stepname)
    if step.finished():
        logger.info('%s: already completed', step.stub)
        return
    step.begin()
    if data.data_stream:
        msg = 'Cannot apply aliases to indices in data_stream'
        logger.debug(msg)
        step.add_log(msg)
        step.end(completed=True, errors=False, logmsg=f'{stepname} completed')
        return
    alias_names = var.aliases.toDict().keys()
    if not alias_names:
        msg = f'No aliases associated with index {var.index}'
        step.add_log(msg)
        logger.info(msg)
    elif not task.job.dry_run:
        msg = f'Transferring aliases to new index ' f'{var.mount_name}'
        logger.debug(msg)
        step.add_log(msg)
        var.client.indices.update_aliases(
            actions=get_alias_actions(var.index, var.mount_name, var.aliases.toDict())
        )
        verify = var.client.indices.get(index=var.mount_name)[var.mount_name][
            'aliases'
        ].keys()
        if alias_names != verify:
            msg = f'Alias names do not match! {alias_names} does not match: {verify}'
            logger.critical(msg)
            step.add_log(msg)
            failed_step(
                task, step, ValueMismatch(msg, 'alias names mismatch', alias_names)
            )
    else:
        msg = 'Dry-Run: alias transfer not executed'
        logger.debug(msg)
        step.add_log(msg)
    step.end(completed=True, errors=False, logmsg=f'{stepname} completed')


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


def delete_old_index(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """Delete old mounted snapshot, if configured to do so"""
    missing_data(stepname, kwargs)
    step = Step(task=task, stepname=stepname)
    if step.finished():
        logger.info('%s: already completed', step.stub)
        return
    step.begin()
    if task.job.config['delete']:
        msg = f'Deleting original mounted index: {var.index}'
        task.add_log(msg)
        logger.info(msg)
        try:
            api.delete_index(var.client, var.index)
        except MissingIndex as miss:
            msg = f'Index {var.index} not found for deletion: {miss}'
            logger.error(msg)
            step.add_log(msg)
        except BadClientResult as bad:
            msg = f'Bad client result: {bad}'
            logger.error(msg)
            step.add_log(msg)
            failed_step(task, step, bad)
    else:
        msg = (
            f'delete set to False — not deleting original mounted index: '
            f'{var.index}'
        )
        task.add_log(msg)
        logger.warning(msg)
    step.end(completed=True, errors=False, logmsg=f'{stepname} completed')


def assign_aliases(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """Put the starting index name on new mounted index as alias"""
    missing_data(stepname, kwargs)
    data = kwargs['data']
    step = Step(task=task, stepname=stepname)
    if step.finished():
        logger.info('%s: already completed', step.stub)
        return
    step.begin()
    if data.data_stream:
        msg = 'Cannot apply aliases to indices in data_stream'
        logger.debug(msg)
        step.add_log(msg)
        step.end(completed=True, errors=False, logmsg=f'{stepname} completed')
        return
    if not task.job.dry_run:
        msg = f'Assigning aliases {var.index} to index {var.mount_name}'
        logger.debug(msg)
        step.add_log(msg)
        try:
            api.assign_alias(var.client, var.mount_name, var.index)
        except BadClientResult as bad:
            failed_step(task, step, bad)
    else:
        msg = f'Assigning aliases {var.index} to index {var.mount_name}'
        _ = f'Dry-Run: No changes, but expected behavior: {msg}'
        logger.debug(_)
        step.add_log(_)
    step.end(completed=True, errors=False, logmsg=f'{stepname} completed')


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
        drm = f'Reassociate index {var.mount_name} with data_stream {data.data_stream}'
        metastep(
            task, stepname, api.modify_data_stream, var.client, acts, dry_run_msg=drm
        )


def _meta_record_it(task: 'Task', snapname: str) -> str:
    """Make a metastep for record_it"""
    task.job.cleanup.append(snapname)
    return f'Snapshot {snapname} added to cleanup list'


def record_it(task: 'Task', stepname, var: DotMap, **kwargs) -> None:
    """Record the now-deletable snapshot in the job's tracking index."""
    missing_data(stepname, kwargs)
    drm = f'Snapshot {var.ss_snap} added to cleanup list'
    metastep(task, stepname, _meta_record_it, task, var.ss_snap, dry_run_msg=drm)
