"""Command-line interface"""

import click
from es_client.defaults import OPTION_DEFAULTS, SHOW_EVERYTHING
from es_client.helpers import config as cfg
from es_client.helpers.logging import configure_logging
from es_pii_tool.commands.from_yaml import file_based

# pylint: disable=W0613,W0622,R0913,R0914,R0917
# These pylint items are being disabled because of how Click works.


@click.group(context_settings=cfg.context_settings())
@cfg.options_from_dict(OPTION_DEFAULTS)
@click.version_option(None, "-v", "--version", prog_name="pii-tool")
@click.pass_context
def run(
    ctx,
    config,
    hosts,
    cloud_id,
    api_token,
    id,
    api_key,
    username,
    password,
    bearer_auth,
    opaque_id,
    request_timeout,
    http_compress,
    verify_certs,
    ca_certs,
    client_cert,
    client_key,
    ssl_assert_hostname,
    ssl_assert_fingerprint,
    ssl_version,
    master_only,
    skip_version_test,
    loglevel,
    logfile,
    logformat,
    blacklist,
):
    """Elastic PII Tool"""
    ctx.obj["default_config"] = None
    cfg.get_config(ctx, quiet=False)
    configure_logging(ctx)
    cfg.generate_configdict(ctx)


@run.command(
    context_settings=cfg.context_settings(),
    short_help="Show all client configuration options",
)
@cfg.options_from_dict(SHOW_EVERYTHING)
@click.pass_context
def show_all_options(
    ctx,
    config,
    hosts,
    cloud_id,
    api_token,
    id,
    api_key,
    username,
    password,
    bearer_auth,
    opaque_id,
    request_timeout,
    http_compress,
    verify_certs,
    ca_certs,
    client_cert,
    client_key,
    ssl_assert_hostname,
    ssl_assert_fingerprint,
    ssl_version,
    master_only,
    skip_version_test,
    loglevel,
    logfile,
    logformat,
    blacklist,
):
    """
    ALL OPTIONS SHOWN

    The full list of options available for configuring a connection at the command-line.
    """
    ctx = click.get_current_context()
    click.echo(ctx.get_help())
    ctx.exit()


run.add_command(file_based)
