"""Integration Test Setup"""

import logging
import pytest
from elasticsearch8.exceptions import NotFoundError
from es_testbed import TestBed
from es_testbed.presets.searchable_test.definitions import get_plan
from es_pii_tool.base import PiiTool
from es_pii_tool.exceptions import FatalError

logger = logging.getLogger(__name__)

# pylint: disable=R0903,R0913


def get_kind(scenario) -> str:
    """Return the searchable snapshot tier for the test based on plan"""
    return get_plan(scenario=scenario)['type']


def get_sstier(scenario) -> str:
    """Return the searchable snapshot tier for the test based on plan"""
    plan = get_plan(scenario=scenario)
    tiers = set()
    for scheme in plan['index_buildlist']:
        if 'target_tier' in scheme:
            if scheme['target_tier'] in ['cold', 'frozen']:
                tiers.add(scheme['target_tier'])
    if 'ilm' in plan:
        if 'phases' in plan['ilm']:
            for phase in ['cold', 'frozen']:
                if phase in plan['ilm']['phases']:
                    tiers.add(phase)
    if len(tiers) > 1:
        raise ValueError('Both cold and frozen tiers specified for this scenario!')
    if tiers:
        retval = list(tiers)[0]  # There can be only one...
    else:
        retval = 'hot'
    return retval


class TestBuiltin:
    """
    Test Built-in scenarios for index or data_stream

    Set this up by setting class variables, as below.
    """

    scenario = ''
    builtin = ''

    @pytest.fixture(scope="class")
    def tb(self, client, prefix, uniq, skip_no_repo, tracker):
        """TestBed setup/teardown"""
        skip_no_repo(get_sstier(self.scenario) in ['cold', 'frozen'])
        teebee = TestBed(client, builtin=self.builtin, scenario=self.scenario)
        teebee.settings['prefix'] = prefix
        teebee.settings['uniq'] = uniq
        teebee.setup()
        yield teebee
        teebee.teardown()
        try:
            client.indices.delete(index=tracker)
        except NotFoundError:
            pass  # We just need it deleted if it exists.


class TestDefault(TestBuiltin):
    """Test TestBed scenarios for the 'searchable_test' builtin"""

    builtin = 'searchable_test'

    # def test_first_pass_pattern(self, first_pass, tierpattern):
    #     """Check that our first_pass search pattern is as expected"""
    #     actual = first_pass['first_pass']['pattern']
    #     expected = tierpattern
    #     logger.debug('first_pass pattern = %s', actual)
    #     logger.debug('first_pass expected = %s', expected)
    #     assert actual == expected

    # def test_second_pass_pattern(self, second_pass, tierpattern):
    #     """Check that our second_pass search pattern is as expected"""
    #     actual = second_pass['second_pass']['pattern']
    #     expected = tierpattern
    #     logger.debug('second_pass pattern = %s', actual)
    #     logger.debug('second_pass expected = %s', expected)
    #     assert actual == expected

    # def test_third_pass_pattern(self, third_pass, wildcard):
    #     """Check that our third_pass search pattern is as expected"""
    #     actual = third_pass['third_pass']['pattern']
    #     expected = wildcard
    #     logger.debug('third_pass pattern = %s', actual)
    #     logger.debug('third_pass expected = %s', expected)
    #     assert actual == expected

    # def test_final_pass_pattern(self, final_pass, wildcard):
    #     """Check that our final_pass search pattern is as expected"""
    #     actual = final_pass['final_pass']['pattern']
    #     expected = wildcard
    #     logger.debug('final_pass pattern = %s', actual)
    #     logger.debug('final_pass expected = %s', expected)
    #     assert actual == expected

    def test_redactions(self, client, tracker, redactions):
        """Actually run the redaction scenario"""
        pii = PiiTool(client, tracker, redaction_dict=redactions)
        try:
            pii.run()
        except FatalError as exc:
            logger.error('FatalException in testing: %s', exc)
            assert False
        assert True

    def test_first_pass_results(self, client, tracker):
        """Evaluate the success of the first_pass job"""
        doc = client.get(index=tracker, id='first_pass')
        logger.debug('first_pass job results: %s', doc)
        assert doc['_source']['completed']
        assert not doc['_source']['errors']

    def test_second_pass_results(self, client, tracker):
        """Evaluate the success of the second_pass job"""
        doc = client.get(index=tracker, id='second_pass')
        logger.debug('second_pass job results: %s', doc)
        assert doc['_source']['completed']
        assert not doc['_source']['errors']

    def test_third_pass_results(self, client, tracker):
        """Evaluate the success of the third_pass job"""
        doc = client.get(index=tracker, id='third_pass')
        logger.debug('third_pass job results: %s', doc)
        assert doc['_source']['completed']
        assert not doc['_source']['errors']

    def test_final_pass_results(self, client, tracker):
        """Evaluate the success of the final_pass job"""
        doc = client.get(index=tracker, id='final_pass')
        logger.debug('final_pass job results: %s', doc)
        assert doc['_source']['completed']
        assert not doc['_source']['errors']
