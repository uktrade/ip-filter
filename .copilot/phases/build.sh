#!/usr/bin/env bash

# Exit early if something goes wrong
set -e

# Add commands below to run as part of the build phase
CODEBUILD_GIT_BRANCH=`git branch -a --contains HEAD | sed -n 2p | awk '{ printf $1 }'`
CODEBUILD_GIT_BRANCH=${CODEBUILD_GIT_BRANCH#remotes/origin/}

echo ">>>>> BUILD PHASE DEBUG <<<<<"
echo "$CODEBUILD_GIT_BRANCH"
echo ">> PWD <<"
pwd
ls -al
cat .git
echo
echo ">> LOCAL-CACHE DIR <<"
ls -al /codebuild/local-cache/workspace/ad88d84512be7140d005045a32fe3dc291db492c9644c95fe897bc60e5e683dd/
cat .git
echo
cat .copilot/config.yml
#export GIT_DIR=$PWD
echo
env
#echo
#export GIT_WORK_TREE=.
#rm -rf /codebuild/local-cache/workspace/ad88d84512be7140d005045a32fe3dc291db492c9644c95fe897bc60e5e683dd/
#ECR_REPOSITORY=
#export ADDITIONAL_ECR_REPOSITORY=public.ecr.aws/uktrade/ip-filter
echo "RUNNING CI IMAGE BUILDER COMMANDS"
echo "rev-parse short"
git rev-parse --short HEAD
echo
echo "rev-parse long"
git rev-parse HEAD
echo
echo ">>>>> END BUILD PHASE DEBUG <<<<<"

buildCommand="/work/cli build"
if [ "${CODEBUILD_GIT_BRANCH}" == "main" ]; then
    buildCommand="${buildCommand} --publish --send-notifications"
fi

echo "Running build command: ${buildCommand}"
$buildCommand
