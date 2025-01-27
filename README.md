# Elasticsearch PII Tool

Did you find PII (Personally Identifiable Information) in your Elasticsearch
indices that doesn't belong there? This is the tool for you!

`es-pii-tool` can help you redact information from even Searchable Snapshot
mounted indices. It works with deeply nested fields, too!


## Client Configuration

The tool connects using the [`es_client` Python module](https://es-client.readthedocs.io/).

You can use command-line options, or a YAML configuration file to configure the client connection.
If using a configuration file is desired, the configuration file structure requires
`elasticsearch` at the root level as follows:

```yaml
---
elasticsearch:
  client:
    hosts: https://10.11.12.13:9200
    cloud_id:
    request_timeout: 60
    verify_certs:
    ca_certs:
    client_cert:
    client_key:
  other_settings:
    username:
    password:
    api_key:
      id:
      api_key:
      token:

logging:
  loglevel: INFO
  logfile: /path/to/file.log
  logformat: default
  blacklist: []
```


## `REDACTIONS_FILE` Configuration

```
  ---
  redactions:
    - job_name_20240930_redact_hot:
        pattern: hot-*
        query: {'match': {'message': 'message1'}}
        fields: ['message']
        message: REDACTED
        expected_docs: 1
        restore_settings: {'index.routing.allocation.include._tier_preference': 'data_warm,data_hot,data_content'}
    - job_name_20240930_redact_cold:
        pattern: restored-cold-*
        query: {'match': {'nested.key': 'nested19'}}
        fields: ['nested.key']
        message: REDACTED
        expected_docs: 1
        restore_settings: {'index.routing.allocation.include._tier_preference': 'data_warm,data_hot,data_content'}
        forcemerge:
          max_num_segments: 1
    - job_name_20240930_redact_frozen:
        pattern: partial-frozen-*
        query: {'range': {'number': {'gte': 8, 'lte': 11}}}
        fields: ['deep.l1.l2.l3']
        message: REDACTED
        expected_docs: 4
        forcemerge:
          only_expunge_deletes: True
```

### Job Name

The job name _must_ be unique. Progress throughout a redaction job is tracked in 
a tracking index, the default name being `redactions-tracker`. If job progress is
interrupted for any reason, `es-pii-tool` will attempt to resume where it left off.

### `pattern`

The `pattern` setting defines which indices will be searched for documents to be
redacted. This can be [anything that Elasticsearch
supports](https://www.elastic.co/guide/en/elasticsearch/reference/current/search-multiple-indices.html),
including wildcard globbing and comma separated values.

### `query`

The Elasticsearch query used to isolate the documents to redact. This should be
in the [Elasticsearch Query
DSL](https://www.elastic.co/guide/en/elasticsearch/reference/current/query-dsl.html)
format.

### `fields`

The fields to be redacted. If the fields are not at the root-level of the document,
deeply nested objects can be referred to using dotted notation.

This is an array value, so multiple lines can be specified.

**NOTE:** All documents to be redacted must have all of the fields defined in the
`fields` array or an error condition will result.

### `message`

This value will replace whatever was in each of the `fields` specified. The default
value is `REDACTED`. It can be any string value. Partial or sub-string replacements
are not supported at this time.

### `expected_docs`

This must match the exact number of hits that will result from executing `query`.

**NOTE:** This value cannot exceed 10,000. This is the default limit that
Elasticsearch imposes for the maximum number of results from a single query. In
the event that you have a single query that produces more than 10,000 hits, and
all need to be redacted, it is suggested that you find some other limiting
condition or filter to reduce the total hits below 10,000.

### `restore_settings`

When redacting data from indices in the `cold` or `frozen` tier, it is required
to fully restore them from the snapshot repository first. If you wish to apply
any specific settings to these indices to be restored, this is where to set them.

The primary use case for `restore_settings` is to force the indices to restore
to the `data_warm` tier, or some other targeted data node, but it can be used
to apply any other desired setting.

### `forcemerge`

This is only used for redacting searchable snapshot indices in the `cold` or
`frozen` tiers. Best practices is to force merge your indices to 1 segment per
shard before taking a snapshot and mounting in the `cold` or `frozen` tier. This
tool defaults to performing this force merge after redacting your data. But you
can set the force merge to `only_expunge_deletes`.

In Elasticsearch, a document, once indexed, is immutable. In order to redact a
document, Elasticsearch effectively deletes the original document and reindexes
the modified document in its place.

#### `max_num_segments`

The default value for this setting is `1`, meaning 1 segment per shard. This is
a rather "expensive" operation in terms of disk I/O, and if you are only removing
a small number of documents from an index or shard, it may not make much sense
to perform a full force merge. To avoid this, use the `only_expunge_deletes`
setting.

#### `only_expunge_deletes`

This is a boolean setting, either `true` or `false` (or `True` or `False`).

If `true`, the force merge will only delete the bits marked for deletion,
which are the original documents that were deleted as part of the redaction
process.

## Running `es_pii_tool`

### Command Line Execution

The script is run by executing `pii-tool` at the command-line. There are multiple
levels of configuration for `pii-tool`. The top level is for client connection to
Elasticsearch configuration.

Subsequent commands include `show-all-options` and `file-based` (meaning
redaction configuration derived from a YAML configuration file).

#### Client configuration 


```
pii-tool --help
Usage: pii-tool [OPTIONS] COMMAND [ARGS]...

  Elastic PII Tool

Options:
  --config PATH                   Path to configuration file.
  --hosts TEXT                    Elasticsearch URL to connect to.
  --cloud_id TEXT                 Elastic Cloud instance id
  --api_token TEXT                The base64 encoded API Key token
  --id TEXT                       API Key "id" value
  --api_key TEXT                  API Key "api_key" value
  --username TEXT                 Elasticsearch username
  --password TEXT                 Elasticsearch password
  --request_timeout FLOAT         Request timeout in seconds
  --verify_certs / --no-verify_certs
                                  Verify SSL/TLS certificate(s)
  --ca_certs TEXT                 Path to CA certificate file or directory
  --client_cert TEXT              Path to client certificate file
  --client_key TEXT               Path to client key file
  --loglevel [DEBUG|INFO|WARNING|ERROR|CRITICAL]
                                  Log level
  --logfile TEXT                  Log file
  --logformat [default|json|ecs]  Log output format
  -v, --version                   Show the version and exit.
  -h, --help                      Show this message and exit.

Commands:
  file-based        Redact from YAML config file
  show-all-options  Show all client configuration options
```

#### `show-all-options`

As the list of configuration options for just the client connection are quite
long, the default `--help` output is somewhat truncated. You can show all of the
configuration options, as well as reveal their environment variable names by
running: `pii-tool show-all-options`:

```
Usage: pii-tool show-all-options [OPTIONS]

  ALL OPTIONS SHOWN

  The full list of options available for configuring a connection at the command-line.

Options:
  --config PATH                   Path to configuration file.  [env var: ESCLIENT_CONFIG]
  --hosts TEXT                    Elasticsearch URL to connect to.  [env var: ESCLIENT_HOSTS]
  --cloud_id TEXT                 Elastic Cloud instance id  [env var: ESCLIENT_CLOUD_ID]
  --api_token TEXT                The base64 encoded API Key token  [env var: ESCLIENT_API_TOKEN]
  --id TEXT                       API Key "id" value  [env var: ESCLIENT_ID]
  --api_key TEXT                  API Key "api_key" value  [env var: ESCLIENT_API_KEY]
  --username TEXT                 Elasticsearch username  [env var: ESCLIENT_USERNAME]
  --password TEXT                 Elasticsearch password  [env var: ESCLIENT_PASSWORD]
  --bearer_auth TEXT              Bearer authentication token  [env var: ESCLIENT_BEARER_AUTH]
  --opaque_id TEXT                X-Opaque-Id HTTP header value  [env var: ESCLIENT_OPAQUE_ID]
  --request_timeout FLOAT         Request timeout in seconds  [env var: ESCLIENT_REQUEST_TIMEOUT]
  --http_compress / --no-http_compress
                                  Enable HTTP compression  [env var: ESCLIENT_HTTP_COMPRESS]
  --verify_certs / --no-verify_certs
                                  Verify SSL/TLS certificate(s)  [env var: ESCLIENT_VERIFY_CERTS]
  --ca_certs TEXT                 Path to CA certificate file or directory  [env var: ESCLIENT_CA_CERTS]
  --client_cert TEXT              Path to client certificate file  [env var: ESCLIENT_CLIENT_CERT]
  --client_key TEXT               Path to client key file  [env var: ESCLIENT_CLIENT_KEY]
  --ssl_assert_hostname TEXT      Hostname or IP address to verify on the node's certificate.  [env var:
                                  ESCLIENT_SSL_ASSERT_HOSTNAME]
  --ssl_assert_fingerprint TEXT   SHA-256 fingerprint of the node's certificate. If this value is given then root-of-trust
                                  verification isn't done and only the node's certificate fingerprint is verified.  [env var:
                                  ESCLIENT_SSL_ASSERT_FINGERPRINT]
  --ssl_version TEXT              Minimum acceptable TLS/SSL version  [env var: ESCLIENT_SSL_VERSION]
  --master-only / --no-master-only
                                  Only run if the single host provided is the elected master  [env var: ESCLIENT_MASTER_ONLY]
  --skip_version_test / --no-skip_version_test
                                  Elasticsearch version compatibility check  [env var: ESCLIENT_SKIP_VERSION_TEST]
  --loglevel [DEBUG|INFO|WARNING|ERROR|CRITICAL]
                                  Log level  [env var: ESCLIENT_LOGLEVEL]
  --logfile TEXT                  Log file  [env var: ESCLIENT_LOGFILE]
  --logformat [default|json|ecs]  Log output format  [env var: ESCLIENT_LOGFORMAT]
  --blacklist TEXT                Named entities will not be logged  [env var: ESCLIENT_BLACKLIST]
  -h, --help                      Show this message and exit.
```

You will note that each of these has an `env var: ESCLIENT_` in this view. 

##### Environment Variables

If set as an environment variable, setting the associated command-line flag becomes
unnecessary.

For example, if I had a client configuration YAML file at `/path/to/config.yaml`,
and I set the `ESCLIENT_CONFIG` environment variable, e.g.

```
export ESCLIENT_CONFIG=/path/to/config.yaml
```

Then I could execute `pii-tool` without needing to use the
`--config /path/to/config.yaml` flag at the command line.

This is extremely useful with Docker-based execution!

Learn more in the [`es_client` documentation](https://es-client.readthedocs.io/en/latest/envvars.html).

#### `file-based`

The sub-command `file-based` is used *after* setting the client connection
parameters:

```
$ pii-tool file-based --help
Usage: run_script.py file-based [OPTIONS] REDACTIONS_FILE

  Redact from YAML config file

Options:
  --dry-run              Do not perform any changes.  [env var: PII_TOOL_DRY_RUN]
  --tracking-index TEXT  Name for the tracking index.  [env var: PII_TOOL_TRACKING_INDEX; default: redactions-tracker]
  -h, --help             Show this message and exit.
```

You will note that there are environment variables here, too!

### Docker Execution

The Docker image requires a volume map to `/.config` on the container (for now).

This is where any configuration files should go, including client YAML files and
the redactions file for use with the `file-based` command.

The command, presuming you have a file called `REDACTIONS_FILE.yaml` in `$(pwd)`:

```shell
docker run \
  --rm \  # Remove the container after execution
  -it \   # Not strictly necessary, but helpful as it is an interactive terminal
  -v $(pwd)/:/.config \  # Map the present-working directory to /.config
  untergeek/es-pii-tool:${TAG} \
    --hosts https://127.0.0.1:9200 \ 
    --username USERNAME \
    --password HIDDEN \
    file-based /.config/REDACTIONS_FILE.yaml
```

Any and all command-line flags are usable. But there's a better way...

#### Environment variables

As mentioned previously, environment variables can make things really easy for
Docker and Kubernetes:

```shell
docker run \
  --rm \  # Remove the container after execution
  -it \   # Not strictly necessary, but helpful as it is an interactive terminal
  -v $(pwd)/:/.config \  # Map the present-working directory to /.config
  -e ESCLIENT_HOSTS=${ESCLIENT_HOSTS} \ 
  -e ESCLIENT_USERNAME=${ESCLIENT_USERNAME} \ 
  -e ESCLIENT_PASSWORD=${ESCLIENT_PASSWORD} \ 
  untergeek/es-pii-tool:0.9.0 file-based /.config/REDACTIONS_FILE.yaml
```

## Testing

### The `docker_test` directory

Hopefully you won't ever have to modify anything here. In order to run the native
tests, you really *should* have Docker running locally, or at least accessible.

If not, you should plan on exporting the following environment variables:

* `TEST_ES_SERVER` Should be set to the full URL of the Elasticsearch cluster
* `TEST_USER` The Elasticsearch test username
* `TEST_PASS` The Elasticsearch password for `TEST_USER`
* `CA_CRT` Potentially optional, if your cluster is signed by a known signer
* `TEST_ES_REPO` The name of the repository on `TEST_ES_SERVER`

Each of these should be put in `.env` in the root directory of the project with
`export` in front, e.g.

```
export TEST_ES_SERVER=https://127.0.0.1:9200
...
```

Why in `.env`? Because `pytest` looks there for it.

Your test cluster would also need to have at least a Trial X-Pack license, as
searchable snapshots requires this.

Now, if this seems a bit daunting, it is. The `docker_test` scripts automate this
for you.

#### Manual Setup

```
docker_test/create.sh VERSION [SCENARIO]
```

`VERSION` needs to be a recognized image tag at
`docker.elastic.co/elasticsearch/elasticsearch`.

`SCENARIO` is technically optional, but is not optional for this project.

In order to test redacting from searchable snapshots from the `frozen` tier, you
need to have at least one `data_frozen` node. The scenario that does that is
`frozen_node`, e.g.:

```
$ docker_test/create.sh 8.15.1 frozen_node

Using scenario: frozen_node
Using Elasticsearch version 8.15.1

Creating 2 node(s)...

Starting node 1...
Container ID: ecf1e6d45bbc8d0617f75f47101372fd723a1424b27f7c250d1c7553f096960c
Getting Elasticsearch credentials from container es-pii-tool-test-0...
- 18s elapsed (typically 15s - 25s)...
Trial license started...
Snapshot repository initialized...

Node 1 started.
Node 2 started.

All nodes ready to test!

Environment variables are in .env
```

This script will create the first node, and collect the credentials created during
creation. It will then start an X-Pack trial license and initialize a snapshot
repository. Then in the same subnet, it will spin up another node, with
`node_role: ["data_frozen"]`. In effect, it creates exactly what testing the
`pii_tool` needs.

**NOTE:** It should take somewhere between 20 and 30 seconds to have both nodes
up and running. The script keeps a lovely spinner running while it's waiting and
trying to capture the credentials so you can see that it's working. It should be done
rather quickly after that.

#### Manual Teardown

```
$ docker_test/destroy.sh
Stopping all containers...
Removing all containers...
Deleting remaining files and directories
Cleanup complete.
```

This cleans up all containers, env var files, and the "repo" directory used as
a shared fs snapshot repository. No extra flags needed.

### Testing

Required steps to test:

* Fork and/or clone the repository
* `pip install -U '.[doc,test]'`

That last step will install all testing dependencies.

With `docker_test` so integral to testing `es-pii-tool`, an effort was made to make
the setup and teardown of the Docker containers automatic.

```
$ pytest --docker_create true --docker_destroy true --es_version 8.15.1
```

If `--docker_create true` and/or `--docker_destroy true` are omitted, the tests
will assume you already have a functional test environment and the environment
variables are configured in `.env`, and that the `.env` file has been `source`-ed.

The output looks like this:

```
$ pytest --docker_create true --docker_destroy true
Running: "/path/to/es-pii-tool/docker_test/create.sh 8.15.1 frozen_node"

Using scenario: frozen_node
Using Elasticsearch version 8.15.1

Creating 2 node(s)...

Starting node 1...
Container ID: f4e006bc94cdc8fa0977d9ab5bae509250f6d5fd2e0b8de6a3bbc994c2ee684a
Getting Elasticsearch credentials from container es-pii-tool-test-0...
- 18s elapsed (typically 15s - 25s)...
Trial license started...
Snapshot repository initialized...

Node 1 started.
Node 2 started.

All nodes ready to test!

Environment variables are in .env[-shell]
===================================================== test session starts =====================================================
platform darwin -- Python 3.12.2, pytest-8.1.1, pluggy-1.5.0
rootdir: /path/to/es-pii-tool
configfile: pytest.ini
plugins: cov-5.0.0, anyio-4.3.0, returns-0.22.0
collected 70 items

tests/integration/test_cold.py .........................                                                                [ 35%]
tests/integration/test_frozen.py .........................                                                              [ 71%]
tests/integration/test_hot.py ....................                                                                      [100%]

 -- docker_test environment destroyed.


=============================================== 70 passed in 148.31s (0:02:28) ================================================
```

#### Errors during testing

While uncommon, occasionally a test will hang. While this could happen for a number
of reasons, the common one is that Docker could not access a resource properly and
a process timed out. 

If this happens, chances are good that it will look like other tests failed. This
is because the primary test scenario runs 4 passes across a series of 3 indices.
Depending on the circumstances, 2 of those indices might be frozen, cold, part of
a data_stream, etc.

Don't panic if a test times out. You *will* need to manually destroy the Docker
environment using `docker_test/destroy.sh` as the testing environment defaults to
*not* destroying the test environment so you can inspect it, if necessary.

After doing so, re-run the test. If it repeatedly fails, then something else may
be going on. This is also why being able to inspect the *not* automatically
destroyed Elasticsearch environment in Docker is a good thing.
