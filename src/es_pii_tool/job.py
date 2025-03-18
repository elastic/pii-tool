"""Functions for creating & updating the progress/status update doc in Elasticsearch"""

import typing as t
import logging
from es_pii_tool.defaults import index_settings, status_mappings
from es_pii_tool.exceptions import (
    BadClientResult,
    FatalError,
    MissingDocument,
    MissingIndex,
)
from es_pii_tool.helpers.elastic_api import (
    create_index,
    get_index,
    get_tracking_doc,
    update_doc,
)
from es_pii_tool.helpers.utils import now_iso8601, parse_job_config

if t.TYPE_CHECKING:
    from elasticsearch8 import Elasticsearch

logger = logging.getLogger(__name__)

# pylint: disable=R0902,R0904,R0913,R0917


class Job:
    """Class to manage a redaction job"""

    ATTRLIST = ['start_time', 'completed', 'end_time', 'errors', 'logs']

    def __init__(
        self,
        client: 'Elasticsearch',
        index: str,
        name: str,
        config: t.Dict,
        dry_run: bool = False,
    ):
        self.client = client
        self.index = index
        self.name = name
        self.file_config = config
        self.dry_run = dry_run
        self.prev_dry_run = False
        self.cleanup: list[str] = []
        try:
            # If the index is already existent, this function will log that fact and
            # return cleanly
            args = (client, index)
            kwargs = {'settings': index_settings(), 'mappings': status_mappings()}
            create_index(*args, **kwargs)  # type: ignore
        except BadClientResult as exc:
            logger.critical(exc.message)
            raise FatalError(
                f'Unexpected, but fatal error trying to create index {index}', exc
            ) from exc
        self.get_history()

    @property
    def config(self) -> t.Dict:
        """
        :getter: Get the job configuration dictionary
        :setter: Set the job configuration dictionary
        :type: dict
        """
        return self._config

    @config.setter
    def config(self, value: t.Dict) -> None:
        self._config = value

    @property
    def indices(self) -> t.Sequence[str]:
        """
        :getter: Get the list of indices in this job
        :setter: Set the list of indices in this job
        :type: list
        """
        return self._indices

    @indices.setter
    def indices(self, value: t.Sequence[str]) -> None:
        self._indices = value

    @property
    def total(self) -> int:
        """
        :getter: Get the count of indices in this job
        :setter: Set the count of indices in this job
        :type: int
        """
        return self._total

    @total.setter
    def total(self, value: int) -> None:
        self._total = value

    @property
    def status(self) -> t.Dict:
        """
        :getter: Get the job status
        :setter: Set the job status
        :type: dict
        """
        return self._status

    @status.setter
    def status(self, value: t.Dict) -> None:
        self._status = value

    @property
    def start_time(self) -> str:
        """
        :getter: Get the ISO8601 string representing the start time of this job
        :setter: Set the ISO8601 string representing the start time of this job
        :type: str
        """
        return self._start_time

    @start_time.setter
    def start_time(self, value: str) -> None:
        self._start_time = value

    @property
    def end_time(self) -> str:
        """
        :getter: Get the ISO8601 string representing the end time of this job
        :setter: Set the ISO8601 string representing the end time of this job
        :type: str
        """
        return self._end_time

    @end_time.setter
    def end_time(self, value: str) -> None:
        self._end_time = value

    @property
    def completed(self) -> bool:
        """
        :getter: Get the job completion state
        :setter: Set the job completion state
        :type: bool
        """
        return self._completed

    @completed.setter
    def completed(self, value: bool) -> None:
        self._completed = value

    @property
    def errors(self) -> bool:
        """
        :getter: Get job error state
        :setter: Set job error state
        :type: bool
        """
        return self._errors

    @errors.setter
    def errors(self, value: bool) -> None:
        self._errors = value

    @property
    def logs(self) -> t.Sequence[str]:
        """
        :getter: Get job logs
        :setter: Set job logs
        :type: list
        """
        return self._logs

    @logs.setter
    def logs(self, value: t.Sequence[str]) -> None:
        self._logs = value

    def add_log(self, value: str) -> None:
        """Append another entry to :py:attr:`logs`"""
        try:
            if self.logs is None:
                _ = []
                _.append(f'{now_iso8601()} {value}')
            else:
                _ = self.logs
                _.append(f'{now_iso8601()} {value}')
            self.logs = _
        except Exception as exc:
            logger.critical(f'Unable to add log entry: {exc}')
            raise exc

    def get_status(self, data: t.Dict) -> t.Dict:
        """Read the status keys from the data

        :param data: The raw contents of the job progress doc

        :returns: Dictionary of results extracted from data
        """
        result = {}
        for key in self.ATTRLIST:
            if key in data:
                result[key] = data[key]
            else:
                result[key] = None
        if not result:
            logger.info('No execution status for job %s', self.name)
        if 'dry_run' in result:
            if result['dry_run']:
                logger.info('Prior record of job %s was a dry-run', self.name)
                self.prev_dry_run = True
        return result

    def update_status(self) -> None:
        """Update instance attribute doc with the current values"""
        contents = {}
        for val in self.ATTRLIST:
            contents[val] = getattr(self, val)
        self.status = contents

    def build_doc(self) -> t.Dict:
        """Build the dictionary which will be the written to the tracking doc

        :returns: The tracking doc dictionary
        """
        doc = {}
        self.update_status()
        for key in self.ATTRLIST:
            doc[key] = self.status[key]
        if 'config' not in doc:
            doc['config'] = {}
        doc['job'] = self.name
        doc['join_field'] = 'job'
        doc['config'] = parse_job_config(self.config, 'write')
        doc['dry_run'] = self.dry_run
        if not self.dry_run:
            doc['cleanup'] = self.cleanup
        # logger.debug('Updated tracking doc: %s', doc)
        return doc

    def get_job(self) -> None:
        """
        Get any job history that may exist for :py:attr:`name`

        Set :py:meth:`status` with the results.
        """
        result = {}
        try:
            result = get_tracking_doc(self.client, self.index, self.name)
        except MissingDocument:
            logger.debug('Job tracking doc does not yet exist.')
            self.config = {}
            self.status = {}
            return
        except Exception as exc:
            logger.critical(exc.args[0])  # First arg is always message
            raise FatalError('We experienced a fatal error', exc) from exc
        try:
            self.config = parse_job_config(result['config'], 'read')
        except KeyError:
            logger.info('No configuration data for job %s', self.name)
            self.config = {}
        self.status = self.get_status(result)

    def launch_prep(self) -> None:
        """
        We don't need to do these actions until :py:meth:`begin` calls this method

        1. Log dry-run status
        2. Set :py:meth:`indices` with the list of indices matching the search pattern
            in the configuration file.
        3. Set :py:meth:`total` with the count of indices.
        """
        if self.dry_run:
            msg = 'DRY-RUN: No changes will be made'
            logger.info(msg)
            self.add_log(msg)
        try:
            self.indices = list(get_index(self.client, self.config['pattern']))
        except (MissingIndex, Exception) as exc:
            logger.critical(f'Fatal Error getting indices: {exc}')
            raise exc
        logger.debug('Indices from provided pattern: %s', self.indices)
        self.total = len(self.indices)
        logger.debug("Total number of indices to scrub: %s", self.total)

    def load_status(self) -> None:
        """Load prior status values (or not)"""
        for key in self.ATTRLIST:
            if self.prev_dry_run:
                # If our last run was a dry run, set each other attribute to None
                setattr(self, key, None)
            else:
                if key in self.status:
                    setattr(self, key, self.status[key])
                else:
                    setattr(self, key, None)

    def get_history(self) -> None:
        """
        Get the history of a job, if any. Ensure all values are populated from the doc,
        or None
        """
        logger.debug('Pulling any history for job: %s', self.name)
        try:
            self.get_job()
        except MissingIndex as exc:
            logger.critical('Missing index: %s', exc.missing)
            raise FatalError(
                f'Fatal error encountered. Index {exc.missing} was not found', exc
            ) from exc
        if not self.config:
            logger.info(
                'No stored config for job: %s. Using file-based config', self.name
            )
            self.config = self.file_config
        if not self.status:
            logger.debug('No event history for job: %s', self.name)
        self.load_status()

    def report_history(self) -> None:
        """
        Report the history of any prior attempt to run the Job
        Log aspects of the history here.
        """
        prefix = f'The prior run of job: {self.name}'
        if self.prev_dry_run:
            logger.info('%s was a dry_run', prefix)
        if self.start_time:
            logger.info('%s started at %s', prefix, self.start_time)
        if self.completed:
            if self.end_time:
                logger.info('%s completed at %s', prefix, self.end_time)
            else:
                msg = 'is marked completed but did not record an end time'
                logger.warning('%s started at %s and %s', prefix, self.start_time, msg)
        if self.errors:
            logger.warning('%s encountered errors.', prefix)
            if self.logs:
                # Only report the log if a error is True
                logger.warning('%s had log(s): %s', prefix, self.logs)

    def begin(self) -> None:
        """Begin the job and record the current status"""
        logger.info('Beginning job: %s', self.name)
        self.launch_prep()
        self.start_time = now_iso8601()
        self.completed = False
        self.record()

    def end(
        self,
        completed: bool = False,
        errors: bool = False,
        logmsg: t.Union[str, None] = None,
    ) -> None:
        """End the job and record the current status

        :param completed: Did the job complete successfully?
        :param errors: Were errors encountered doing the job?
        :param logs: Logs recorded doing the job (only if errors)
        """
        if self.dry_run:
            msg = (
                f'DRY-RUN: Not recording snapshots that can be deleted: {self.cleanup}'
            )
            logger.info(msg)
            self.add_log(msg)
        self.end_time = now_iso8601()
        self.completed = completed
        self.errors = errors
        if logmsg:
            self.add_log(logmsg)
        self.record()
        logger.info('Job: %s ended. Completed: %s', self.name, completed)

    def record(self) -> None:
        """Record the current status of the job

        :rtype: None
        :returns: No return value
        """
        doc = self.build_doc()
        try:
            update_doc(self.client, self.index, self.name, doc)
        except Exception as exc:
            logger.critical(exc.args[0])  # First arg is always message
            raise FatalError('Unable to update document', exc) from exc

    def finished(self) -> bool:
        """Check if a prior run was recorded for this job and log accordingly

        :returns: The boolean state of whether a prior run failed to complete
        """
        if self.completed:
            if self.dry_run:
                logger.info(
                    'DRY-RUN: Ignoring previous successful run of job: %s', self.name
                )
            else:
                logger.info('Job %s was completed previously.', self.name)
                return True
        if self.start_time:
            self.report_history()
            logger.info('Restarting or resuming job: %s', self.name)
        return False
