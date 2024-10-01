"""Frozen tier tests. Mixed ILM"""

from . import TestDefault


class TestDataStream(TestDefault):
    """TestDataStream"""

    scenario = 'frozen_ds'


class TestIndices(TestDefault):
    """TestIndices"""

    scenario = 'frozen'


class TestRolloverIndices(TestDefault):
    """TestRolloverIndices"""

    scenario = 'frozen_rollover'


class TestRolloverIndicesILM(TestDefault):
    """TestRolloverIndicesILM"""

    scenario = 'frozen_ilm'
