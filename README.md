# IP-Filter

A configurable IP Filter for AWS Copilot/ECS that allows access to applications based on combinations of

- basic auth [for automated testing tools],
- IP address [based on a configurable index in the `x-forwarded-for` header],
- shared secret in an HTTP header [passed from a CDN]

The IP Filter is designed to run as a sidecar container that reverse proxies traffic to the service container. It also requires the AppConfig ECS agent sidecar which it uses to pull config updates from AppConfig.

The IP filter requires the `$COPILOT_ENVIRONMENT_NAME` environment variable which is automatically set by AWS Copilot.  This allows environment level configuration, for example:

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
| `IPFILTER_ENABLED` | Is the IP filter enabled? If disabled traffic will be proxied directly to the service | `True`
| `APPCONFIG_PROFILES` | A comma separated list of AppConfig profiles | `default:rule:set`
| `PUBLIC_PATHS` | A comma separated list of path prefixes that are not proteted by the IP filter | `/healthcheck,/robots.txt`
| `PROTECTED_PATHS` | A comma separated list of path prefixes that are protected by the IP filter | `/admin,/api`

These environment variables can be overridden for a given `$COPILOT_ENVIRONMENT_NAME`, for example:

```
IPFILTER_ENABLED=True
PRODUCTION_PROTECTED_PATHS=/admin/
```

In this example, the IP filter is enabled for all environments, but in production only the `/admin/` path is protected.

### Usage of PUBLIC_PATHS and PROTECTED_PATHS

By default the IP filter protects every path.  However, any paths supplied in `PUBLIC_PATHS` will be publically accessible.  The typical use case is to allow place a site behind the IP filter, whilst allowing the `\healthcheck` endpoint to remain public.

Conversely, if `PROTECTED_PATHS` is set, then every path not listed in `PROTECTED_PATHS` will be public.  The typical usecase is to protect only the `\admin` path in a public site.

These environment variables are designed to be mutally exclusive.

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

If multiple basic auth configurations are provided, then the IP filter ensures that at lesaat one of the configurations is valid.

Shared token:

```
SharedToken:
    - HeaderName: x-my-shared-token
      Value: some-secure-value
```
A request will be blocked if it does not include a header with the name `x-my-shared-token` and the value `some-secure-value` in the request. The shared token header is then set by the CDN and is used to ensure the website only serves traffic to requests which originated via the CDN.

If multiple shared tokens are configured then the IP filter checks that at least one shared token header and value is supplied.

### Minimal configuration

```
PORT: 8000
SERVER: localhost:8080
APPCONFIG_PROFILES: default:rule:set
```

