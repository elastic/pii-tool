"""Hot tier tests. No ILM. No searchable snapshots."""

from . import TestDefault


class TestDataStream(TestDefault):
    """TestDataStream"""

    scenario = 'hot_ds'


class TestIndices(TestDefault):
    """TestIndices"""

    scenario = 'hot'


class TestRolloverIndices(TestDefault):
    """TestRolloverIndices"""

    scenario = 'hot_rollover'
