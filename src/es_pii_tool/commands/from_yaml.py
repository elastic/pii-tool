"""Click decorated function for Redacting from YAML file"""

import logging
import click
from es_client.helpers.config import cli_opts, get_client
from es_client.helpers.utils import option_wrapper
from es_pii_tool.defaults import CLICK_DRYRUN, CLICK_TRACKING
from es_pii_tool.exceptions import FatalError
from es_pii_tool.base import PiiTool

logger = logging.getLogger(__name__)

click_opt_wrap = option_wrapper()  # Needed or pylint blows a fuse


@click.command()
@click_opt_wrap(*cli_opts('dry-run', settings=CLICK_DRYRUN))
@click_opt_wrap(*cli_opts('tracking-index', settings=CLICK_TRACKING))
@click.argument('redactions_file', type=click.Path(exists=True), nargs=1)
@click.pass_context
def file_based(ctx, dry_run, redactions_file, tracking_index):
    """Redact from YAML config file"""
    try:
        client = get_client(configdict=ctx.obj['configdict'])
    except Exception as exc:
        logger.critical('Error attempting to get client connection: %s', exc.args[0])
        raise FatalError(
            'Unable to establish connection to Elasticsearch!', exc
        ) from exc
    try:
        main = PiiTool(
            client, tracking_index, redaction_file=redactions_file, dry_run=dry_run
        )
        main.run()
    except Exception as exc:
        logger.error('Exception: %s', exc)
        raise exc
