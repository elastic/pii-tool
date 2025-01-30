"""Steps to redact a snapshot mounted index"""

import typing as t
import logging
from dotmap import DotMap  # type: ignore
from es_pii_tool.trackables import Task
from es_pii_tool.helpers import steps as s

logger = logging.getLogger(__name__)

# pylint: disable=W0718


class RedactionSteps:
    """All of the redaction steps for the final flow"""

    def __init__(self, task: Task, var: DotMap):
        self.task = task
        self.var = var  # These are the variables from RedactSnapshot
        self.counter = 1  # Counter will track the step number for us
        self.steps: t.Sequence = []  # Steps to execute will be ordered here
        self.data = DotMap()

    def prep_steps(self):
        """Execute the preparatory steps for all indices"""
        # PREPARATORY STEPS
        # All indices will do these steps
        prepsteps = [
            s.resolve_index,  # Resolve whether an index or data_stream
            s.get_index_lifecycle_data,  # Get INDEX lifecycle from settings, if any
            s.get_ilm_explain_data,  # Get ILM explain data, if any
            s.get_ilm_lifecycle_data,  # Get ILM lifecycle data, if any
            s.clone_ilm_policy,  # If an ILM policy exists for index, clone it.
        ]

        for func in prepsteps:
            stepname = f'step{str(self.counter).zfill(2)}_{func.__name__}'
            logger.debug('Attempting %s', stepname)
            try:
                func(self.task, stepname, self.var, data=self.data)
            except Exception as exc:
                logger.error('Failed to execute %s: %s', stepname, exc)
                raise exc
            self.counter += 1

    def first_steps(self):
        """
        Append the first steps to :py:attr:`steps`
        """
        self.steps = [
            s.pre_delete,  # Force delete var.redaction_target, just in case
            s.restore_index,  # Restore to var.redaction_target
            s.un_ilm_the_restored_index,  # Remove ILM from var.redaction_target
            s.redact_from_index,  # Redact specified docs from var.redaction_target
            s.forcemerge_index,  # Force merge, if configured to do so
            s.clear_cache,  # Clear the index cache for var.redaction_target
            s.confirm_redaction,  # Confirm the docs were redacted
            s.snapshot_index,  # Snapshot var.redaction_target
            s.mount_snapshot,  # Mount the snapshotted index as var.mount_name
        ]

    def ilm_steps(self):
        """
        Append ILM specific steps only if there is a new ILM lifecycle name
        """
        # After the prep steps, this value should be known
        if bool(self.data.new.ilmname):
            self.steps.append(s.apply_ilm_policy)  # Apply the cloned ILM policy
            self.steps.append(s.confirm_ilm_phase)
            # Confirm we're in the expected phase and steps are "completed"

    def delete_original(self):
        """
        Append steps to delete the original index
        """
        self.steps.append(s.un_ilm_the_original_index)  # Remove ILM as a precaution
        self.steps.append(s.close_old_index)  # Close it - Also precautionary
        self.steps.append(s.delete_old_index)  # Delete it

    def get_steps(self):
        """
        Meta-step to populate :py:attr:`steps`
        """
        # Do preparatory steps on all indices
        self.prep_steps()

        # Set the first steps in self.steps
        self.first_steps()

        # Add configuration dependent steps
        self.ilm_steps()

        self.steps.append(s.delete_redaction_target)  # Delete var.redaction_target

        # After the prep steps, these values should be known
        is_data_stream = bool(self.data.data_stream)

        # Only if original index was not a data_stream
        if not is_data_stream:
            self.steps.append(s.fix_aliases)  # Collect and fix aliases to apply

        # Remove original index
        self.delete_original()

        # Reassociate as needed
        if is_data_stream:
            self.steps.append(s.reassociate_index_with_ds)  # Reassociate with ds
        else:
            self.steps.append(s.assign_aliases)  # Reassociate with aliases

        # Final step
        self.steps.append(s.record_it)

    def run(self) -> None:
        """
        Run the steps in sequence

        Step numbers are calculated by :py:attr:`counter`, which makes it easier
        to number steps if they are changed or reordered.
        """
        self.get_steps()

        # Now we finish the steps
        for func in self.steps:
            stepname = f'step{str(self.counter).zfill(2)}_{func.__name__}'
            logger.debug('Attempting %s', stepname)
            func(self.task, stepname, self.var, data=self.data)
            self.counter += 1
