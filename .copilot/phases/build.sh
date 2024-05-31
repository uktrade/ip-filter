#!/usr/bin/env bash

# Exit early if something goes wrong
set -e

# Add commands below to run as part of the build phase
CODEBUILD_GIT_BRANCH=`git branch -a --contains HEAD | sed -n 2p | awk '{ printf $1 }'`
CODEBUILD_GIT_BRANCH=${CODEBUILD_GIT_BRANCH#remotes/origin/}

#echo "$CODEBUILD_GIT_BRANCH"
#pwd
#ls -al
#cat .copilot/config.yml
#cat .git
#echo
#export GIT_DIR=.
#export GIT_WORK_TREE=.
#rm -rf /codebuild/local-cache/workspace/ad88d84512be7140d005045a32fe3dc291db492c9644c95fe897bc60e5e683dd/
#ECR_REPOSITORY=
ADDITIONAL_ECR_REPOSITORY="public.ecr.aws/uktrade/ip-filter"

buildCommand="/work/cli build"
if [ "${CODEBUILD_GIT_BRANCH}" == "main" ]; then
    buildCommand="${buildCommand} --publish --send-notifications"
fi

echo "Running build command: ${buildCommand}"
$buildCommand
