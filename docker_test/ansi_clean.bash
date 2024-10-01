#!/bin/bash

ansi_clean () {
  # This function is separate so nobody touches the control-M sequence 
  # in the second sed stream filter
  echo ${1} | sed -e 's/\x1b\[[0-9;]*m//g' -e 's///g'
}
