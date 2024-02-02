#!/usr/bin/env bash

# Exit early if something goes wrong
set -e

# Add commands below to run as part of the install phase
echo "$(git symbolic-ref HEAD --short)"
buildCommand="/work/cli build"
if [ "$(git symbolic-ref HEAD --short)" == "DBTP-369-run-unit-tests-in-codebuild-pt3" ]; then
    buildCommand="${buildCommand} --publish --send-notifications"
fi

echo "Running build command: ${buildCommand}"


python --version
pip install poetry
poetry install --no-root
