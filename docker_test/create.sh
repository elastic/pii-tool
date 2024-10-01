#!/bin/bash


# Source the common.bash file from the same path as the script
source $(dirname "$0")/common.bash

echo
NODECOUNT=1
ROLES="\"data\", \"data_content\", \"data_hot\", \"data_warm\", \"data_cold\", \"master\", \"ingest\""

# Test to see if we were passed a VERSION
if [ "x${1}" == "x" ]; then
  echo "Error! No Elasticsearch version provided."
  echo "VERSION must be in Semver format, e.g. X.Y.Z, 8.6.0"
  echo "USAGE: ${0} VERSION [SCENARIO]" 
  exit 1
fi

# Test to see if we were passed another argument
if [ "x${2}" != "x" ]; then
  source ${SCRIPTPATH}/scenarios.bash
  echo "Using scenario: ${2}"
fi

# Set the version
VERSION=${1}
add2envfiles ES_VERSION ${VERSION}

# Start console output
echo "Using Elasticsearch version ${VERSION}"
echo

######################################
### Setup snapshot repository path ###
######################################

# Nuke it from orbit, just to be sure
rm -rf ${REPOLOCAL}
mkdir -p ${REPOLOCAL}

#####################
### Run Container ###
#####################

docker network rm -f ${TRUNK}-net > /dev/null 2>&1
docker network create ${TRUNK}-net > /dev/null 2>&1


# Start the container
echo "Creating ${NODECOUNT} node(s)..."
echo 

echo "Starting node 1..."
start_container "1"

# Set the URL
URL=https://${URL_HOST}:${LOCAL_PORT}

add2envfiles CA_CRT "${PROJECT_ROOT}/http_ca.crt"
add2envfiles TEST_PATH ${TESTPATH}
add2envfiles TEST_ES_SERVER ${URL}
add2envfiles TEST_ES_REPO ${REPONAME}

add2envfiles ESCLIENT_CA_CERTS ${CACRT}
add2envfiles ESCLIENT_HOSTS ${URL}

# Set up the curl config file, first line creates a new file, all others append
echo "-o /dev/null" > ${CURLCFG}
echo "-s" >> ${CURLCFG}
echo '-w "%{http_code}\n"' >> ${CURLCFG}

# Do the xpack_fork function, passing the container name and the .env file path
xpack_fork "${NAME}"
echo

# Did we get a bad return code?
if [ $? -eq 1 ]; then

  # That's an error, and we need to exit
  echo "ERROR! Unable to get/reset elastic user password. Unable to continue. Exiting..."
  exit 1
fi

# Initialize trial license
response=$(curl -s \
  --cacert ${CACRT} -u "${ESUSR}:${ESPWD}" \
  -XPOST "${URL}/_license/start_trial?acknowledge=true")

expected='{"acknowledged":true,"trial_was_started":true,"type":"trial"}'
if [ "$response" != "$expected" ]; then
  echo "ERROR! Unable to start trial license!"
  exit 1
else
  echo "Trial license started..."
fi

# Set up snapshot repository. The following will create a JSON file suitable for use with
# curl -d @filename

rm -f ${REPOJSON}  

# Build a pretty JSON object defining the repository settings
echo    '{'                    >> $REPOJSON
echo    '  "type": "fs",'      >> $REPOJSON
echo    '  "settings": {'      >> $REPOJSON
echo -n '    "location": "'    >> $REPOJSON
echo -n "${REPODOCKER}"        >> $REPOJSON
echo    '"'                    >> $REPOJSON
echo    '  }'                  >> $REPOJSON
echo    '}'                    >> $REPOJSON

# Create snapshot repository
response=$(curl -s \
  --cacert ${CACRT} -u "${ESUSR}:${ESPWD}" \
  -H 'Content-Type: application/json' \
  -XPOST "${URL}/_snapshot/${REPONAME}?verify=false" \
  --json \@${REPOJSON})

expected='{"acknowledged":true}'
if [ "$response" != "$expected" ]; then
  echo "ERROR! Unable to create snapshot repository"
  exit 1
else
  echo "Snapshot repository initialized..."
  rm -f ${REPOJSON}
fi

echo
echo "Node 1 started."

if [ ${NODECOUNT} -gt 1 ]; then
  for i in $(seq 2 ${NODECOUNT}); do
    CAPTURE=$(start_container "${i}" "${NODETOKEN}" "${ROLES}")
    testport=$(( 9200 + ${i} -1 ))
    CAPTURE=$(check_url "https://${URL_HOST}:${testport}")
    echo "Node ${i} started."
  done
fi

##################
### Wrap it up ###
##################

echo
echo "All nodes ready to test!"
echo

if [ "$EXECPATH" == "$PROJECT_ROOT" ]; then
  echo "Environment variables are in .env[-shell]"
elif [ "$EXECPATH" == "$SCRIPTPATH" ]; then
  echo "\$PWD is $SCRIPTPATH."
  echo "Environment variables are in ../.env[-shell]"
else
  echo "Environment variables are in ${PROJECT_ROOT}/.env[-shell]"
fi
