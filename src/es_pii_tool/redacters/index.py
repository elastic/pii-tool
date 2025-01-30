"""Redact data from an Elasticsearch index"""

import typing as t
import logging
from dotmap import DotMap  # type: ignore
from es_pii_tool.exceptions import (
    BadClientResult,
    FatalError,
    MissingIndex,
)
from es_pii_tool.trackables import Task
from es_pii_tool.helpers.utils import (
    exception_msgmaker,
    get_field_matches,
)
from es_pii_tool.helpers import elastic_api as api
from es_pii_tool.redacters.snapshot import RedactSnapshot

if t.TYPE_CHECKING:
    from es_pii_tool.job import Job

logger = logging.getLogger(__name__)


class RedactIndex:
    """Redact index per settings"""

    def __init__(self, index: str, job: 'Job', counter: int):
        try:
            self.task = Task(job, index=index, id_suffix='REDACT-INDEX')
        except Exception as exc:
            logger.critical('Unable to create task: %s', exc)
            raise FatalError('Unable to create task', exc) from exc
        self.index = index
        self.counter = counter
        self.data = DotMap()
        self.verify_index()

    @property
    def success(self) -> bool:
        """Was the redaction a success?"""
        return self._success

    @success.setter
    def success(self, value: bool) -> None:
        self._success = value

    def end_in_failure(
        self,
        exception: t.Union[BadClientResult, MissingIndex],
        reraise: bool = False,
        func: t.Union[t.Callable, None] = None,
        kwargs: t.Union[t.Dict[str, t.Union[bool, str]], None] = None,
    ) -> None:
        """For steps and checks that end in failure, we lump you into this method"""
        msg = exception_msgmaker(exception)
        logger.critical(msg)
        if func:
            if kwargs is None:
                kwargs = {}
                logger.error('Empty kwargs passed')
            if 'logmsg' in kwargs:  # For the task ender
                kwargs['logmsg'] = msg
            func(**kwargs)
        if reraise:
            raise FatalError(msg, exception)

    def verify_index(self):
        """Verify the index exists"""
        # If the index name changed because of an ILM phase shift from hot to cold
        # or cold to frozen, then we should verify the name change here. We should raise
        # an exception if the name of the index changed or it disappeared.
        if not api.verify_index(self.task.job.client, self.index):
            msg = f'Halting execution: Index {self.index} changed or is missing.'
            logger.critical(msg)
            self.success = False
            raise ValueError(msg, 'index not found as expected', self.index)

    def run_query(self):
        """Run the query"""
        self.data.result = DotMap(
            dict(
                api.do_search(
                    self.task.job.client,
                    self.index,
                    self.task.job.config['query'],
                    size=10000,
                )
            )
        )
        self.data.hits = self.data.result.hits.total.value
        logger.debug('Checking document fields on index: %s...', self.index)
        if self.data.hits == 0:
            self.counter += 1
            msg = f'Documents matching redaction query not found on index: {self.index}'
            logger.debug(msg)
            msg = f'Index {self.counter} of {self.task.job.total} processed...'
            logger.info(msg)
            # Record success for this task but send msg to the log field
            # An index could be in the pattern but have no matches.
            self.task.end(True, logmsg=msg)
        self.task.add_log(f"Hits: {self.data.hits}")

    def verify_fields(self):
        """Verify the fields in the query results match what we expect"""
        if not get_field_matches(self.task.job.config, self.data.result.toDict()) > 0:
            msg = f'Fields required for redaction not found on index: {self.index}'
            logger.warning(msg)
            self.task.end(completed=True, logmsg=msg)
            logger.warning(
                'Not a fatal error. Index in pattern does not have the specified fields'
            )

    def get_phase(self):
        """Get the ILM phase (if any) for the index"""
        nope = 'Not assigned an ILM Phase'
        try:
            self.data.phase = api.get_phase(self.task.job.client, self.index) or nope
        except MissingIndex as exc:
            kwargs = {'completed': False, 'errors': True, 'logmsg': 'replaceme'}
            self.end_in_failure(exc, reraise=True, func=self.task.end, kwargs=kwargs)
        logger.debug('Index in phase: %s', self.data.phase.upper())
        self.task.add_log(f'ILM Phase: {self.data.phase}')

    def normal_redact(self):
        """Redact data from a normal (not searchable-snapshot) index"""
        msg = 'Initiating redaction of data from writeable index...'
        logger.info(msg)
        self.task.add_log(msg)
        # As the redact_from_index function doesn't track dry-run, we have to do it
        if not self.task.job.dry_run:
            msg = f'Redacting data from {self.index}'
            logger.info(msg)
            self.task.add_log(msg)
            try:
                api.redact_from_index(
                    self.task.job.client, self.index, self.task.job.config
                )
            except (MissingIndex, BadClientResult) as exc:
                kwargs = {'completed': False, 'errors': True, 'logmsg': 'replaceme'}
                self.end_in_failure(
                    exc, reraise=False, func=self.task.end, kwargs=kwargs
                )
        else:
            msg = f'DRY-RUN: Will not redact data from {self.index}'
            logger.info(msg)
            self.task.add_log(msg)

    def snapshot_redact(self):
        """Redact data from searchable snapshot-backed index"""
        msg = 'Initiating redaction of data from mounted searchable snapshot...'
        logger.info(msg)
        self.task.add_log(msg)
        try:
            snp = RedactSnapshot(self.index, self.task.job, self.data.phase)
        except Exception as exc:
            logger.critical('Unable to build RedactSnapshot object. Exception: %s', exc)
            raise exc
        try:
            snp.run()
        except Exception as exc:
            logger.critical('Unable to run RedactSnapshot object. Exception: %s', exc)
            raise exc

    def run(self):
        """Do the actual run"""
        if self.task.finished():
            self.success = True
            return
        # Log task start time
        self.task.begin()
        self.run_query()
        if self.task.completed:
            self.success = True
            return
        self.verify_fields()
        if self.task.completed:
            self.success = True
            return
        self.get_phase()
        if self.data.phase in ('cold', 'frozen'):
            self.snapshot_redact()
        else:
            self.normal_redact()
        # If we have reached this point, we've succeeded.
        self.counter += 1
        msg = f'Index {self.counter} of {self.task.job.total} processed...'
        logger.info(msg)
        self.task.add_log(msg)
        self.task.end(completed=True, logmsg='DONE')
        self.success = True
