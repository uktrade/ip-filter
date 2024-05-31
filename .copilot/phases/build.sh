#!/usr/bin/env bash

# Exit early if something goes wrong
set -e

# Add commands below to run as part of the build phase
CODEBUILD_GIT_BRANCH=`git branch -a --contains HEAD | sed -n 2p | awk '{ printf $1 }'`
CODEBUILD_GIT_BRANCH=${CODEBUILD_GIT_BRANCH#remotes/origin/}

echo "$CODEBUILD_GIT_BRANCH"
pwd


buildCommand="/work/cli build"
if [ "${CODEBUILD_GIT_BRANCH}" == "main" ]; then
    buildCommand="${buildCommand} --publish --send-notifications"
fi

echo "Running build command: ${buildCommand}"
$buildCommand
