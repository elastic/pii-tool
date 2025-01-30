"""Functions for creating & updating the progress/status update doc in Elasticsearch"""

import typing as t
import logging
from es_pii_tool.exceptions import FatalError, MissingArgument, MissingDocument
from es_pii_tool.helpers.elastic_api import get_progress_doc, update_doc
from es_pii_tool.helpers.utils import now_iso8601

if t.TYPE_CHECKING:
    from es_pii_tool.job import Job


MOD = __name__

# pylint: disable=R0902,W0707


class Trackable:
    """An individual task or, tracked in Elasticsearch"""

    ATTRLIST = ['start_time', 'completed', 'end_time', 'errors', 'logs']

    def __init__(
        self,
        job: t.Optional['Job'] = None,
        index: str = '',
    ):
        self.logger = logging.getLogger(f'{MOD}.{self.__class__.__name__}')
        self.stub = ''
        if job:
            self.job = job
            self.stub = f'Job: {job.name}'
        self.index = index
        self.task_id = ''
        self.stepname = ''
        self.doc_id = None

    @property
    def status(self) -> t.Dict:
        """
        The status of the current trackable, or retrieved from an previous
        incomplete trackable
        """
        return self._status

    @status.setter
    def status(self, value: t.Dict) -> None:
        self._status = value

    @property
    def start_time(self) -> str:
        """The ISO8601 string representing the start time of this trackable"""
        return self._start_time

    @start_time.setter
    def start_time(self, value: str) -> None:
        self._start_time = value

    @property
    def end_time(self) -> str:
        """The ISO8601 string representing the end time of this trackable"""
        return self._end_time

    @end_time.setter
    def end_time(self, value: str) -> None:
        self._end_time = value

    @property
    def completed(self) -> bool:
        """Is the trackable completed? or Did the trackable complete successfully?"""
        return self._completed

    @completed.setter
    def completed(self, value: bool) -> None:
        self._completed = value

    @property
    def errors(self) -> bool:
        """Were errors encountered during this trackable?"""
        return self._errors

    @errors.setter
    def errors(self, value: bool) -> None:
        self._errors = value

    @property
    def logs(self) -> t.Sequence[str]:
        """The list of log lines collected during this trackable"""
        return self._logs

    @logs.setter
    def logs(self, value: t.Sequence[str]) -> None:
        self._logs = value

    def add_log(self, value: str) -> None:
        """Append another entry to self.logs"""
        if not self.logs:
            _ = []
            _.append(f'{now_iso8601()} {value}')
        else:
            _ = self.logs
            _.append(f'{now_iso8601()} {value}')
        self.logs = _

    def load_status(self) -> None:
        """Load prior status values (or not)"""
        for key in self.ATTRLIST:
            if self.job.prev_dry_run:
                # If our last run was a dry run, set each other attribute to None
                setattr(self, key, None)
            else:
                if key in self.status:
                    setattr(self, key, self.status[key])
                else:
                    setattr(self, key, None)

    def get_trackable(self) -> t.Dict:
        """
        Get any history that may exist for self.stepname of self.task_id of
        self.job.name

        :returns: The step object from the progress/status update doc
        """
        retval = {}
        try:
            retval = get_progress_doc(
                self.job.client,
                self.job.index,
                self.job.name,
                self.task_id,
                stepname=self.stepname,
            )
        except MissingDocument:
            self.logger.debug('Doc tracking %s does not exist yet', self.stub)
            return retval
        except Exception as exc:
            msg = f'Fatal error encountered: {exc.args[0]}'
            self.logger.critical(msg)
            raise FatalError(msg, exc)
        self.doc_id = retval['_id']
        return retval['_source']

    def get_history(self) -> None:
        """
        Get the history of self.stepname, if any. Ensure all values are populated
        from the doc, or None
        """
        self.logger.debug('Pulling any history for %s', self.stub)
        self.status = self.get_trackable()
        if not self.status:
            self.logger.debug('No history for %s', self.stub)
        self.load_status()

    def report_history(self) -> None:
        """
        Get the history of any prior attempt to run self.task_id of self.job.name
        Log aspects of the history here.
        """
        prefix = f'The prior run of {self.stub}'
        if self.start_time:
            self.logger.info('%s started at %s', prefix, self.start_time)
        if self.completed:
            if self.end_time:
                self.logger.info('%s completed at %s', prefix, self.end_time)
            else:
                msg = 'is marked completed but did not record an end time'
                self.logger.warning(
                    '%s started at %s and %s', prefix, self.start_time, msg
                )
        if self.errors:
            self.logger.warning('%s encountered errors.', prefix)
            if self.logs:
                # Only report the log if a error is True
                self.logger.warning('%s had log(s): %s', prefix, self.logs)

    def begin(self) -> None:
        """Begin the step and record the current status"""
        self.logger.info('Beginning %s', self.stub)
        if self.job.dry_run:
            msg = 'DRY-RUN: No changes will be made'
            self.logger.info(msg)
            self.add_log(msg)
        self.start_time = now_iso8601()
        self.completed = False
        self.record()
        if not self.doc_id:
            self.get_trackable()
            self.load_status()
            self.logger.debug('self.doc_id = %s', self.doc_id)

    def end(
        self,
        completed: bool = False,
        errors: bool = False,
        logmsg: t.Union[str, None] = None,
    ) -> None:
        """End the step and record the current status

        :param completed: Did the step complete successfully?
        :param errors: Were errors encountered doing the step?
        :param logs: Logs recorded doing the step (only if errors)
        """
        self.end_time = now_iso8601()
        self.completed = completed
        self.errors = errors
        if logmsg:
            self.add_log(logmsg)
        self.record()
        self.logger.info('%s ended. Completed: %s', self.stub, completed)

    def update_status(self) -> None:
        """Update instance attribute doc with the current values"""
        # self.logger.debug('Current status: %s', self.status)
        contents = {}
        for val in self.ATTRLIST:
            if getattr(self, val) is not None:
                contents[val] = getattr(self, val)
        self.status = contents
        # self.logger.debug('Updated status: %s', self.status)

    def build_doc(self) -> t.Dict:
        """Build the dictionary which will be the written to the tracking doc

        :returns: The tracking doc dictionary
        """
        doc = {}
        self.update_status()
        for key in self.ATTRLIST:
            if key in self.status:
                doc[key] = self.status[key]
        # Only add this field if self.index is not empty/None
        if self.index:
            doc['index'] = self.index
        # Only add this field if self.stepname is not empty/None
        if self.stepname:
            doc['step'] = self.stepname
        # Only add this field if self.task_id not empty/None
        if self.task_id:
            doc['task'] = self.task_id  # Necessary for the parent-child relationship
        doc['job'] = self.job.name
        doc['dry_run'] = self.job.dry_run
        # self.logger.debug('Updated step doc: %s', doc)
        return doc

    def record(self) -> None:
        """Record the current status of the task"""
        doc = self.build_doc()
        try:
            update_doc(
                self.job.client, self.job.index, self.doc_id, doc  # type: ignore
            )
        except Exception as exc:
            msg = f'Fatal error encountered: {exc.args[0]}'
            self.logger.critical(msg)
            raise FatalError(msg, exc)

    def finished(self) -> bool:
        """
        Check if a prior run was recorded for this step and log accordingly

        :returns: State of whether a prior run failed to complete
        """
        if self.completed:
            if self.job.dry_run:
                self.logger.info('DRY-RUN: Ignoring previous run of %s', self.stub)
            else:
                self.logger.info('%s was completed previously.', self.stub)
                return True
        if self.start_time:
            self.report_history()
            self.logger.warning('%s was not completed in a previous run.', self.stub)
        return False


class Task(Trackable):
    """An individual task item, tracked in Elasticsearch"""

    def __init__(
        self,
        job: t.Optional['Job'] = None,
        index: str = '',
        id_suffix: str = '',
        task_id: str = '',
    ):
        super().__init__(job=job, index=index)
        self.logger = logging.getLogger(f'{MOD}.{self.__class__.__name__}')
        if job is None:
            raise MissingArgument('job', 'keyword argument', 'job')
        if task_id:
            self.task_id = task_id
        elif not index or not id_suffix:
            missing = ['task_id']
            if not index:
                missing.append('index')
            if not id_suffix:
                missing.append('id_suffix')
            raise MissingArgument(
                'task_id, or both index and id_suffix must be provided',
                'keyword argument(s)',
                missing,
            )
        else:
            self.task_id = f'{index}---{id_suffix}'
        self.index = index
        self.stub = f'Task: {self.task_id} of Job: {self.job.name}'
        self.doc_id = None
        self.get_history()


class Step(Trackable):
    """An individual step item, tracked in Elasticsearch"""

    def __init__(
        self,
        job: t.Optional['Job'] = None,
        task: t.Optional[Task] = None,
        index: str = '',
        stepname: str = '',
    ):
        super().__init__(job=job, index=index)
        self.logger = logging.getLogger(f'{MOD}.{self.__class__.__name__}')
        if task is None:
            raise MissingArgument('task', 'keyword argument', 'task')
        if not stepname:
            raise MissingArgument(
                'stepname must be provided',
                'keyword argument(s)',
                'stepname',
            )
        self.task_id = task.task_id
        self.job = task.job
        self.index = index
        self.stepname = stepname
        self.stub = f'Step: {stepname} of Task: {self.task_id} of Job: {task.job.name}'
        self.doc_id = None
        self.get_history()
