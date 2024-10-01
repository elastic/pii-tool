"""Cold tier tests. Mixed ILM"""

from . import TestDefault


class TestDataStream(TestDefault):
    """TestDataStream"""

    scenario = 'cold_ds'


class TestIndices(TestDefault):
    """TestIndices"""

    scenario = 'cold'


class TestRolloverIndices(TestDefault):
    """TestRolloverIndices"""

    scenario = 'cold_rollover'


class TestRolloverIndicesILM(TestDefault):
    """TestRolloverIndicesILM"""

    scenario = 'cold_ilm'
