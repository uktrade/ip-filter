# Changelog

## [2.1.5](https://github.com/uktrade/ip-filter/compare/2.1.4...2.1.5) (2025-04-11)


### Bug Fixes

* Log formatting (DBTP-1603) ([#78](https://github.com/uktrade/ip-filter/issues/78)) ([ff7e2a7](https://github.com/uktrade/ip-filter/commit/ff7e2a719eb65a3baa4e4f8925103ca160facd52))

## [2.1.4](https://github.com/uktrade/ip-filter/compare/2.1.3...2.1.4) (2025-04-10)


### Dependencies

* Bump dbt-copilot-python from 0.2.2 to 0.2.3 ([#83](https://github.com/uktrade/ip-filter/issues/83)) ([babd423](https://github.com/uktrade/ip-filter/commit/babd42385520ceb060a0764bb479f9160aa2bc28))
* Bump ddtrace from 3.1.0 to 3.4.1 ([#82](https://github.com/uktrade/ip-filter/issues/82)) ([a0bdf44](https://github.com/uktrade/ip-filter/commit/a0bdf44d20422b4e6a0cdca36a5afdd839a10ddb))
* Bump flask from 2.3.2 to 3.1.0 ([#87](https://github.com/uktrade/ip-filter/issues/87)) ([c0e10c6](https://github.com/uktrade/ip-filter/commit/c0e10c61dabd5d326cf9104d6cb5c3851063999c))
* Bump sentry-sdk from 2.18.0 to 2.25.1 ([#80](https://github.com/uktrade/ip-filter/issues/80)) ([384998a](https://github.com/uktrade/ip-filter/commit/384998a12458503314b552d6273c2835f8ea2151))

## [2.1.3](https://github.com/uktrade/ip-filter/compare/2.1.2...2.1.3) (2025-04-07)


### Bug Fixes

* Changing ip filter address (DBTP-1602) ([#76](https://github.com/uktrade/ip-filter/issues/76)) ([bc6adb1](https://github.com/uktrade/ip-filter/commit/bc6adb1286f24108981cf3385081ebb1d7a6327b))
* Logs formatted ASIM (DBTP-1602) ([#75](https://github.com/uktrade/ip-filter/issues/75)) ([ef7e7af](https://github.com/uktrade/ip-filter/commit/ef7e7af9e16f12fe8f08193b63c08df50fbb214a))

## [2.1.2](https://github.com/uktrade/ip-filter/compare/2.1.2...2.1.1) (2025-03-26)


### Dependencies

* Bump gunicorn from 22.0.0 to 23.0.0 ([#71](https://github.com/uktrade/ip-filter/issues/71)) ([a39bb3e](https://github.com/uktrade/ip-filter/commit/a39bb3e1fa5a180eb0e02a1b0a089cc65399b366))

## [2.1.1](https://github.com/uktrade/ip-filter/compare/2.1.0...2.1.1) (2025-03-19)


### Bug Fixes

* IP-filter unable to build in CI (DBTP-1662) ([#69](https://github.com/uktrade/ip-filter/issues/69)) ([8298c10](https://github.com/uktrade/ip-filter/commit/8298c1060c272fbbf3277cd296376c7e38eb505c))

## [2.1.0](https://github.com/uktrade/ip-filter/compare/2.0.2...2.1.0) (2025-03-18)


### Features

* **logging:** Add fields to enable Datadog logs correlation (DBTP-1662) ([#66](https://github.com/uktrade/ip-filter/issues/66)) ([0ad4a52](https://github.com/uktrade/ip-filter/commit/0ad4a5200823ee847c960457b72fdfb938b7a2e3))


### Documentation

* Correct docs for IP Filter's Basic Auth functionality ([#64](https://github.com/uktrade/ip-filter/issues/64)) ([f4d9009](https://github.com/uktrade/ip-filter/commit/f4d9009d15a82d756f1f8120d8931121a2d7e972))

## [2.0.2](https://github.com/uktrade/ip-filter/compare/2.0.1...2.0.2) (2024-12-30)


### Bug Fixes

* avoid unnecessarily chunking upstream requests ([#56](https://github.com/uktrade/ip-filter/issues/56)) ([6b64c4a](https://github.com/uktrade/ip-filter/commit/6b64c4a6ad07814daad9e581cc36904d8a283b34))

## [2.0.1](https://github.com/uktrade/ip-filter/compare/2.0.0...2.0.1) (2024-10-28)


### Bug Fixes

* DPM-325 - allow networks in ADDITIONAL_IP_LIST ([#45](https://github.com/uktrade/ip-filter/issues/45)) ([073832d](https://github.com/uktrade/ip-filter/commit/073832d7d2c28ef93fa700622f9d40d4f1b22a74))

## [2.0.0](https://github.com/uktrade/ip-filter/compare/1.1.0...2.0.0) (2024-08-09)


### ⚠ BREAKING CHANGES

* The previous commit introduced a breaking change to the API behavior by changing the IP_DETERMINED_BY_X_FORWARDED_FOR_INDEX default from -1 to -2.

### Bug Fixes

* mark the previous change as breaking ([d0b46ce](https://github.com/uktrade/ip-filter/commit/d0b46cedf9266ee4d8f06b434c950368fff11585))

## [1.1.0](https://github.com/uktrade/ip-filter/compare/1.0.0...1.1.0) (2024-05-29)


### Features

* DBTP-1033 Versioning release process ([#34](https://github.com/uktrade/ip-filter/issues/34)) ([8a3f94c](https://github.com/uktrade/ip-filter/commit/8a3f94c7ce06d260d111eb91d4f7d8fceb958fe3))
