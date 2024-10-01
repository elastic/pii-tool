# Docker environment setup scenarios

case ${2} in

  'frozen_node')
    NODECOUNT=2
    DISCOVERY_TYPE="multi-node"
    ROLES="data_frozen"
    ;;

  *)
    echo "Error! SCENARIO not recognized."
    echo "USAGE: ${0} VERSION [SCENARIO]"
    echo "You entered: ${2}"
    exit 1
    ;;

esac
