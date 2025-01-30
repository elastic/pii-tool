"""Redact data from a snapshot mounted index"""

import typing as t
import logging
from datetime import datetime
from dotmap import DotMap  # type: ignore
from es_pii_tool.exceptions import FatalError
from es_pii_tool.trackables import Task
from es_pii_tool.helpers import elastic_api as api
from es_pii_tool.helpers.utils import (
    get_inc_version,
    strip_index_name,
)
from es_pii_tool.redacters.steps import RedactionSteps

if t.TYPE_CHECKING:
    from elasticsearch8 import Elasticsearch
    from es_pii_tool.job import Job

logger = logging.getLogger(__name__)


class RedactSnapshot:
    """Redact PII from indices mounted as searchable snapshots"""

    def __init__(self, index: str, job: 'Job', phase: str):
        self.index = index
        self.phase = phase
        try:
            self.task = Task(job, index=index, id_suffix='REDACT-SNAPSHOT')
        except Exception as exc:
            logger.critical('Unable to create task: %s', exc)
            raise FatalError('Unable to create task', exc) from exc
        # self.var = self.ConfigAttrs(job.client, index, phase)
        self.var = DotMap()
        self._buildvar(job.client, index, phase)

    def _buildvar(self, client: 'Elasticsearch', index: str, phase: str):
        """Populate :py:attr:`var` with the values we need to start with"""
        self.var.client = client
        self.var.index = index
        self.var.phase = phase
        self._get_mapped_vars(phase)
        self.var.og_name = strip_index_name(index)  # Removes prefixes and suffixes
        now = datetime.now()
        self.var.redaction_target = (
            f'redacted-{now.strftime("%Y%m%d%H%M%S")}-{self.var.og_name}'
        )
        self.var.new_snap_name = f'{self.var.redaction_target}-snap'
        # Check if the old index has been redacted before and has a version number
        self.var.ver = get_inc_version(index)
        # The mount name contains a version at the end in case we need to redact
        # the index again. The version allows us to use a similar naming scheme
        # without redundancy
        self.var.mount_name = (
            f'{self.var.prefix}redacted-{self.var.og_name}---v{self.var.ver + 1:03}'
        )
        logger.debug('mount_name = %s', self.var.mount_name)

    def _get_mapped_vars(self, phase: str):
        self.var.prefix = ''
        self.var.storage = ''
        if phase == 'cold':
            self.var.prefix = 'restored-'
            self.var.storage = 'full_copy'
        elif phase == 'frozen':
            self.var.prefix = 'partial-'
            self.var.storage = 'shared_cache'

    def get_index_deets(self):
        """Return searchable snapshot values from deeply nested index settings"""
        response = api.get_index(self.var.client, self.var.index)
        logger.debug('Found indices: %s', list(response.keys()))
        self.var.aliases = DotMap(response[self.var.index]['aliases'])
        snap_data = response[self.var.index]['settings']['index']['store']['snapshot']
        self.var.repository = snap_data['repository_name']
        self.var.ss_snap = snap_data['snapshot_name']
        self.var.ss_idx = snap_data['index_name']
        logger.debug('ss_idx = %s', self.var.ss_idx)

    @property
    def success(self) -> bool:
        """
        :getter: Get the success state
        :setter: Set the success state
        :type: str
        """
        return self._success

    @success.setter
    def success(self, value: bool) -> None:
        self._success = value

    def run(self):
        """Do the actual run"""
        if self.task.finished():
            self.success = True
            return
        # Log task start time
        self.task.begin()
        logger.info("Getting index info: %s", self.index)
        self.var.restore_settings = DotMap(self.task.job.config['restore_settings'])
        # self.var.get_index_deets()
        self.get_index_deets()

        steps = RedactionSteps(self.task, self.var)
        steps.run()

        if not self.task.job.dry_run:
            msg = f'Index {self.index} has completed all steps.'
            logger.info(msg)
            self.task.add_log(msg)
            self.task.end(True, errors=False)
            self.success = True
            return
        # Implied else (meaning it is a dry run)
        _ = f'DRY-RUN || {self.task.logs}'
        self.success = False
        self.task.logs = _
