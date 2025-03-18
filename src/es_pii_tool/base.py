"""Main app definition"""

# pylint: disable=broad-exception-caught,R0913
import typing as t
import logging
from es_pii_tool.exceptions import FatalError, MissingIndex
from es_pii_tool.job import Job
from es_pii_tool.redacters.index import RedactIndex
from es_pii_tool.trackables import Task
from es_pii_tool.helpers.elastic_api import get_hits
from es_pii_tool.helpers.utils import end_it, get_redactions

if t.TYPE_CHECKING:
    from elasticsearch8 import Elasticsearch

# pylint: disable=R0917

logger = logging.getLogger(__name__)


class PiiTool:
    """Elasticsearch PII Tool"""

    def __init__(
        self,
        client: 'Elasticsearch',
        tracking_index: str,
        redaction_file: str = '',
        redaction_dict: t.Union[t.Dict, None] = None,
        dry_run: bool = False,
    ):
        if redaction_dict is None:
            redaction_dict = {}
        logger.debug('Redactions file: %s', redaction_file)
        self.counter = 0
        self.client = client
        try:
            self.redactions = get_redactions(redaction_file, redaction_dict)
        except Exception as err:
            logger.critical('Unable to load redactions: %s', err)
            raise err
        self.tracking_index = tracking_index
        self.dry_run = dry_run

    def verify_doc_count(self, job: Job) -> bool:
        """Verify that expected_docs and the hits from the query have the same value

        :param job: The job object for the present redaction run

        :type job: :py:class:`~.app.tracking.Job`

        :rtype: None
        :returns: No return value
        """
        try:
            task = Task(job, task_id=f'PRE---{job.name}---DOC-COUNT-VERIFICATION')
        except Exception as err:
            logger.critical('Unable to create task: %s', err)
            raise FatalError('Unable to create task', err) from err
        success = False
        errors = False
        if task.finished():
            return True  # We're done already
        # Log task start
        task.begin()
        hits = 0
        try:
            hits = get_hits(self.client, job.config['pattern'], job.config['query'])
        except Exception as err:
            logger.critical('Unable to count query result hits: %s', err)
            raise err
        msg = f'{hits} hit(s)'
        logger.debug(msg)
        task.add_log(msg)
        logger.info("Checking expected document count...")
        zeromsg = (
            f"For index pattern {job.config['pattern']}, with query "
            f"{job.config['query']} 'expected_docs' is {job.config['expected_docs']} "
            f"but query results is {hits} matches."
        )
        if job.config['expected_docs'] == hits:
            msg = (
                f'Query result hits: {hits} matches expected_docs: '
                f'{job.config["expected_docs"]}'
            )
            logger.debug(msg)
            task.add_log(msg)
            success = True
            if hits == 0:
                logger.critical(zeromsg)
                logger.info('Continuing to next configuration block (if any)')
                success = False
        else:
            logger.critical(zeromsg)
            logger.info('Continuing to next configuration block (if any)')
        if not success:
            errors = True
            task.add_log(zeromsg)
        task.end(success, errors=errors)
        return success

    def iterate_indices(self, job: Job) -> bool:
        """Iterate over every index in job.indices"""
        all_succeeded = True
        for idx in job.indices:
            try:
                task = Task(job, index=idx, id_suffix='PARENT-TASK')
                # First check to see if idx has been touched as part of a previous run
                if task.finished():
                    continue  # This index has already been verified
                task.begin()
            except Exception as err:
                logger.critical('Unable to create task: %s', err)
                raise FatalError('Unable to create task', err) from err
            task_success = False
            try:
                msg = f'Iterating per index: Index {idx} of {job.indices}'
                logger.debug(msg)
                task.add_log(msg)
                redact = RedactIndex(idx, job, self.counter)
                redact.run()
                task_success = redact.success
                self.counter = redact.counter
                logger.debug('RESULT: %s', task_success)
            except MissingIndex as err:
                logger.critical(err)
                raise FatalError(f'Index {err.missing} not found.', err) from err
            except FatalError as err:
                logger.critical('Fatal upstream error encountered: %s', err.message)
                raise FatalError('We suffered a fatal upstream error', err) from err
            end_it(task, task_success)
            if not task.completed:
                all_succeeded = False
                job.add_log(f'Unable to complete task {task.task_id}')
        return all_succeeded

    def iterate_configuration(self) -> None:
        """Iterate over every configuration block in self.redactions"""
        logger.debug('Full redactions object from config: %s', self.redactions)
        for config_block in self.redactions['redactions']:  # type: ignore
            job_success = True
            # Reset counter to zero for each full iteration
            self.counter = 0
            if self.dry_run:
                logger.info("DRY-RUN MODE ENABLED. No data will be changed.")

            # There's really only 1 root-level key for each configuration block,
            # and that's job_id
            job_name = list(config_block.keys())[0]
            args = (self.client, self.tracking_index, job_name, config_block[job_name])
            job = Job(*args, dry_run=self.dry_run)
            if job.finished():
                continue
            job.begin()
            if not self.verify_doc_count(job):
                # This configuration block can't go further because of the mismatch
                job_success = False
                end_it(job, job_success)
                continue

            job_success = self.iterate_indices(job)
            # At this point, self.counter should be equal to total, indicating that we
            # matched expected_docs. We should therefore register that the job was
            # successful, if we have reached this point with no other errors having
            # interrupted the process.

            end_it(job, job_success)

    def run(self) -> None:
        """Do the thing"""
        logger.info('PII scrub initiated')
        self.iterate_configuration()
