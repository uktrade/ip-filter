# IP Filter

A configurable IP Filter for AWS Copilot/ECS that allows access to applications based on combinations of

- basic auth [for automated testing tools],
- IP address [based on a configurable index in the `x-forwarded-for` header],
- shared secret in an HTTP header [passed from a CDN]

The IP Filter is designed to run as a sidecar container that reverse proxies traffic to the service container. It also requires the AppConfig ECS agent sidecar which it uses to pull config updates from AppConfig.

The IP Filter requires the `$COPILOT_ENVIRONMENT_NAME` environment variable which is automatically set by AWS Copilot.  This allows environment level configuration, for example:

```
# These settings apply globally to all environments
IPFILTER_ENABLED=True
APPCONFIG_PROFILES=ipfilter:default:default
# These settings are applied when `$COPILOT_ENVIRONMENT_NAME=staging`
STAGING_IPFILTER_ENABLED=False
STAGING_APPCONFIG_PROFILES=ipfilter:default:default,ipfilter:default:basicauth
```

## Usage

See the DBT platform documentation for more information on usage.

## Configuration

### Environment variables

The following are settings that apply globally.

| Variable                 |  Description | Example |
| ---                      | ---          | ---     |
| `SERVER`| The origin host that all requests are routed to | `some-domain-under.cloud-foundry-router.test`
| `SERVER_PROTO` | The protocol used to communicate to the origin | `https`
| `EMAIL` | The email address shown to users on authorisation failure | `my.email@domain.test`
| `EMAIL_NAME` | The email address shown to users on authorisation failure | `DBT`
| `LOG_LEVEL` | The Python log level | `WARN`
| `PORT` | The port for the application to listen on | `8080`
| `IP_DETERMINED_BY_X_FORWARDED_FOR_INDEX` | The index of the client IP in the XFF header, defaults to -1 | -1
| `APPCONFIG_URL` | The URL of the local AppConfig agent | http://localhost:2772

The following settings can be applied globally and overridden on a per environment basis.

| Variable                 |  Description | Example |
| ---                      | ---          | ---     |
| `IPFILTER_ENABLED` | Is the IP Filter enabled? If disabled traffic will be proxied directly to the service | `True`
| `APPCONFIG_PROFILES` | A comma separated list of AppConfig profiles | `default:rule:set`
| `PUBLIC_PATHS` | A comma separated list of path prefixes that are not proteted by the IP Filter | `/healthcheck,/robots.txt`
| `PROTECTED_PATHS` | A comma separated list of path prefixes that are protected by the IP Filter | `/admin,/api`

These environment variables can be overridden for a given `$COPILOT_ENVIRONMENT_NAME`, for example:

```
IPFILTER_ENABLED=True
PRODUCTION_PROTECTED_PATHS=/admin/
```

In this example, the IP Filter is enabled for all environments, but in production only the `/admin/` path is protected.

### Usage of PUBLIC_PATHS and PROTECTED_PATHS

By default the IP Filter protects every path.  However, you can configure the IP Filter to allow public access to a subset of paths, or to only protect a subset of paths.

Any paths listed in `PUBLIC_PATHS` will be publically accessible.  The typical use case is to allow the `/healthcheck` endpoint to remain public.

Conversely, if `PROTECTED_PATHS` is set, then every path not listed in `PROTECTED_PATHS` will be public.  The typical usecase is to protect only the `/admin` path of a public site.

The `PUBLIC_PATHS` and `PROTECTED_PATHS` environment variables are mutally exclusive; if both are set, then a configuration warning will appear in the IP Filter logs and the `PROTECTED_PATHS` setting will be ignored.

You can unset these variables on a per environment basis, e.g. 

```
# By default, for all envrionments, the IP Filter is applied to every path except `/healthcheck`
PUBLIC_PATHS=/healthcheck

# The `/admin` url is protected by the IP Filter, and all other paths are public by default. 
PRODUCTION_PUBLIC_PATHS=    # unset for the production environment
PRODUCTION_PROTECTED_PATHS=/admin
```

### AppConfig configuration

AppConfig profiles specified in the `$APPCONFIG_PROFILES` environment variable are retrieved and combined into a single list of IPs, basic auth config and shared tokens.

```
IpRanges:
    - 1.1.1.1/32
    - 2.2.2.2/32
    - 3.3.3.3/32
```

Basic auth example

```
BasicAuth:
    - Path: /basic-auth-path/
      Username: myusername
      Password: mypassword
```

Basic auth enables automated testing tools such as Browserstack to bypass the IP whitelist. This is only on non production automated testing environments where it isn't possible to whitelist the IP range of the testing service.

If multiple basic auth configurations are provided, then the IP Filter ensures that at least the basic auth credentials supplied in the request authenticate against at least one of the configurations.

Shared token:

```
SharedToken:
    - HeaderName: x-my-shared-token
      Value: some-secure-value
```
A request will be blocked if it does not include a header with the name `x-my-shared-token` and the value `some-secure-value` in the request. The shared token header is then set by the CDN and is used to ensure the website only serves traffic to requests which originate via the CDN.

If multiple shared tokens are configured then the IP Filter checks that at least one shared token header and value is supplied.

### Minimal configuration

```
PORT: 8000
SERVER: localhost:8080
APPCONFIG_PROFILES: default:rule:set
```

## Contributing to ip-filter

### Getting started

1. Clone the repository:

   ```shell
   git clone https://github.com/uktrade/ip-filter.git && cd ip-filter
   ```

2. Install the required dependencies:

   ```shell
   pip install poetry && poetry install && poetry run pre-commit install
   ```

### Testing

#### Automated testing

Run `poetry run pytest` in the root directory to run all tests.

There is also a `tests.sh` script which will run all the tests with the standard unittest module
and also run the coverage report.

#### Coverage

Coverage information can be run locally with: `poetry run coverage run pytest`. 
This will run pytest using the python coverage module and create a bunch of .coverage files.

Combine the coverage files with `coverage combine` and then get a report with: `poetry run coverage report`.

Note: coverage doesn't currently see that end-to-end style tests actually cover anything (presumably because
the application spins up in a separate process). It would be worth us going forward trying to move a lot of the 
coverage the end-to-end tests provide into unit tests, as it would provide more useful coverage data as well as speeding
up the test suite.
