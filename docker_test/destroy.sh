#!/bin/bash

if [ "x${1}" != "verbose" ]; then
  DEBUG=0
else
  DEBUG=1
fi

# Source the common.bash file from the same path as the script
source $(dirname "$0")/common.bash

log_out () {
  # $1 is the log line
  if [ ${DEBUG} -eq 1 ]; then
    echo ${1}
  fi
}

# Stop running containers
echo
echo "Stopping all containers..."
RUNNING=$(docker ps | grep -v NAMES | grep ${TRUNK} | awk '{print $NF}')
for container in ${RUNNING}; do
  log_out "Stopping container ${container}..."
  log_out "$(docker stop ${container}) stopped."
done

# Remove existing containers
echo "Removing all containers..."
EXISTS=$(docker ps -a | grep -v NAMES | grep ${TRUNK} | awk '{print $NF}')
for container in ${EXISTS}; do
  log_out "Removing container ${container}..."
  log_out "$(docker rm -f ${container}) deleted."
done

# Delete Docker network
docker network rm -f ${TRUNK}-net > /dev/null 2>&1

# Delete .env[-shell] file(s) and curl config file
echo "Deleting remaining files and directories"
rm -rf ${REPOLOCAL}
rm -f ${ENVCFG} ${SHELLENVCFG}
rm -f ${CURLCFG}
rm -f ${PROJECT_ROOT}/http_ca.crt

echo "Cleanup complete."
