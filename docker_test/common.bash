# Common variables and functions

# Source the common.bash file from the same path as the script
source $(dirname "$0")/ansi_clean.bash

#MANUAL_PROJECT_NAME=project_name
DOCKER_PORT=9200
LOCAL_PORT=9200
URL_HOST=127.0.0.1
ESUSR=elastic
ENVFILE=.env
SHELLENVFILE=.env-shell
CURLFILE=.kurl
REPODOCKER=/media
REPOJSON=createrepo.json
REPONAME=testing
LIMIT=30  # How many seconds to wait to obtain the credentials
IMAGE=docker.elastic.co/elasticsearch/elasticsearch
MEMORY=1GB  # The heap will be half of this
JAVA_OPTS="-Xms512m -Xmx512m"
CLUSTER_NAME="docker_test"
DISCOVERY_TYPE="single-node"


#############################
### Function declarations ###
#############################

docker_logline () {
  # Return the line number that contains "${1}"
  echo $(docker logs ${NAME} | grep -n "${1}" | awk -F\: '{print $1}')
}

initial_value () {
  # $1 is the value representation
  local searchstr=''

  case ${1} in
    
    'password') 
      searchstr='elasticsearch-reset-password'
      ;;

    'nodetoken') 
      searchstr='\--enrollment-token'
      ;;

  esac

  local linenum=$(docker_logline ${searchstr})

  # Increment the linenum (because we want the next line)
  ((++linenum))

  # Get the (next) line, i.e. incremented and tailed to isolate
  retval=$(docker logs ${NAME} | head -n ${linenum} | tail -1 | awk '{print $1}')

  # Strip the ANSI color/bold here. External function because of the control-M sequence
  ansi_clean "${retval}"
}

add2envfiles () {
  # $1 is the env var name
  # $2 is the value
  echo "${1}=\"${2}\"" >> ${ENVCFG}
  echo "export ${1}=\"${2}\"" >> ${SHELLENVCFG}
}

get_espw () {
  # Start with an empty value
  linenum=''

  # Make a pretty spinner
  spin='-\|/'
  # spin modulo tracker
  s=0
  # tenths incrementer (of a second)
  tenths=0
  # tenths modulo tracker
  t=0
  # seconds incrementer
  seconds=0

  # Loop until we get a valid line number, or LIMIT tries
  while [ "x${linenum}" == "x" ] && [ $seconds -lt $LIMIT ]; do

    # increment $s and modulo 4
    s=$(( (s+1) %4 ))
    # increment $tenths
    ((++tenths))
    # increment $t and modulo 10
    t=$(( (t+1) %10 ))

    # if $t is 0 (it was evenly divisible by 10)
    if [ $t -eq 0 ]; then
      # we increment seconds, because 1 second has elapsed
      ((++seconds))
      # Get the docker log line associated with elasticsearch-reset-password
      linenum=$(docker_logline "elasticsearch-reset-password")
    fi

    # Print the spinner to stderr (so it shows up)
    printf "\r${spin:$s:1} ${seconds}s elapsed (typically 15s - 25s)..." >&2

    # wait 1/10th of a second before looping again
    sleep 0.1
  done
  # end while loop

  # Error out if we didn't get it
  if [ "x${linenum}" == "x" ] || [ $seconds -ge $LIMIT ]; then
    echo "ERROR: Unable to get password for user ${ESUSR}. Unable to continue. Exiting..."
    exit 1
  fi
  
  ESPWD=$(initial_value 'password')
  NODETOKEN=$(initial_value 'nodetoken')
}

change_espw () {
  
  # To shorten the command-line, we put this as a variable
  exec_cmd=/usr/share/elasticsearch/bin/elasticsearch-reset-password

  #################################################
  # The change password command:                  #
  # docker exec -it ${1} ${exec_cmd} -b -u $ESUSR #
  #################################################
  #############################################################################
  # Output 1: Not ready response:                                             #
  # ERROR: Failed to determine the health of the cluster. , with exit code 69 #
  #############################################################################
  #######################################################
  # Output 2: Successful response:                      #
  # Password for the [elastic] user successfully reset. #
  # New value: NEW_PASSWORD                             #
  #######################################################

  # awk '{print $3}' of the "Not ready response" is "to"
  # So we start with retval='to'
  retval='to'

  # We're only going to try this to the $LIMIT
  count=0

  # Loop until we get the expected response, or LIMIT tries
  while [ "x$retval" == "xto" ] && [ $count -lt $LIMIT ]; do
    retval=$(docker exec -it ${NAME} $exec_cmd -b -u ${ESUSR} | tail -1 | awk '{print $3}')
    ((++count))
    sleep 1
  done
  
  # If we still don't have a value, send an empty reponse back, rather than "to"
  if [ "x${retval}" == "xto" ]; then
    echo '' 
  else
    echo ${retval}
  fi
}

xpack_fork () {

  echo "Getting Elasticsearch credentials from container ${NAME}..."

  # Get the password from the get_espw function. It sets ESPWD
  get_espw 

  # If we have an empty value, that's a problem
  if [ "x${ESPWD}" == "x" ]; then
    echo "ERROR: Unable to get password for user ${ESUSR}. Unable to continue. Exiting..."
    exit 1
  fi

  # Add variables to .env files
  add2envfiles ESCLIENT_USERNAME ${ESUSR}
  add2envfiles TEST_USER ${ESUSR}

  # We escape the quotes so we can include them in case of special characters
  add2envfiles ESCLIENT_PASSWORD ${ESPWD}
  add2envfiles TEST_PASS ${ESPWD}


  # Get the CA certificate and copy it to the PROJECT_ROOT
  docker cp -q ${NAME}:/usr/share/elasticsearch/config/certs/http_ca.crt ${PROJECT_ROOT}

  # Put the credentials into ${CURLCFG}
  echo "-u ${ESUSR}:${ESPWD}" >> ${CURLCFG}
  echo "--cacert ${CACRT}" >> ${CURLCFG}

  # Complete
  # echo "Credentials captured!"
}

# Save original execution path
EXECPATH=$(pwd)

# Extract the path for the script
SCRIPTPATH="$(cd -- "$(dirname "$0")" >/dev/null 2>&1 ; pwd -P)"

# Ensure we are in the script path
cd ${SCRIPTPATH}

# Get the directory name
SCRIPTPATH_NAME=$(pwd | awk -F\/ '{print $NF}')

# Go up a level
cd ../

# Find out what the last part of this directory is called
PROJECT_NAME=$(pwd | awk -F\/ '{print $NF}')

# Manually override the project name, if specified
if [ "x${MANUAL_PROJECT_NAME}" != "x" ]; then
  PROJECT_NAME=${MANUAL_PROJECT_NAME}
fi

# We should be at the project root dir now
PROJECT_ROOT=$(pwd)

if [ "${SCRIPTPATH_NAME}" != "docker_test" ]; then
  echo "$0 is not in parent directory 'docker_test'"
  echo "This could cause issues as that is expected."
  echo "PROJECT_ROOT is now set to ${SCRIPTPATH}"
  echo "You may want to set MANUAL_PROJECT_NAME in common.bash"
  PROJECT_ROOT=${SCRIPTPATH}
fi

# If we have a tests/integration path, then we'll use that
if [ -d "tests/integration" ]; then
  TESTPATH=${PROJECT_ROOT}/tests/integration
else
  # Otherwise we will just dump it into the $SCRIPTPATH
  TESTPATH=${SCRIPTPATH}
fi

# Set the CACRT var
CACRT=${PROJECT_ROOT}/http_ca.crt

# Set the .env file
ENVCFG=${PROJECT_ROOT}/${ENVFILE}
SHELLENVCFG=${PROJECT_ROOT}/${SHELLENVFILE}

# Set the curl config file and ensure we're not reusing an old one
CURLCFG=${SCRIPTPATH}/${CURLFILE}

# Determine local IPs
OS=$(uname -a | awk '{print $1}')
if [[ "$OS" = "Linux" ]]; then
  IPLIST=$(ip -4 -o addr show scope global | grep -v docker |awk '{gsub(/\/.*/,"",$4); print $4}')
elif [[ "$OS" = "Darwin" ]]; then
  IPLIST=$(ifconfig | awk -F "[: ]+" '/inet / { if ($2 != "127.0.0.1") print $2 }')
else
  echo "Could not determine local IPs for assigning environment variables..."
  echo "Please manually determine your local non-loopback IP address and assign it,"
  echo "e.g. TEST_ES_SERVER=https://A.B.C.D:${LOCAL_PORT}"
  exit 0
fi

#######################
### Set Docker vars ###
#######################

# Set the TRUNK
TRUNK=${PROJECT_NAME}-test

# Set the Docker container number
NUM=0

# Set the Docker container name
NAME=${TRUNK}-${NUM}

# Set the bind mount path for the snapshot repository
REPOLOCAL=${SCRIPTPATH}/repo

# Navigate back to the script path
cd ${SCRIPTPATH}

############################
### URL CHECKER FUNCTION ###
############################

check_url () {

  # URL = $1
  # EXPECTED = $2

  if [ "x${EXPECTED}" == "x" ]; then
    local EXPECTED=200
  else
    local EXPECTED=${2}
  fi

  # Start with an empty value
  local ACTUAL=0

  # Initialize loop counter
  local COUNTER=0

  # Loop until we get our 200 code
  while [ "${ACTUAL}" != "${EXPECTED}" ] && [ ${COUNTER} -lt ${LIMIT} ]; do

    # Get our actual response
    ACTUAL=$(curl -K ${CURLCFG} ${1})

    # If we got what we expected, we're great!
    if [ "${ACTUAL}" != "${EXPECTED}" ]; then
      # Sleep and try again
      sleep 1
      ((++COUNTER))
    fi

  done
  # End while loop

  # If we still don't have what we expected, we hit the LIMIT
  if [ "${ACTUAL}" != "${EXPECTED}" ]; then

    echo "Unable to connect to ${1} in ${LIMIT} seconds. Unable to continue. Exiting..."
    exit 1
  fi

  echo ${ACTUAL}
}

################################
### Start a Docker container ###
################################

start_container () {
  # $1 node_number
  # $2 enrollment_token
  # $3 node roles

  if [ "x${2}" == "x" ]; then
    local token=''
  else
    local token=${2}
  fi

  if [ "x${3}" == "x" ]; then
    local roles="'data', 'data_cold', 'data_content', 'data_hot', 'data_warm', 'ingest', 'master'"
  else
    local roles="${3}"
  fi

  local fromzero=$(( ${1} - 1 ))
  local nodename="${TRUNK}-${fromzero}"
  local portnum=$(( 9200 + ${fromzero} ))

echo -en "Container ID: "
docker run -q -d -it \
  --name ${nodename} \
  --network ${TRUNK}-net \
  -m ${MEMORY} \
  -p ${portnum}:${DOCKER_PORT} \
  -v ${REPOLOCAL}:${REPODOCKER} \
  -e ENROLLMENT_TOKEN="${token}" \
  -e ES_JAVA_OPTS="${JAVA_OPTS}" \
  -e "discovery.type=${DISCOVERY_TYPE}" \
  -e "cluster.name=${CLUSTER_NAME}" \
  -e "node.name=node-${fromzero}" \
  -e "node.roles=[${roles}]" \
  -e "xpack.searchable.snapshot.shared_cache.size=100MB" \
  -e "xpack.monitoring.templates.enabled=false" \
  -e "path.repo=${REPODOCKER}" \
${IMAGE}:${VERSION}

}

##################
### END COMMON ###
##################
