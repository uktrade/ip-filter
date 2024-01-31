#!/usr/bin/env bash

# Exit early if something goes wrong
set -e

# Add commands below to run as part of the build phase
buildCommand="/work/cli build"

if [ "${CODEBUILD_WEBHOOK_HEAD_REF}" == "refs/heads/master" ]; then
    buildCommand="${buildCommand} --publish --send-notifications"
fi

echo "Running build command: ${buildCommand}"
$buildCommand
